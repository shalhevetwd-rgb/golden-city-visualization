"""Persist a hash-pinned, isolated facade edit using Blender.

The source SHA256 pins a version; it is not a digital signature. This worker
saves editable native/GLB models, and does not promise updated rendered pixels.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from constraints import compare_snapshots
from scene_audit import snapshot, apply_facade_color, apply_building_facade_color, _validate_color
from render_context import export_portable


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def edit(model, request, out):
    model, request, out = map(Path, (model, request, out))
    record = json.loads(request.read_text())
    if not isinstance(record, dict):
        raise ValueError('Edit request must be an object')
    expected = record.get('source_sha256')
    if not isinstance(expected, str) or len(expected) != 64 or _sha(model) != expected:
        raise ValueError('Edit source version mismatch')
    if model.suffix.lower() != '.blend':
        raise ValueError('Persistent facade editing requires a native Blender source')
    if record.get('operation') != 'facade_color' or record.get('role', 'facade') != 'facade':
        raise ValueError('Only facade material editing is supported')
    identity = record.get('building_id')
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError('Building id must be a nonempty string')
    color = _validate_color(record.get('color'))
    destinations = [out / name for name in ('edited.blend', 'edited.glb', 'edit-check.json')]
    if any(path.exists() for path in destinations):
        raise ValueError('Edit output version already exists; choose a new output directory')
    if any(path.resolve() in {model.resolve(), request.resolve()} for path in destinations):
        raise ValueError('Edit output cannot overwrite its source or request')
    # Never enable scripts embedded in a supplied .blend file.
    bpy.ops.wm.open_mainfile(filepath=str(model.resolve()), use_scripts=False)
    if _sha(model) != expected:
        raise ValueError('Source changed while opening the edit version')
    before = snapshot()
    facade = [obj for obj in bpy.data.objects if obj.type == 'MESH'
              and obj.get('owner') == identity and obj.get('role') == 'facade']
    if facade:
        eligible = apply_building_facade_color(identity, color)
    else:
        # Preserve editing of the original v2 single-body facade scenes.
        target = bpy.data.objects.get('building:' + identity)
        if target is None or target.type != 'MESH' or target.get('owner') != identity or target.get('role') is not None:
            raise ValueError('No eligible facade for target building')
        apply_facade_color(target, color)
        eligible = [target.name]
    after = snapshot()
    qa = compare_snapshots(before, after, identity)
    if not qa['passed'] or set(qa['changed_objects']) != set(eligible):
        raise ValueError('Edit changed immutable scene data or did not update every eligible facade')
    if _sha(model) != expected:
        raise ValueError('Source changed during the edit')
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.edit-stage-', dir=out) as temporary:
        staging = Path(temporary)
        blend, glb = staging / 'edited.blend', staging / 'edited.glb'
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        # Cameras/lights are part of the saved scene. Procedural shaders remain
        # native; GLB receives the audited constant PBR finish, including tint.
        portable = export_portable(bpy.context.scene, glb)
        if not isinstance(portable, dict) or portable.get('passed') is not True or portable.get('all_primitive_factors_verified') is not True:
            raise ValueError('Portable GLB material verification is missing or failed')
        if portable.get('glb_sha256') != _sha(glb):
            raise ValueError('Portable material proof is not bound to the saved GLB bytes')
        if snapshot() != after:
            raise ValueError('Portable export did not restore the audited native material links')
        if glb.read_bytes()[:4] != b'glTF':
            raise ValueError('Blender did not export a GLB')
        bpy.ops.wm.open_mainfile(filepath=str(blend), use_scripts=False)
        reopened = snapshot()
        if reopened != after:
            raise ValueError('Saved native model changed the audited scene')
        qa.update({'source_sha256': expected, 'operation': 'facade_color', 'building_id': identity,
                   'eligible_facade_objects': eligible, 'native_reopen_verified': True,
                   'unchanged_scene_objects': sorted(set(before) - set(qa['changed_objects'])),
                   'glb_material_representation': 'constant PBR factors; native procedural maps retained in Blender',
                   'material_export_verified': True,
                   'portable_material_node_count': len(portable.get('material_bindings', {})),
                   'outputs': {path.name: {'sha256': _sha(path), 'bytes': path.stat().st_size}
                               for path in (blend, glb)},
                   'render_updated': False, 'certification': False})
        (staging / 'edit-check.json').write_text(json.dumps(qa, indent=2, allow_nan=False))
        for name in ('edited.blend', 'edited.glb', 'edit-check.json'):
            (staging / name).replace(out / name)
    return qa


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 3:
        raise ValueError('Expected source.blend request.json output-directory')
    result = edit(*args)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
