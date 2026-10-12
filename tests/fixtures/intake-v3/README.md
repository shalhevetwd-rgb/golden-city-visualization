All files in this directory are generated synthetic test fixtures. They are not real cadastral data, buildings, camera calibration or survey plans.

plan.pdf: 2 real PDF pages generated using PyMuPDF 1.26.6.
plan.dxf: real ASCII DXF R2010 using ezdxf 1.4.4; $INSUNITS=6 explicitly declares drawing metres, no source CRS.
plan.png/jpg: real decoded image formats, no coordinate reference.
mesh.obj: source planar quad, no declared units.
mesh.glb: glTF 2.0 triangle with actual BIN positions/normals/indices and parented camera.
projected.geojson: explicitly declared EPSG:2039 synthetic square, not a surveyed boundary.
unreferenced.geojson: identical coordinates with the CRS declaration removed, which must remain unreferenced.
