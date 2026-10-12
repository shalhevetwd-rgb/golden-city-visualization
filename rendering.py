"""Headless, local Blender renderer; coordinates are meters with local Z up.

The saved scene JSON is the render's immutable input, and the .blend preserves
objects, materials, camera and lighting so it can be opened in Blender directly.

Terrain is always an unmeasured schematic flat plane. Uploaded aerial images
and context meshes currently appear only in the interactive viewer; this offline
renderer does not import them. Decorative roads/aprons require synthetic=True.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time

from shapely import constrained_delaunay_triangles
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent
MATERIALS = {'plaster', 'stone', 'brick', 'glass'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def scene_origin(project):
    center = shape(project['boundary']).centroid
    return [center.x, center.y, 0.0]


def default_cameras(project):
    boundary = shape(project['boundary'])
    x0, y0, x1, y1 = boundary.bounds
    center = boundary.centroid
    extent = max(x1-x0, y1-y0, 20)
    heights = [float(b['properties']['height']) + float(b['properties'].get('base_elevation',0)) for b in project['buildings']]
    high = max(heights, default=20)
    # Street camera lies just outside the parcel, at eye height; no measured
    # street coordinates or terrain elevations are implied by this preset.
    return {
        'aerial': {'id': 'aerial', 'name': 'מבט אווירי', 'position': [extent*.95, -extent*1.2, max(extent*.95, high*2)], 'target': [0, 0, high*.25], 'fov': 37},
        'street': {'id': 'street', 'name': 'מבט בגובה הרחוב — 1.7 מ׳', 'position': [(x0-center.x)*.45, y0-center.y-extent*.65, 1.7], 'target': [-extent*.08, -extent*.05, high*.43], 'fov': 38},
    }


def validate_camera(camera):
    if not isinstance(camera, dict):
        raise ValueError('Camera must be an object')
    values = {}
    for key in ('position', 'target'):
        vector = camera.get(key)
        if not isinstance(vector, (list, tuple)) or len(vector) != 3:
            raise ValueError(f'Camera {key} must contain three local coordinates')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1_000_000 for v in vector):
            raise ValueError('Camera coordinates must be finite local meters')
        values[key] = [float(v) for v in vector]
    if sum((a-b)**2 for a,b in zip(values['position'],values['target'])) < 1e-8:
        raise ValueError('Camera position and target must differ')
    fov = camera.get('fov', 50)
    if isinstance(fov, bool) or not isinstance(fov,(int,float)) or not math.isfinite(fov) or not 10 <= fov <= 120:
        raise ValueError('Camera field of view must be between 10 and 120 degrees')
    return {**camera, **values, 'fov': float(fov)}


def resolve_camera(project, camera=None):
    cameras = default_cameras(project)
    saved = project.get('cameras', {})
    if isinstance(saved, dict):
        cameras.update(saved)
    elif isinstance(saved, list):
        cameras.update({c.get('id', c.get('name')): c for c in saved})
    if camera is None:
        camera = 'aerial'
    if isinstance(camera, str):
        if camera not in cameras:
            raise ValueError('Saved camera was not found')
        camera = cameras[camera]
    return validate_camera(camera)


def mask_color(index):
    encoded = (index * 2654435761) & 0xffffff
    return [(encoded >> 16) & 255, (encoded >> 8) & 255, encoded & 255]


def building_fingerprints(project):
    result = {}
    for feature in project['buildings']:
        props = feature['properties']
        identifier = str(props['id'])
        geometry = {'geometry': feature['geometry'], 'height': props['height'], 'base_elevation':props.get('base_elevation',0), 'floors': props['floors'], 'balconies': props.get('balconies', []), 'slabs':props.get('slabs',[])}
        material = {'color': props.get('color','#d9bd89'), 'material': props.get('material','stone')}
        result[identifier] = {'geometry_hash': _hash(geometry), 'material_hash': _hash(material), 'content_hash': _hash({'geometry': geometry, 'material': material})}
    return result


def polygon_meshes(project):
    """Prepare exact constrained cap triangles for Polygon holes in Blender.

    Blender's plain ngon cap would fill holes. Keep these render meshes outside
    the frozen project so source geometries and version hashes remain intact.
    """
    geometries = [project['boundary']]
    for feature in project['buildings']:
        geometries.append(feature['geometry'])
        props=feature['properties']
        for key in ('balconies','slabs'):
            geometries.extend(record['footprint'] for record in props.get(key,[]))
    geometries.extend(record['footprint'] for record in project.get('overhead_obstructions',[]))
    prepared={}
    for geometry in geometries:
        polygon=shape(geometry)
        if not polygon.interiors:
            continue
        if not polygon.is_valid:
            raise ValueError('Rendering requires valid Polygon openings')
        triangles=list(constrained_delaunay_triangles(polygon).geoms)
        union=unary_union(triangles)
        if union.symmetric_difference(polygon).area>max(1e-8,polygon.area*1e-10):
            raise RuntimeError('Constrained triangulation failed to preserve Polygon openings')
        prepared[_hash(geometry)]={
            'rings':[[list(coordinate[:2]) for coordinate in ring.coords[:-1]] for ring in [polygon.exterior,*polygon.interiors]],
            'triangles':[[list(coordinate[:2]) for coordinate in triangle.exterior.coords[:-1]] for triangle in triangles],
            'surface_area':polygon.area,
        }
    return prepared


def png_size(path):
    with Path(path).open('rb') as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
        raise RuntimeError('Blender did not produce a valid PNG')
    return struct.unpack('>II', header[16:24])


def render_project(project, camera=None, output_path=None, *, width=640, height=480, samples=24, timeout=180, save_blend=True, object_mask=True, export_models=False):
    """Render a validated project using Blender CPU Cycles, without a display.

    This is a schematic architectural visualization, not a measured terrain
    reconstruction. Synthetic decoration requires an explicit project flag. Object masks have flat unique RGB labels and no lighting;
    they establish unchanged visible geometry separately from cast shadows.
    """
    executable = os.environ.get('GOLDEN_CITY_BLENDER') or shutil.which('blender')
    if not executable:
        raise RuntimeError('Blender executable is unavailable; install Blender for PNG rendering')
    for dimension in (width,height):
        if type(dimension) is not int or not 64 <= dimension <= 4096:
            raise ValueError('Render dimensions must be integers between 64 and 4096')
    if type(samples) is not int or not 1 <= samples <= 256:
        raise ValueError('Render samples must be between 1 and 256')
    if not isinstance(timeout,(int,float)) or not 1 <= timeout <= 600:
        raise ValueError('Render timeout must be between 1 and 600 seconds')
    if project.get('errors'):
        raise ValueError('Rendering requires geometry without validation errors')
    selected = resolve_camera(project,camera)
    if output_path is None:
        output_path = ROOT / 'data' / 'renders' / (str(project.get('id','preview')) + '.png')
    output = Path(output_path).resolve()
    if output.suffix.lower() != '.png':
        raise ValueError('Render output must have a .png extension')
    output.parent.mkdir(parents=True, exist_ok=True)
    scene_path = output.with_suffix('.scene.json')
    inventory_path = output.with_suffix('.objects.json')
    obj_path = output.with_suffix('.obj')
    glb_path = output.with_suffix('.glb')
    blend_path = output.with_suffix('.blend')
    mask_path = output.with_name(output.stem + '-objects.png')
    log_path = output.with_suffix('.log')
    manifest = {'project':project, 'polygon_meshes':polygon_meshes(project), 'camera': selected, 'origin':scene_origin(project), 'width':width, 'height':height, 'samples':samples, 'output':str(output), 'blend':str(blend_path) if save_blend else None, 'inventory':str(inventory_path), 'obj':str(obj_path) if export_models else None, 'glb':str(glb_path) if export_models else None, 'mask':str(mask_path) if object_mask else None}
    # JSON input, no shell interpolation and no embedded Python or executable
    # content from the project. The fixed Blender script reads this snapshot.
    scene_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    started = time.monotonic()
    command = [executable, '--background', '--factory-startup', '--threads', str(min(os.cpu_count() or 2, 6)), '--python', str(ROOT/'blender_scene.py'), '--', str(scene_path)]
    try:
        with log_path.open('w') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f'Blender rendering timed out after {timeout} seconds; log: {log_path}') from error
    if completed.returncode or not output.exists():
        tail = log_path.read_text(errors='replace')[-1800:]
        raise RuntimeError(f'Blender rendering failed ({completed.returncode}): {tail}')
    if png_size(output) != (width,height):
        raise RuntimeError('Rendered PNG dimensions do not match request')
    if object_mask and png_size(mask_path) != (width,height):
        raise RuntimeError('Object mask PNG dimensions do not match request')
    # Verification opens artifacts with independent consumers. A signature or
    # a successful producer process alone cannot establish usable output.
    from delivery_validation import validate_file
    inventory=json.loads(inventory_path.read_text())
    source_counts=inventory['counts']
    building_names=['building:'+str(feature['properties']['id']) for feature in project['buildings']]
    delivery_checks={}
    artifacts=[(output,{'width':width,'height':height}),(scene_path,None),(inventory_path,None)]
    if object_mask:artifacts.append((mask_path,{'width':width,'height':height}))
    source_bounds={record['name']:[record['bounds_min'],record['bounds_max']] for record in inventory['objects'] if record['type']=='MESH'}
    native_expected={'objects':source_counts['mesh_objects'],'object_names':building_names,'object_bounds':source_bounds,'bound_tolerance':1e-4,'cameras':1,'camera_parameters':{inventory['camera']['name']:{key:inventory['camera'][key] for key in ('position','forward','up','vertical_fov')}},'min_vertices':source_counts['vertices'],'min_faces':source_counts['polygons']}
    if save_blend:artifacts.append((blend_path,{**native_expected,'vertices':source_counts['vertices'],'faces':source_counts['polygons']}))
    if export_models:
        artifacts.extend([(obj_path,{**{key:value for key,value in native_expected.items() if key!='camera_parameters'},'cameras':0,'min_vertices':source_counts['unique_vertices'],'faces':source_counts['polygons']}),(glb_path,{**native_expected,'min_vertices':source_counts['unique_vertices'],'faces':source_counts['triangles']}),(obj_path.with_suffix('.mtl'),None)])
    for artifact,expected in artifacts:
        verified=validate_file(artifact,expected=expected,artifact_role='render output')
        if artifact.suffix in ('.blend','.glb'):
            verified['checks'].append('camera_position_direction_up_vertical_fov_match_source')
        delivery_checks[artifact.name]=verified
        if verified.get('status')!='passed':
            raise RuntimeError(f'Output verification failed for {artifact.name}: {verified}')
    metadata = {
        'engine':'Blender Cycles CPU', 'synthetic':project.get('synthetic') is True, 'scene_type':'schematic architectural visualization', 'project_id':project.get('id'), 'project_version':project.get('version'), 'frozen_project_hash':_hash(project), 'terrain':'flat unmeasured local z=0',
        'duration_seconds':round(time.monotonic()-started,3), 'width':width,'height':height,'samples':samples,'denoising':False,
        'camera':selected, 'fov_axis':'vertical', 'origin':manifest['origin'], 'crs':project['crs'], 'coordinates':'local meters x=easting, y=northing, z=up; subtract origin from project XY',
        'output_path':str(output), 'scene_path':str(scene_path), 'inventory_path':str(inventory_path), 'obj_path':str(obj_path) if export_models else None, 'glb_path':str(glb_path) if export_models else None, 'mtl_path':str(obj_path.with_suffix('.mtl')) if export_models else None, 'blend_path':str(blend_path) if save_blend else None, 'object_mask_path':str(mask_path) if object_mask else None,
        'png_sha256':hashlib.sha256(output.read_bytes()).hexdigest(), 'delivery_checks':delivery_checks, 'source_scene_counts':source_counts, 'model_expectations':{artifact.name:expected for artifact,expected in artifacts if artifact.suffix in ('.blend','.obj','.glb')}, 'model_format_coordinates':'OBJ declares local XYZ Z-up, forward=Y/up=Z; GLB uses standard Y-up and Blender re-import restores scene Z-up', 'building_fingerprints':building_fingerprints(project),
        'object_mask_labels':{str(b['properties']['id']):mask_color(index+1) for index,b in enumerate(project['buildings'])},
        'limitations':['No cadastral or terrain validation; terrain is always a flat unmeasured plane','Uploaded aerial images and context meshes are supported in the interactive viewer only, not this offline Blender renderer','Procedural windows and materials are schematic','OBJ and GLB use constant PBR base colors; procedural shaders and sky are preserved in native Blender only','Changes may affect global lighting and shadows while neighboring objects remain unchanged'],
    }
    metadata_path=output.with_suffix('.metadata.json')
    metadata_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    verified_metadata=validate_file(metadata_path,artifact_role='render metadata')
    if verified_metadata.get('status')!='passed':
        raise RuntimeError(f'Output metadata verification failed: {verified_metadata}')
    return metadata
