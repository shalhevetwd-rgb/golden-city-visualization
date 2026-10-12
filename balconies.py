"""Conservative geometric sky-projection checks for explicitly modelled slabs.

All horizontal coordinates use the project's projected metre CRS. Elevation is
the slab's top, in one common local vertical datum, and thickness extends down.
The output is geometric evidence only, never halachic or professional approval.
"""

import math

from shapely.geometry import GeometryCollection, LineString, mapping
from shapely.ops import unary_union

from spatial import polygon_geometry, projected_metric_crs


def _number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


def _slab(record, kind, building_id=None):
    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
        raise ValueError(f"Every {kind} requires a nonempty string id")
    geometry = polygon_geometry(record.get("footprint"), f"{kind} footprint")
    upper = _number(record.get("elevation"), f"{kind} elevation")
    thickness = _number(record.get("thickness", 0.25 if kind == "balcony" else 0.2),
                        f"{kind} thickness", positive=True)
    return {
        "id": record["id"], "kind": kind, "building_id": building_id,
        "geometry": geometry, "lower_z": upper - thickness, "upper_z": upper,
    }


def _line_segments(geometry, line):
    intersection = geometry.intersection(line)
    if intersection.is_empty:
        return []
    parts = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
    result = []
    for part in parts:
        if part.geom_type == "LineString" and part.length > 1e-9:
            result.append([part.bounds[0], part.bounds[2]])
    return result


def analyze_balcony(project, building_id, balcony_id, overhead_complete=False):
    """Project modelled geometry above one balcony onto its horizontal footprint.

    Buildings contain ``properties.balconies`` and optional ``properties.slabs``;
    each slab has id, footprint (Polygon), elevation (top in metres), and optional
    positive thickness (default 0.25 m for balconies, 0.2 m for other slabs).
    Optional project-level
    ``overhead_obstructions`` uses the same fields. Body elevations use
    ``properties.base_elevation`` (default 0) and required positive ``height``.

    Missing overhead data is unknown by default: ``open_sky_area_m2`` is null.
    Passing ``overhead_complete=True`` is an explicit caller declaration, not
    verification of the source, hidden geometry, terrain, or legal suitability.
    """
    if not isinstance(project, dict):
        raise ValueError("A project object is required")
    projected_metric_crs(project.get("crs"))
    if type(overhead_complete) is not bool:
        raise ValueError("overhead_complete must be an explicit boolean")
    if not isinstance(building_id, str) or not isinstance(balcony_id, str):
        raise ValueError("Building and balcony ids must be strings")
    features = project.get("buildings")
    if not isinstance(features, list) or not features:
        raise ValueError("The project requires buildings")
    obstacles, selected, building_ids = [], None, set()
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise ValueError("Building features require properties")
        properties = feature["properties"]
        identifier = str(properties.get("id", ""))
        if not identifier or identifier in building_ids:
            raise ValueError("Building ids must be present and unique")
        building_ids.add(identifier)
        footprint = polygon_geometry(feature.get("geometry"), "building footprint")
        base = _number(properties.get("base_elevation", 0), "building base_elevation")
        height = _number(properties.get("height"), "building height", positive=True)
        obstacles.append({"id": identifier, "building_id": identifier, "kind": "building",
                          "geometry": footprint, "lower_z": base, "upper_z": base + height})
        for collection_name, kind in (("balconies", "balcony"), ("slabs", "slab")):
            records = properties.get(collection_name, [])
            if not isinstance(records, list):
                raise ValueError(f"{collection_name} must be a list")
            seen = set()
            for record in records:
                obstacle = _slab(record, kind, identifier)
                if obstacle["id"] in seen:
                    raise ValueError(f"Duplicate {kind} id")
                seen.add(obstacle["id"])
                if kind == "balcony" and identifier == building_id and obstacle["id"] == balcony_id:
                    selected = obstacle
                obstacles.append(obstacle)
    overhead = project.get("overhead_obstructions", [])
    if not isinstance(overhead, list):
        raise ValueError("overhead_obstructions must be a list")
    seen_overhead = set()
    for record in overhead:
        obstacle = _slab(record, "overhead")
        if obstacle["id"] in seen_overhead:
            raise ValueError("Duplicate overhead obstruction id")
        seen_overhead.add(obstacle["id"])
        obstacles.append(obstacle)
    if selected is None:
        raise ValueError("Selected balcony was not found")
    geometry, elevation = selected["geometry"], selected["upper_z"]
    projections = []
    for obstacle in obstacles:
        if obstacle is selected or obstacle["upper_z"] <= elevation + 1e-8:
            continue
        covered = geometry.intersection(obstacle["geometry"])
        if covered.area > 1e-9:
            projections.append({**obstacle, "geometry": covered})
    blocked = unary_union([obstacle["geometry"] for obstacle in projections]) if projections else GeometryCollection()
    unobstructed = geometry.difference(blocked)
    area, blocked_area, geometric_area = geometry.area, blocked.area, unobstructed.area
    min_x, min_y, max_x, max_y = geometry.bounds
    section_y = geometry.centroid.y
    section_line = LineString([(min_x - 1, section_y), (max_x + 1, section_y)])
    top_obstructions = []
    section_obstructions = []
    for obstacle in projections:
        description = {key: obstacle[key] for key in ("id", "kind", "building_id", "lower_z", "upper_z")}
        top_obstructions.append({**description, "footprint": mapping(obstacle["geometry"])})
        segments = _line_segments(obstacle["geometry"], section_line)
        if segments:
            section_obstructions.append({**description, "segments_x": segments})
    return {
        "building_id": building_id,
        "balcony_id": balcony_id,
        "crs": project["crs"],
        "elevation_m": elevation,
        "total_area_m2": area,
        "projected_obstruction_area_m2": blocked_area,
        "geometric_open_sky_area_m2": geometric_area,
        "open_sky_area_m2": geometric_area if overhead_complete else None,
        "open_sky_fraction": geometric_area / area if overhead_complete else None,
        "geometrically_clear": blocked_area <= 1e-9 if overhead_complete else None,
        "overhead_data_complete": overhead_complete,
        "completeness_basis": "caller_declared" if overhead_complete else "unknown",
        "status": "geometric_analysis_only" if overhead_complete else "incomplete_scene",
        "certification": {"halachic": False, "professional": False, "survey": False},
        "limitations": [
            "Only explicit horizontal footprints and vertical ranges are checked.",
            "Model completeness and vertical datum have not been independently verified.",
            "Open projection area does not determine halachic or professional suitability.",
        ],
        "top_view": {"balcony": mapping(geometry), "blocked": mapping(blocked),
                     "open": mapping(unobstructed), "obstructions": top_obstructions},
        "section": {
            "axis": "east-west", "plane_y": section_y,
            "balcony_segments_x": _line_segments(geometry, section_line),
            "balcony_elevation_m": elevation,
            "balcony_bottom_m": selected["lower_z"],
            "obstructions": section_obstructions,
            "min_z": min([selected["lower_z"]] + [max(elevation, item["lower_z"]) for item in projections]),
            "max_z": max([elevation] + [item["upper_z"] for item in projections]),
        },
    }
