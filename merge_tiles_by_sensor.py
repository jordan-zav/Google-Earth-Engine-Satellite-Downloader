# Copyright (c) 2026 Jordan Zavaleta
# This file is part of Google-Earth-Engine-Satellite-Downloader.
# Google-Earth-Engine-Satellite-Downloader is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import argparse
import json
import os
import re
from pathlib import Path

import rasterio
from rasterio.merge import merge


TILE_RE = re.compile(r"_x\d{3}_y\d{3}\.tif$", re.IGNORECASE)


def is_tile(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".tif", ".tiff"} and bool(TILE_RE.search(path.name))


def find_tile_dirs(root: Path) -> list[Path]:
    tile_dirs = set()
    for path in root.rglob("*.tif"):
        if is_tile(path):
            tile_dirs.add(path.parent)
    return sorted(tile_dirs)


def tile_paths(tile_dir: Path) -> list[Path]:
    return sorted(path for path in tile_dir.glob("*.tif") if is_tile(path))


def area_and_sensor(tile_dir: Path) -> tuple[str, str]:
    if tile_dir.name == "final_tiles" and tile_dir.parent.parent != tile_dir.parent:
        return tile_dir.parent.parent.name, tile_dir.parent.name
    if tile_dir.parent != tile_dir:
        return tile_dir.parent.name, tile_dir.name
    return "area", tile_dir.name


def output_path_for(tile_dir: Path) -> Path:
    area_name, sensor_name = area_and_sensor(tile_dir)
    return tile_dir / f"{area_name}_{sensor_name}_mosaic.tif"


def merge_tile_dir(tile_dir: Path, overwrite: bool, quiet_gdal: bool) -> dict:
    tiles = tile_paths(tile_dir)
    if not tiles:
        return {"tile_dir": str(tile_dir), "status": "skipped", "reason": "no input tiles"}

    output_path = output_path_for(tile_dir)
    if output_path.exists() and not overwrite:
        return {
            "tile_dir": str(tile_dir),
            "status": "skipped",
            "reason": "mosaic exists",
            "input_tiles": len(tiles),
            "output": str(output_path),
        }

    env_options = {"CPL_LOG": os.devnull} if quiet_gdal else {}
    with rasterio.Env(**env_options):
        datasets = [rasterio.open(path) for path in tiles]
        try:
            mosaic, transform = merge(datasets)
            profile = datasets[0].profile.copy()
            profile.update(
                {
                    "driver": "GTiff",
                    "height": mosaic.shape[1],
                    "width": mosaic.shape[2],
                    "transform": transform,
                    "count": mosaic.shape[0],
                    "dtype": "float32",
                    "compress": "lzw",
                    "tiled": True,
                    "BIGTIFF": "IF_SAFER",
                }
            )

            temp_path = output_path.with_suffix(".tmp.tif")
            with rasterio.open(temp_path, "w", **profile) as dst:
                dst.write(mosaic.astype("float32"))
            temp_path.replace(output_path)
        finally:
            for dataset in datasets:
                dataset.close()

        with rasterio.open(output_path) as src:
            validation = {
                "bands": src.count,
                "dtype": src.dtypes[0],
                "crs": str(src.crs),
                "width": src.width,
                "height": src.height,
            }

    return {
        "tile_dir": str(tile_dir),
        "status": "merged",
        "input_tiles": len(tiles),
        "output": str(output_path),
        "bytes": output_path.stat().st_size,
        **validation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge GeoTIFF tiles by sensor folder.")
    parser.add_argument("--root", default="outputs/by_shapefile")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-gdal-warnings", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    tile_dirs = find_tile_dirs(root)
    results = []

    for tile_dir in tile_dirs:
        tiles = tile_paths(tile_dir)
        if args.dry_run:
            result = {
                "tile_dir": str(tile_dir),
                "status": "planned",
                "input_tiles": len(tiles),
                "output": str(output_path_for(tile_dir)),
            }
        else:
            result = merge_tile_dir(tile_dir, args.overwrite, quiet_gdal=not args.show_gdal_warnings)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    summary_path = root / "merge_summary.json"
    if not args.dry_run:
        summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summary": str(summary_path)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
