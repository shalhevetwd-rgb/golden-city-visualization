"""Independent Blender reader for delivery validation; no renderer imports.

Invoked in a fresh factory-startup process with automatic scripts disabled.
Only emits geometry/camera metadata to a temporary JSON file.
"""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def inspect_model(source):
    suffix = source.suffix.lower()
    if suffix == '.blend':
        bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if suffix == '.obj':
            with source.open(encoding='utf-8') as stream:
                header = ''.join(next(stream, '') for _ in range(8))
            axes = {'forward_axis': 'Y', 'up_axis': 'Z'} if 'axes: forward=Y up=Z' in header or 'local XYZ Z up' in header else {}
            bpy.ops.wm.obj_import(filepath=str(source), validate_meshes=False, **axes)
        elif suffix == '.glb':
            bpy.ops.import_scene.gltf(filepath=str(source))
        else:
            raise ValueError('Unsupported model format')
    meshes, cameras = [], []
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH':
            mesh = obj.data
            if mesh.validate(verbose=False, clean_customdata=False):
                raise ValueError('Re-imported mesh required repairs: ' + obj.name)
            positions = [list(obj.matrix_world @ vertex.co) for vertex in mesh.vertices]
            if any(not math.isfinite(value) for position in positions for value in position):
                raise ValueError('Non-finite mesh coordinates')
            if not positions or not mesh.polygons:
                raise ValueError('Empty geometry object: ' + obj.name)
            surface_area = sum(polygon.area for polygon in mesh.polygons)
            if not math.isfinite(surface_area) or surface_area <= 1e-12:
                raise ValueError('Model contains only degenerate geometry')
            mesh.calc_loop_triangles()
            world_surface_area = 0.0
            for triangle in mesh.loop_triangles:
                a, b, c = [obj.matrix_world @ mesh.vertices[index].co for index in triangle.vertices]
                world_surface_area += (b - a).cross(c - a).length * .5
            if not math.isfinite(world_surface_area) or world_surface_area <= 1e-12:
                raise ValueError('Model object transform collapses its geometry: ' + obj.name)
            meshes.append({
                'name': obj.name, 'vertices': len(mesh.vertices),
                'faces': len(mesh.polygons), 'surface_area_local': surface_area,
                'surface_area_world': world_surface_area,
                'bounds': [[min(p[axis] for p in positions) for axis in range(3)],
                           [max(p[axis] for p in positions) for axis in range(3)]],
            })
        elif obj.type == 'CAMERA':
            position = list(obj.matrix_world.translation)
            rotation = list(obj.matrix_world.to_euler())
            if not all(math.isfinite(value) for value in position + rotation):
                raise ValueError('Non-finite camera transform')
            if not math.isfinite(obj.data.lens) or obj.data.lens <= 0:
                raise ValueError('Invalid camera lens')
            cameras.append({'name': obj.name, 'position': position, 'rotation': rotation,
                            'quaternion': list(obj.matrix_world.to_quaternion().normalized()),
                            'forward': list((obj.matrix_world.to_quaternion() @ Vector((0, 0, -1))).normalized()),
                            'up': list((obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))).normalized()),
                            'type': obj.data.type, 'lens_mm': obj.data.lens,
                            'vertical_fov': math.degrees(obj.data.angle_y),
                            'sensor_height': obj.data.sensor_height, 'sensor_fit': obj.data.sensor_fit})
    if not meshes:
        raise ValueError('Model contains no mesh geometry')
    return {
        'reader': 'Blender independent re-import', 'blender_version': bpy.app.version_string,
        'format': suffix[1:], 'objects': sorted(meshes, key=lambda item: item['name']),
        'vertices': sum(item['vertices'] for item in meshes),
        'faces': sum(item['faces'] for item in meshes),
        'cameras': sorted(cameras, key=lambda item: item['name']),
        'camera_support': 'not supported by OBJ' if suffix == '.obj' else 'supported',
        'active_camera': bpy.context.scene.camera.name if bpy.context.scene.camera else None,
        'coordinate_interpretation': ('declared local XYZ Z up' if axes else 'OBJ default Y up') if suffix == '.obj' else 'Blender Z up',
    }


if __name__ == '__main__':
    arguments = sys.argv[sys.argv.index('--') + 1:]
    source, report = map(Path, arguments)
    try:
        result = {'status': 'passed', **inspect_model(source)}
    except Exception as error:
        result = {'status': 'failed', 'reason': str(error)}
    report.write_text(json.dumps(result, allow_nan=False, indent=2), encoding='utf-8')
    if result['status'] != 'passed':
        raise RuntimeError(result['reason'])
