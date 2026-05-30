import argparse
import json
import math
import zipfile
from pathlib import Path

import ee
import geopandas as gpd
import rasterio
import requests
from rasterio.merge import merge
from shapely.geometry import box


PROJECT_ID = "ngea-2027"
PRODUCT_NAME = "aster"
EXPECTED_BANDS = 13


def initialize_ee(project_id: str) -> None:
    ee.Initialize(project=project_id)


def normalize_name(value: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in value)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "area"


def load_local_geometry(shapefile_path: Path):
    gdf = gpd.read_file(shapefile_path).to_crs("EPSG:4326")
    return gdf.geometry.union_all()


def ee_geometry_from_local(local_geom) -> ee.Geometry:
    geojson = json.loads(gpd.GeoSeries([local_geom], crs="EPSG:4326").to_json())
    return ee.Geometry(geojson["features"][0]["geometry"])


def mask_aster_valid(image: ee.Image) -> ee.Image:
    bands = image.select(["B01", "B02", "B3N", "B04", "B05", "B06", "B07", "B08", "B09"])
    return image.updateMask(bands.reduce(ee.Reducer.min()).gt(0))


def build_image(region: ee.Geometry, start: str, end: str) -> ee.Image:
    collection = (
        ee.ImageCollection("ASTER/AST_L1T_003")
        .filterBounds(region)
        .filterDate(start, end)
        .map(mask_aster_valid)
    )
    mosaic = collection.median()

    b1 = mosaic.select("B01")
    b2 = mosaic.select("B02")
    b5 = mosaic.select("B05")
    b6 = mosaic.select("B06")
    b7 = mosaic.select("B07")
    b8 = mosaic.select("B08")
    b9 = mosaic.select("B09")

    indices = [
        b2.divide(b1).rename("ferric_iron_b2_b1"),
        b5.add(b7).divide(b6.multiply(2)).rename("aloh_proxy_b5_b7_b6"),
        b7.add(b9).divide(b8.multiply(2)).rename("mgoh_carbonate_proxy_b7_b9_b8"),
        b5.multiply(b7).sqrt().divide(b6).rename("clay_relative_absorption_b6"),
    ]
    return mosaic.select(["B01", "B02", "B3N", "B04", "B05", "B06", "B07", "B08", "B09"]).addBands(indices)


def iter_tiles(local_geom, tile_degrees: float):
    minx, miny, maxx, maxy = local_geom.bounds
    x_count = math.ceil((maxx - minx) / tile_degrees)
    y_count = math.ceil((maxy - miny) / tile_degrees)
    for x_index in range(x_count):
        for y_index in range(y_count):
            x1 = minx + x_index * tile_degrees
            y1 = miny + y_index * tile_degrees
            x2 = min(x1 + tile_degrees, maxx)
            y2 = min(y1 + tile_degrees, maxy)
            tile = box(x1, y1, x2, y2)
            if tile.intersects(local_geom):
                yield x_index, y_index, tile


def split_geometry(tile_geom, split: int):
    minx, miny, maxx, maxy = tile_geom.bounds
    width = (maxx - minx) / split
    height = (maxy - miny) / split
    for x_part in range(split):
        for y_part in range(split):
            part = box(
                minx + x_part * width,
                miny + y_part * height,
                minx + (x_part + 1) * width,
                miny + (y_part + 1) * height,
            ).intersection(tile_geom)
            if not part.is_empty:
                yield x_part, y_part, part


def download_tile(image: ee.Image, tile_geom, output_path: Path, scale: int, timeout: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        print(json.dumps({"skip_existing": str(output_path)}, ensure_ascii=False), flush=True)
        return

    region = ee_geometry_from_local(tile_geom)
    url = image.clip(region).toFloat().getDownloadURL(
        {"region": region, "scale": scale, "crs": "EPSG:4326", "format": "GEO_TIFF"}
    )
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()

    temp_path = output_path.with_suffix(".download")
    temp_path.write_bytes(response.content)
    if zipfile.is_zipfile(temp_path):
        with zipfile.ZipFile(temp_path) as archive:
            tif_names = [name for name in archive.namelist() if name.lower().endswith((".tif", ".tiff"))]
            if len(tif_names) != 1:
                raise RuntimeError(f"Expected one GeoTIFF in download, found {len(tif_names)}")
            with archive.open(tif_names[0]) as src, output_path.open("wb") as dst:
                dst.write(src.read())
        temp_path.unlink()
    else:
        temp_path.replace(output_path)

    print(json.dumps({"downloaded": str(output_path), "bytes": output_path.stat().st_size}, ensure_ascii=False), flush=True)


def merge_parts(part_paths: list[Path], output_path: Path) -> None:
    datasets = [rasterio.open(path) for path in part_paths]
    try:
        merged, transform = merge(datasets)
        profile = datasets[0].profile.copy()
        profile.update(
            {
                "driver": "GTiff",
                "height": merged.shape[1],
                "width": merged.shape[2],
                "transform": transform,
                "count": merged.shape[0],
                "dtype": "float32",
                "compress": "lzw",
                "tiled": True,
                "BIGTIFF": "IF_SAFER",
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(merged.astype("float32"))
    finally:
        for dataset in datasets:
            dataset.close()

    print(json.dumps({"merged": str(output_path), "parts": len(part_paths), "bytes": output_path.stat().st_size}, ensure_ascii=False), flush=True)


def validate_raster(path: Path) -> dict:
    with rasterio.open(path) as src:
        return {
            "path": str(path),
            "bands": src.count,
            "dtype": src.dtypes[0],
            "crs": str(src.crs),
            "valid": src.count == EXPECTED_BANDS and src.dtypes[0] == "float32" and str(src.crs) == "EPSG:4326",
        }


def run(args) -> None:
    shapefile_path = Path(args.shapefile)
    area_name = normalize_name(args.name or shapefile_path.stem)
    local_geom = load_local_geometry(shapefile_path)
    region = ee_geometry_from_local(local_geom)
    image = build_image(region, args.start, args.end)

    base_dir = Path(args.output_dir) / area_name / PRODUCT_NAME
    tile_dir = base_dir / "final_tiles"
    part_dir = base_dir / "split_parts"
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    tiles = list(iter_tiles(local_geom, args.tile_degrees))
    if args.limit_tiles:
        tiles = tiles[: args.limit_tiles]

    print(
        json.dumps(
            {
                "area": area_name,
                "product": PRODUCT_NAME,
                "shapefile": str(shapefile_path),
                "tiles": len(tiles),
                "tile_degrees": args.tile_degrees,
                "scale": args.scale,
                "output": str(tile_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    if args.dry_run:
        return

    failed = []
    for x_index, y_index, tile_geom in tiles:
        output_path = tile_dir / f"{area_name}_{PRODUCT_NAME}_x{x_index:03d}_y{y_index:03d}.tif"
        try:
            download_tile(image, tile_geom, output_path, args.scale, args.timeout)
        except Exception as exc:
            failed.append({"x": x_index, "y": y_index, "error": str(exc)})
            print(json.dumps({"failed_direct": str(output_path), "error": str(exc)}, ensure_ascii=False), flush=True)

    retry_failed = []
    for failure in failed:
        x_index = failure["x"]
        y_index = failure["y"]
        tile_geom = next(tile for tx, ty, tile in tiles if tx == x_index and ty == y_index)
        output_path = tile_dir / f"{area_name}_{PRODUCT_NAME}_x{x_index:03d}_y{y_index:03d}.tif"
        part_paths = []
        for x_part, y_part, part_geom in split_geometry(tile_geom, args.split):
            part_path = part_dir / f"{area_name}_{PRODUCT_NAME}_x{x_index:03d}_y{y_index:03d}_p{x_part:02d}_{y_part:02d}.tif"
            try:
                download_tile(image, part_geom, part_path, args.scale, args.timeout)
                part_paths.append(part_path)
            except Exception as exc:
                retry_failed.append({"x": x_index, "y": y_index, "part": f"p{x_part:02d}_{y_part:02d}", "error": str(exc)})
                print(json.dumps({"failed_part": str(part_path), "error": str(exc)}, ensure_ascii=False), flush=True)
        if len(part_paths) == args.split * args.split:
            merge_parts(part_paths, output_path)

    final_tiles = sorted(tile_dir.glob(f"{area_name}_{PRODUCT_NAME}_x*_y*.tif"))
    validation = [validate_raster(path) for path in final_tiles]
    summary = {
        "area": area_name,
        "product": PRODUCT_NAME,
        "expected_tiles": len(tiles),
        "final_tiles": len(final_tiles),
        "direct_failures": failed,
        "retry_failures": retry_failed,
        "invalid_outputs": [item for item in validation if not item["valid"]],
    }
    (log_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)

    if retry_failed or len(final_tiles) != len(tiles) or summary["invalid_outputs"]:
        raise RuntimeError(f"Finished with unresolved ASTER problems. See {log_dir / 'summary.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download normalized ASTER tiles for any shapefile.")
    parser.add_argument("--shapefile", required=True)
    parser.add_argument("--name")
    parser.add_argument("--output-dir", default="outputs/by_shapefile")
    parser.add_argument("--start", default="2000-02-24")
    parser.add_argument("--end", default="2008-04-30")
    parser.add_argument("--tile-degrees", type=float, default=0.2)
    parser.add_argument("--split", type=int, default=2)
    parser.add_argument("--scale", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--limit-tiles", type=int)
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    initialize_ee(args.project_id)
    run(args)


if __name__ == "__main__":
    main()
