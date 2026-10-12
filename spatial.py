"""Explicit-CRS GeoJSON import and audited planar control-point alignment.

This module never infers cadastral boundaries, geocodes addresses, or certifies
the supplied source. Geographic positions are transformed with PROJ using
longitude/latitude order. Site-plan alignment is a 2-D similarity transform;
terrain elevations and vertical datums are deliberately outside its contract.
"""

from copy import deepcopy
from datetime import date
import hashlib
import json
import math

import numpy as np
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import mapping, shape
from shapely.ops import transform


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def projected_metric_crs(value):
    """Require an explicit projected CRS whose horizontal coordinates are metres."""
    try:
        crs = CRS.from_user_input(value)
    except (CRSError, TypeError, ValueError) as error:
        raise ValueError("An explicit, valid CRS is required") from error
    if not crs.is_projected or len(crs.axis_info) != 2 or any(
        abs(axis.unit_conversion_factor - 1) > 1e-8 for axis in crs.axis_info[:2]
    ):
        raise ValueError("The target CRS must be projected in metres")
    return crs


def polygon_geometry(geojson, label="geometry"):
    """Read exactly one valid 2-D Polygon, without silently repairing its shape."""
    if not isinstance(geojson, dict) or geojson.get("type") != "Polygon":
        raise ValueError(f"{label} must be a GeoJSON Polygon")
    rings = geojson.get("coordinates")
    if not isinstance(rings, (list, tuple)) or not rings:
        raise ValueError(f"{label} requires coordinate rings")
    for ring in rings:
        if not isinstance(ring, (list, tuple)) or len(ring) < 4:
            raise ValueError(f"{label} rings require at least four coordinates")
        for coordinate in ring:
            if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
                raise ValueError(f"{label} requires explicit 2-D coordinates")
            _number(coordinate[0], f"{label} x")
            _number(coordinate[1], f"{label} y")
        if list(ring[0]) != list(ring[-1]):
            raise ValueError(f"{label} rings must be closed")
    try:
        geometry = shape(geojson)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {label}") from error
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise ValueError(f"{label} must have positive area and valid topology")
    return geometry


def _check_coordinates_extent(coordinates, crs):
    """Reject coordinates outside the declared CRS's published area of use."""
    to_geographic = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    extent = crs.area_of_use
    for x, y in coordinates:
        try:
            lon, lat = to_geographic.transform(x, y, errcheck=True)
        except ProjError as error:
            raise ValueError("Coordinates cannot be transformed in the declared CRS") from error
        if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordinates fall outside geographic bounds")
        if extent is not None:
            longitude_valid = extent.west - 1e-7 <= lon <= extent.east + 1e-7
            if extent.west > extent.east:
                longitude_valid = lon >= extent.west - 1e-7 or lon <= extent.east + 1e-7
            if not longitude_valid or not extent.south - 1e-7 <= lat <= extent.north + 1e-7:
                raise ValueError(f"Coordinates are outside the area of use of {crs.to_string()}")


def _check_crs_extent(geometry, crs):
    _check_coordinates_extent((coordinate for ring in (geometry.exterior, *geometry.interiors)
                               for coordinate in ring.coords), crs)


def _source_metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Source metadata is required")
    result = deepcopy(metadata)
    for key in ("provider", "reference", "acquired_at", "source_type"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip() or len(metadata[key]) > 1000:
            raise ValueError(f"Source metadata requires {key}")
    if metadata["source_type"] not in ("synthetic", "user_supplied", "official"):
        raise ValueError("source_type must be synthetic, user_supplied, or official")
    try:
        date.fromisoformat(metadata["acquired_at"][:10])
    except ValueError as error:
        raise ValueError("acquired_at must begin with an ISO date") from error
    # A label such as 'official' describes the supplied provenance; it is not a
    # verification result. Never allow input metadata to overwrite this status.
    result["verification"] = "not_verified"
    return result


def import_boundary(geojson, source_crs, target_crs, source_metadata):
    """Transform a user-supplied Polygon/Feature into a metric project boundary.

    All four arguments are required. ``source_metadata`` contains ``provider``,
    ``reference``, ``acquired_at`` (ISO date), and ``source_type``. This records
    provenance but does not establish survey or cadastral accuracy.
    """
    metadata = _source_metadata(source_metadata)
    try:
        source = CRS.from_user_input(source_crs)
    except (CRSError, TypeError, ValueError) as error:
        raise ValueError("An explicit valid source CRS is required") from error
    if not (source.is_geographic or source.is_projected) or len(source.axis_info) != 2:
        raise ValueError("Only horizontal 2-D geographic or projected source CRSs are supported")
    target = projected_metric_crs(target_crs)
    if not isinstance(geojson, dict):
        raise ValueError("Expected a GeoJSON Polygon or Feature")
    geometry_json = geojson.get("geometry") if geojson.get("type") == "Feature" else geojson
    geometry = polygon_geometry(geometry_json, "boundary")
    if source.is_geographic:
        for ring in (geometry.exterior, *geometry.interiors):
            for longitude, latitude in ring.coords:
                if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                    raise ValueError("Geographic coordinates use longitude/latitude and must be in range")
    _check_crs_extent(geometry, source)
    transformer = Transformer.from_crs(source, target, always_xy=True)
    try:
        projected = transform(lambda x, y: transformer.transform(x, y, errcheck=True), geometry)
    except ProjError as error:
        raise ValueError("Boundary transformation failed") from error
    projected = polygon_geometry(mapping(projected), "transformed boundary")
    _check_crs_extent(projected, target)
    digest = hashlib.sha256(json.dumps(geometry_json, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "boundary": mapping(projected),
        "crs": target.to_string(),
        "source": {**metadata, "crs": source.to_string(), "geometry_sha256": digest},
        "report": {
            "area_m2": projected.area,
            "bounds_m": list(projected.bounds),
            "coordinate_order": "x/y; geographic source is longitude/latitude",
            "transform": "PROJ horizontal CRS transformation",
            "source_verification": "not_verified",
            "survey_accuracy_verified": False,
            "vertical_datum": "not_supplied",
        },
    }


def _point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain two coordinates")
    return [_number(value[0], label), _number(value[1], label)]


def _well_distributed(points, label):
    singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    if singular[0] <= 1e-9 or singular[1] <= singular[0] * 1e-3:
        raise ValueError(f"{label} control points are collinear or too poorly distributed")
    if len({tuple(point) for point in points}) != len(points):
        raise ValueError(f"{label} control points must be distinct")


def align_site_plan(footprints, control_points, target_crs, max_residual=0.25, scale_bounds=(0.1, 10.0)):
    """Fit scale, rotation, and translation without modifying supplied features.

    ``footprints`` is a nonempty list of GeoJSON Features with local 2-D Polygon
    geometry. Attached properties.balconies/slabs footprints use those same local
    horizontal units and are transformed together; elevation/thickness remain
    metres in their existing common vertical datum. Each control pair has
    ``plan_xy`` and projected metre ``target_xy``.
    At least three well-distributed pairs are required. Reflections/shearing are
    not permitted; residuals above ``max_residual`` metres reject the fit. Source
    units must be chosen so the scale is within the explicit ``scale_bounds``.
    """
    target = projected_metric_crs(target_crs)
    tolerance = _number(max_residual, "max_residual")
    if tolerance <= 0:
        raise ValueError("max_residual must be positive")
    if not isinstance(scale_bounds, (list, tuple)) or len(scale_bounds) != 2:
        raise ValueError("scale_bounds must contain minimum and maximum")
    minimum, maximum = (_number(value, "scale bound") for value in scale_bounds)
    if not 0 < minimum <= maximum:
        raise ValueError("scale_bounds must be positive and ordered")
    if not isinstance(control_points, list) or len(control_points) < 3:
        raise ValueError("At least three control-point pairs are required")
    if any(not isinstance(pair, dict) for pair in control_points):
        raise ValueError("Each control-point pair must be an object")
    source_points = np.array([_point(pair.get("plan_xy"), "plan_xy") for pair in control_points])
    target_points = np.array([_point(pair.get("target_xy"), "target_xy") for pair in control_points])
    _check_coordinates_extent(target_points, target)
    _well_distributed(source_points, "Plan")
    _well_distributed(target_points, "Target")
    source_center, target_center = source_points.mean(axis=0), target_points.mean(axis=0)
    source_zero, target_zero = source_points - source_center, target_points - target_center
    denominator = float(np.sum(source_zero * source_zero))
    a = float(np.sum(source_zero[:, 0] * target_zero[:, 0] + source_zero[:, 1] * target_zero[:, 1]) / denominator)
    b = float(np.sum(source_zero[:, 0] * target_zero[:, 1] - source_zero[:, 1] * target_zero[:, 0]) / denominator)
    scale = math.hypot(a, b)
    if not minimum <= scale <= maximum:
        raise ValueError(f"Alignment scale {scale:.6g} falls outside scale_bounds")
    matrix = np.array([[a, -b], [b, a]])
    translation = target_center - matrix @ source_center
    predicted = source_points @ matrix.T + translation
    residuals = np.linalg.norm(predicted - target_points, axis=1)
    if float(max(residuals)) > tolerance:
        raise ValueError(f"Control-point residual {float(max(residuals)):.4f} m exceeds {tolerance:.4f} m")
    if not isinstance(footprints, list) or not footprints:
        raise ValueError("At least one site-plan footprint is required")
    def project_footprint(geojson, label):
        geometry = polygon_geometry(geojson, label)
        projected = transform(lambda x, y: (a * np.asarray(x) - b * np.asarray(y) + translation[0],
                                            b * np.asarray(x) + a * np.asarray(y) + translation[1]), geometry)
        projected = polygon_geometry(mapping(projected), f"aligned {label}")
        _check_crs_extent(projected, target)
        return mapping(projected)

    buildings = []
    for feature in footprints:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("Site-plan footprints must be GeoJSON Features")
        result = deepcopy(feature)
        result["geometry"] = project_footprint(feature.get("geometry"), "site-plan footprint")
        properties = result.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("Site-plan feature properties must be an object")
        for collection_name in ("balconies", "slabs"):
            if collection_name not in properties:
                continue
            records = properties[collection_name]
            if not isinstance(records, list):
                raise ValueError(f"{collection_name} must be a list")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError(f"Each {collection_name} entry must be an object")
                record["footprint"] = project_footprint(record.get("footprint"), f"{collection_name} footprint")
        buildings.append(result)
    return {
        "buildings": buildings,
        "crs": target.to_string(),
        "alignment": {
            "method": "2-D similarity from control-point pairs",
            "scale": scale,
            "rotation_degrees": math.degrees(math.atan2(b, a)),
            "translation": translation.tolist(),
            "residuals_m": residuals.tolist(),
            "max_residual_m": float(max(residuals)),
            "rms_residual_m": float(np.sqrt(np.mean(residuals * residuals))),
            "tolerance_m": tolerance,
            "control_points": deepcopy(control_points),
            "survey_accuracy_verified": False,
            "terrain_alignment": "not_performed",
        },
    }
