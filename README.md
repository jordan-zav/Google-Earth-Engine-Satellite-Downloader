# Google Earth Engine Satellite Downloader

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Google Earth Engine](https://img.shields.io/badge/Google%20Earth%20Engine-supported-brightgreen.svg)](https://earthengine.google.com/)

## Objetivo

Esta carpeta contiene dos scripts reutilizables para descargar mosaicos e índices desde Google Earth Engine usando cualquier shapefile como área de trabajo.

Los scripts aplican el mismo flujo depurado:

1. Leer el shapefile.
2. Reproyectar la geometría a WGS84.
3. Crear una grilla de teselas de 0.2 grados.
4. Descargar cada tesela como GeoTIFF local.
5. Detectar teselas que no se descargan por error de Earth Engine o timeout.
6. Partir cada tesela fallida en 4 subpartes.
7. Descargar esas 4 subpartes.
8. Unir las 4 subpartes en un GeoTIFF final Float32.
9. Validar CRS, número de bandas y tipo de dato.
10. Guardar un resumen técnico en summary.json.
11. Opcionalmente unir todas las teselas de cada sensor en un mosaico único.

## Scripts incluidos

download_landsat_shapefile.py

Descarga Landsat 8 y Landsat 9 Collection 2 Level 2 Surface Reflectance.

Bandas finales:

1. SR_B2 azul
2. SR_B3 verde
3. SR_B4 rojo
4. SR_B5 NIR
5. SR_B6 SWIR1
6. SR_B7 SWIR2
7. iron_oxide_red_blue
8. clay_hydroxyl_swir1_swir2
9. ferrous_silicate_swir1_nir
10. ndvi
11. ndwi

download_aster_shapefile.py

Descarga ASTER AST_L1T_003 para el periodo anterior a abril de 2008.

Bandas finales:

1. B01
2. B02
3. B3N
4. B04
5. B05
6. B06
7. B07
8. B08
9. B09
10. ferric_iron_b2_b1
11. aloh_proxy_b5_b7_b6
12. mgoh_carbonate_proxy_b7_b9_b8
13. clay_relative_absorption_b6

merge_tiles_by_sensor.py

Une teselas GeoTIFF ya descargadas por carpeta de sensor. Detecta automáticamente carpetas con archivos x000_y000.tif, crea un mosaico Float32 y guarda el resultado en la misma carpeta.

## Instalación y autenticación

Instalar dependencias:

pip install -r requirements.txt

Autenticarse en Google Earth Engine:

earthengine authenticate

Si se usa un proyecto específico de Google Cloud, pasar el identificador con --project-id.

Ejemplo:

python descarga_por_shapefile/download_landsat_shapefile.py --shapefile data/area.shp --name study_area --project-id your-project-id

## Comandos de uso

Ejemplo para Landsat:

python descarga_por_shapefile/download_landsat_shapefile.py --shapefile data/area.shp --name study_area

Ejemplo para ASTER:

python descarga_por_shapefile/download_aster_shapefile.py --shapefile data/area.shp --name study_area

Prueba sin descargar:

python descarga_por_shapefile/download_landsat_shapefile.py --shapefile data/area.shp --name study_area --dry-run

python descarga_por_shapefile/download_aster_shapefile.py --shapefile data/area.shp --name study_area --dry-run

Descargar solo pocas teselas para prueba:

python descarga_por_shapefile/download_landsat_shapefile.py --shapefile data/area.shp --name study_area --limit-tiles 2

python descarga_por_shapefile/download_aster_shapefile.py --shapefile data/area.shp --name study_area --limit-tiles 2

Unir teselas por sensor:

python descarga_por_shapefile/merge_tiles_by_sensor.py --root outputs/by_shapefile

Revisar qué carpetas se unirían sin crear mosaicos:

python descarga_por_shapefile/merge_tiles_by_sensor.py --root outputs/by_shapefile --dry-run

Regenerar mosaicos existentes:

python descarga_por_shapefile/merge_tiles_by_sensor.py --root outputs/by_shapefile --overwrite

## Parámetros principales

--shapefile

Ruta del shapefile que define el área de descarga.

--name

Nombre normalizado para carpetas y archivos de salida. Si no se indica, se usa el nombre del shapefile.

--output-dir

Carpeta base de salida. Por defecto usa outputs/by_shapefile.

--tile-degrees

Tamaño de la tesela en grados. El valor por defecto es 0.2.

--split

Número de divisiones por lado para teselas fallidas. El valor por defecto es 2, lo que produce 4 subpartes.

--scale

Resolución de descarga en metros. El valor por defecto es 30.

--dry-run

Muestra el plan de descarga sin descargar archivos.

--limit-tiles

Limita la cantidad de teselas procesadas. Sirve para probar antes de lanzar una descarga grande.

--overwrite

En merge_tiles_by_sensor.py, permite reemplazar un mosaico existente.

--show-gdal-warnings

En merge_tiles_by_sensor.py, muestra advertencias detalladas de GDAL. Por defecto se silencian para evitar ruido en consola.

## Estructura de salida

outputs/by_shapefile/NOMBRE/PRODUCTO/final_tiles

Contiene los GeoTIFF finales listos para cargar en QGIS.

outputs/by_shapefile/NOMBRE/PRODUCTO/split_parts

Contiene las subpartes usadas para reconstruir teselas que fallaron en descarga directa.

outputs/by_shapefile/NOMBRE/PRODUCTO/logs

Contiene summary.json con el resumen de ejecución.

outputs/by_shapefile/NOMBRE/PRODUCTO/final_tiles/NOMBRE_PRODUCTO_mosaic.tif

Mosaico final generado por merge_tiles_by_sensor.py.

## Nomenclatura normalizada

Landsat:

NOMBRE_landsat_oli_sr_indices_x000_y000.tif

ASTER:

NOMBRE_aster_x000_y000.tif

Subpartes de reintento:

NOMBRE_producto_x000_y000_p00_00.tif

Las subpartes no son el producto final. El producto final siempre queda en final_tiles con el nombre normalizado sin sufijo de subparte.

## Reanudación

Los scripts son reanudables. Si una tesela final ya existe y pesa más de cero bytes, se salta automáticamente.

Esto permite detener y volver a ejecutar sin perder lo ya descargado.

## Validación

Al final se valida:

CRS igual a EPSG:4326.

Tipo de dato igual a Float32.

Número de bandas correcto.

Landsat debe tener 11 bandas.

ASTER debe tener 13 bandas.

Si hay problemas, el script se detiene con error y deja el detalle en summary.json.

## Nota operativa

Para procesos que duren más de 5 minutos, avisar antes de iniciar y monitorear cada 5 minutos. Las descargas completas de shapefiles grandes pueden tardar varios minutos u horas según Earth Engine, la red y el tamaño del área.

## Citación

Si usas este repositorio en un proyecto académico o técnico, cita el software usando el archivo CITATION.cff incluido en el repositorio.
