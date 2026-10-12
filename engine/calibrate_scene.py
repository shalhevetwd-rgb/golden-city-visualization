import bpy,math,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'outputs/v2';bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'));scene=bpy.context.scene;scene.view_settings.exposure=-1.25;scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.12;bpy.data.objects['lighting:sun'].data.energy=2.4
for name in ['Warm limestone, procedural','Light limestone']:
 m=bpy.data.materials.get(name)
 if not m:continue
 n=m.node_tree.nodes;l=m.node_tree.links;bsdf=n.get('Principled BSDF');geo=n.new('ShaderNodeNewGeometry');sep=n.new('ShaderNodeSeparateXYZ');l.new(geo.outputs['Position'],sep.inputs[0]);norm=n.new('ShaderNodeSeparateXYZ');l.new(geo.outputs['Normal'],norm.inputs[0]);absn=n.new('ShaderNodeMath');absn.operation='ABSOLUTE';l.new(norm.outputs['X'],absn.inputs[0]);mix=n.new('ShaderNodeMix');mix.data_type='FLOAT';l.new(absn.outputs[0],mix.inputs[0]);l.new(sep.outputs['X'],mix.inputs[2]);l.new(sep.outputs['Y'],mix.inputs[3]);vec=n.new('ShaderNodeCombineXYZ');l.new(mix.outputs[0],vec.inputs[0]);l.new(sep.outputs['Z'],vec.inputs[1]);brick=n.new('ShaderNodeTexBrick');l.new(vec.outputs[0],brick.inputs['Vector']);brick.inputs['Scale'].default_value=1;brick.inputs['Mortar Size'].default_value=.007;brick.inputs['Brick Width'].default_value=.95;brick.inputs['Row Height'].default_value=.42;brick.inputs['Color1'].default_value=(.58,.51,.39,1);brick.inputs['Color2'].default_value=(.74,.67,.54,1);brick.inputs['Mortar'].default_value=(.30,.27,.22,1);l.new(brick.outputs['Color'],bsdf.inputs['Base Color']);bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.24;bump.inputs['Distance'].default_value=.005;l.new(brick.outputs['Fac'],bump.inputs['Height']);l.new(bump.outputs['Normal'],bsdf.inputs['Normal'])
# Existing tree geometry: smooth shading only, no changed positions or topology.
for o in bpy.data.objects:
 if o.type=='MESH' and o.name.startswith('landscape:tree:'):
  for p in o.data.polygons:p.use_smooth=True
bpy.ops.wm.save_as_mainfile(filepath=str(out/'model.blend'));print('Lighting calibrated; source meshes untouched')
