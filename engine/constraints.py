"""Recorded-evidence gates and immutable-scene checks.

A complete review record binds exact local source bytes to a geometry version.
It does not authenticate a reviewer, source authority, cadastral accuracy, or
religious approval. Real rendering also requires trusted renderer capabilities.
"""
import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

REQUIRED_EVIDENCE = (
    'parcel_geometry_verified', 'alignment_approved', 'building_positions_verified',
    'floors_verified', 'heights_verified', 'terrain_verified',
)
REQUIRED_RENDER_CAPABILITIES = frozenset({
    'verified_terrain', 'preserved_environment', 'georeferenced_inputs',
})


def required_evidence_fields():
    return list(REQUIRED_EVIDENCE)


def public_requirements():
    return {'approvals': list(REQUIRED_EVIDENCE),
            'project_fields': ['id', 'crs', 'geometry_manifest_path', 'geometry_version_sha256', 'inputs'],
            'input_fields': ['path', 'sha256'],
            'review_fields': ['source', 'source_sha256', 'checked_at', 'verification',
                              'project_id', 'geometry_version_sha256', 'reviewer'],
            'reviewer_fields': ['id', 'name'],
            'verification': 'professional_review',
            'checked_at': 'ISO 8601 timestamp with explicit timezone',
            'source_paths': 'Existing relative paths contained within the supplied base_dir',
            'geometry': 'Current SHA256-pinned finite JSON manifest with matching projected metre CRS, polygon boundary and buildings',
            'renderer_capabilities': sorted(REQUIRED_RENDER_CAPABILITIES),
            'certification': False}


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Geometry manifest contains non-finite numbers')
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)


def _read_bound_file(base_dir, path, expected_sha):
    if base_dir is None or not _nonempty(path) or not _sha(expected_sha):
        raise ValueError('Missing base_dir, relative path or valid SHA256')
    relative = Path(path)
    if relative.is_absolute() or '..' in relative.parts or path in {'.', ''}:
        raise ValueError('Source path must be relative and cannot traverse outside base_dir')
    root = Path(base_dir).resolve()
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError('Source file is not contained within base_dir')
    payload = resolved.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha:
        raise ValueError('Source bytes differ from recorded SHA256')
    return payload


def _number(value, name, positive=False):
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        finite = False
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not finite:
        raise ValueError(name + ' must be a finite number')
    if positive and value <= 0:
        raise ValueError(name + ' must be positive')
    return value


def _polygon(value):
    from shapely.geometry import shape
    if not isinstance(value, dict) or value.get('type') not in {'Polygon', 'MultiPolygon'}:
        raise ValueError('Boundary/footprint must be a Polygon or MultiPolygon')
    def points(coords):
        if not isinstance(coords, list) or not coords:
            raise ValueError('Polygon coordinates are missing')
        if isinstance(coords[0], (int, float, bool)):
            if len(coords) != 2:
                raise ValueError('Polygon coordinates must be strictly 2D')
            for coordinate in coords:
                _number(coordinate, 'Polygon coordinate')
        else:
            for child in coords:
                points(child)
    points(value.get('coordinates'))
    geometry = shape(value)
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise ValueError('Boundary/footprint polygon is empty or invalid')
    return geometry


def _validate_manifest(manifest, project):
    from pyproj import CRS
    if not isinstance(manifest, dict):
        raise ValueError('Geometry manifest must be an object')
    _finite_json(manifest)
    if manifest.get('crs') != project.get('crs'):
        raise ValueError('Geometry CRS is not bound to the project CRS')
    crs = CRS.from_user_input(project.get('crs'))
    if not crs.is_projected or not crs.axis_info or any(
            not math.isclose(axis.unit_conversion_factor, 1, abs_tol=1e-12)
            for axis in crs.axis_info[:2]):
        raise ValueError('Geometry CRS must be projected in metres')
    manifest_id = manifest.get('project_id', manifest.get('id'))
    if manifest_id is not None and manifest_id != project.get('id'):
        raise ValueError('Geometry manifest belongs to another project')
    _polygon(manifest.get('boundary'))
    buildings = manifest.get('buildings')
    if not isinstance(buildings, list) or not buildings:
        raise ValueError('Geometry manifest requires buildings')
    ids = set()
    for building in buildings:
        if not isinstance(building, dict) or not isinstance(building.get('properties'), dict):
            raise ValueError('Building geometry requires properties')
        props = building['properties']
        identity = props.get('id')
        if not _nonempty(identity) or identity in ids:
            raise ValueError('Building identities must be unique nonempty strings')
        ids.add(identity)
        _polygon(building.get('geometry'))
        _number(props.get('height'), 'Building height', positive=True)
        floors = props.get('floors')
        if isinstance(floors, bool) or not isinstance(floors, int) or floors <= 0:
            raise ValueError('Building floors must be a positive integer')
        if props.get('base_elevation') is not None:
            _number(props['base_elevation'], 'Base elevation')


def _timestamp(value):
    if not _nonempty(value):
        return False
    try:
        when = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return when.tzinfo is not None and when.utcoffset() is not None and when <= datetime.now(timezone.utc) + timedelta(minutes=5)
    except (ValueError, TypeError, OverflowError):
        return False


def site_gate(project, base_dir=None, renderer_capabilities=None):
    """Fail closed on stale/missing bytes, review records, geometry or capability.

    renderer_capabilities is supplied by trusted backend code, never read from
    project JSON. Current flat-ground renderer supplies none for real projects.
    """
    p = project if isinstance(project, dict) else {}
    synthetic = p.get('synthetic') is True
    result = {'final_render_allowed': synthetic,
              'classification': 'synthetic_test' if synthetic else 'blocked_for_missing_evidence',
              'missing': list(REQUIRED_EVIDENCE), 'validation_errors': [],
              'evidence_complete': False, 'recorded_real_site_review': False,
              'accurate_real_site': False, 'certification': False,
              'missing_renderer_capabilities': [] if synthetic else sorted(REQUIRED_RENDER_CAPABILITIES)}
    if synthetic:
        return result
    errors = result['validation_errors']
    if not isinstance(project, dict):
        errors.append('Project must be an object')
        return result
    if p.get('synthetic', False) is not False:
        errors.append('synthetic must be a literal boolean')
    if not _nonempty(p.get('id')):
        errors.append('Project identity is missing')
    geometry_valid = False
    try:
        raw = _read_bound_file(base_dir, p.get('geometry_manifest_path'), p.get('geometry_version_sha256'))
        manifest = json.loads(raw)
        _validate_manifest(manifest, p)
        geometry_valid = True
    except Exception as error:
        errors.append('Geometry: ' + str(error))
    sources = p.get('inputs')
    valid_sources = {}
    if not isinstance(sources, dict) or not sources:
        errors.append('Current source inputs are missing')
    else:
        for identity, record in sources.items():
            try:
                if not _nonempty(identity) or not isinstance(record, dict):
                    raise ValueError('Source identity/record is invalid')
                _read_bound_file(base_dir, record.get('path'), record.get('sha256'))
                valid_sources[identity] = record['sha256']
            except (ValueError, TypeError, OSError) as error:
                errors.append('Source ' + str(identity) + ': ' + str(error))
    evidence = p.get('evidence', {})
    if not isinstance(evidence, dict):
        evidence = {}
        errors.append('Evidence records must be an object')
    missing = []
    for key in REQUIRED_EVIDENCE:
        record = evidence.get(key)
        reviewer = record.get('reviewer') if isinstance(record, dict) else None
        valid = (p.get(key) is True and isinstance(record, dict) and geometry_valid
                 and record.get('verification') == 'professional_review'
                 and record.get('project_id') == p.get('id') and _nonempty(p.get('id'))
                 and record.get('geometry_version_sha256') == p.get('geometry_version_sha256')
                 and _timestamp(record.get('checked_at')) and isinstance(reviewer, dict)
                 and _nonempty(reviewer.get('id')) and _nonempty(reviewer.get('name'))
                 and _nonempty(record.get('source')) and record.get('source') in valid_sources
                 and record.get('source_sha256') == valid_sources.get(record.get('source')))
        if not valid:
            missing.append(key)
    result['missing'] = missing
    complete = not missing and not errors
    result['evidence_complete'] = complete
    result['recorded_real_site_review'] = complete
    capabilities = {cap for cap in renderer_capabilities if isinstance(cap, str)} if isinstance(renderer_capabilities, (set, frozenset, list, tuple)) else set()
    result['missing_renderer_capabilities'] = sorted(REQUIRED_RENDER_CAPABILITIES - capabilities)
    result['final_render_allowed'] = complete and not result['missing_renderer_capabilities']
    if complete:
        result['classification'] = 'recorded_site_review' if result['final_render_allowed'] else 'blocked_for_renderer_capabilities'
    return result


def _rectangle(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(name + ' must contain four coordinates')
    x0, y0, x1, y1 = [_number(v, name) for v in value]
    if x1 <= x0 or y1 <= y0:
        raise ValueError(name + ' must have positive width and length')
    return x0, y0, x1, y1


def sukkah_geometry(spec, obstructions, policy, *, obstructions_complete=None):
    """Rectangle-based geometric screen only, with explicitly complete inputs."""
    if not isinstance(policy, dict) or any(k not in policy for k in ['min_width_m', 'min_length_m', 'min_height_m']):
        return {'status': 'missing_project_policy', 'halakhic_approval': False}
    thresholds = [_number(policy[k], k, positive=True) for k in ['min_width_m', 'min_length_m', 'min_height_m']]
    if not isinstance(spec, dict):
        raise ValueError('Balcony specification must be an object')
    rectangle = _rectangle(spec.get('rectangle'), 'Balcony rectangle')
    height = _number(spec.get('clear_height_m'), 'Clear height', positive=True)
    z = _number(spec.get('floor_z'), 'Floor elevation')
    if not isinstance(obstructions, (list, tuple)):
        raise ValueError('Obstructions must be an explicit list')
    obstructed = False
    x0, y0, x1, y1 = rectangle
    for item in obstructions:
        if not isinstance(item, dict):
            raise ValueError('Obstruction must be an object')
        ox0, oy0, ox1, oy1 = _rectangle(item.get('rectangle'), 'Obstruction rectangle')
        elevation = _number(item.get('z'), 'Obstruction elevation')
        if elevation > z and min(x1, ox1) > max(x0, ox0) and min(y1, oy1) > max(y0, oy0):
            obstructed = True
    complete = (spec.get('obstructions_complete') if obstructions_complete is None else obstructions_complete) is True
    size = x1 - x0 >= thresholds[0] and y1 - y0 >= thresholds[1] and height >= thresholds[2]
    status = 'geometry_failed' if obstructed or not size else 'geometric_candidate' if complete else 'unknown_obstructions'
    return {'status': status, 'sky_obstructed': obstructed,
            'obstructions_complete': complete, 'sky_open_in_submitted_geometry': complete and not obstructed,
            'dimension_screen_passed': size, 'halakhic_approval': False,
            'requires': ['סכך', 'דפנות', 'פסיקה ואישור מקצועי לפרויקט']}


def compare_snapshots(before, after, target):
    """Only facade material data may change; all scene structure is immutable."""
    changed = sorted(k for k in before if before[k] != after.get(k))
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    outside = [k for k in changed if before[k].get('owner') != target]
    geometry_changed = [k for k in changed if before[k].get('geometry') != after.get(k, {}).get('geometry')
                        or before[k].get('transform') != after.get(k, {}).get('transform')]
    immutable = []
    nonfacade = []
    for key in changed:
        prior, current = before[key], after.get(key, {})
        fields = set(prior) | set(current)
        if any(prior.get(field) != current.get(field) for field in fields - {'materials', 'material'}):
            immutable.append(key)
        if prior.get('role') is not None and prior['role'] != 'facade' and prior.get('owner') == target:
            nonfacade.append(key)
    return {'changed_objects': changed, 'added_objects': added, 'removed_objects': removed,
            'outside_target_changed': outside, 'geometry_changed': geometry_changed,
            'immutable_state_changed': immutable, 'nonfacade_changed': nonfacade,
            'passed': not outside and not geometry_changed and not immutable and not nonfacade and not added and not removed,
            'pixel_identity_claim': False}
