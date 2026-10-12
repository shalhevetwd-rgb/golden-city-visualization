"""Faithfully refresh portable projections from frozen native v3 scenes.

No PNG or native source is written. Temporary mesh/material data is audited
away before each successful portable file replaces its prior projection.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import bpy

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from render_context import load_context,export_portable,preflight_source_geometry
from scene_audit import snapshot


def identity(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


cfg,out=load_context()
frozen_names=('model.blend','edited-model.blend','aerial.png','street.png',
              'architectural.png','edited-aerial.png','before-after.png','contact-sheet.png')
before_files={name:identity(out/name) for name in frozen_names}
reports={}
for name in ('model.blend','edited-model.blend'):
    bpy.ops.wm.open_mainfile(filepath=str(out/name),use_scripts=False,load_ui=False)
    scene=bpy.context.scene;preflight=preflight_source_geometry(scene,out)
    before=snapshot();mesh_names=set(bpy.data.meshes.keys());material_names=set(bpy.data.materials.keys())
    portable=name.replace('.blend','.glb')
    with tempfile.TemporaryDirectory(prefix='.portable-stage-',dir=out) as temporary:
        staged=Path(temporary)/portable
        report=export_portable(scene,staged)
        if snapshot()!=before:raise ValueError('Portable export changed the frozen native scene: '+name)
        if mesh_names!=set(bpy.data.meshes.keys()) or material_names!=set(bpy.data.materials.keys()):
            raise ValueError('Portable export did not remove all temporary mesh/material data')
        report.update(native_source=name,native_source_sha256=before_files[name],
                      source_geometry_preflight=preflight,native_snapshot_restored=True,
                      temporary_data_removed=True)
        staged.replace(out/portable);reports[portable]=report
    print('PORTABLE ALL-MESH MATERIAL FACTORS AND NATIVE RESTORATION VERIFIED',portable,report['mesh_nodes_checked'],flush=True)
after_files={name:identity(out/name) for name in frozen_names}
if after_files!=before_files:raise ValueError('Refreshing GLB projections changed a frozen native/image source')
reports['frozen_sources']={'before_sha256':before_files,'after_sha256':after_files,'unchanged':True}
(out/'portable-materials-report.json').write_text(json.dumps(reports,indent=2))
print('FROZEN BLEND/PNG SOURCES UNCHANGED; BOTH GLB MATERIAL PROJECTIONS REFRESHED',flush=True)
