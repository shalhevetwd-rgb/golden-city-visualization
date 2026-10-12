"""Fixed Blender Python entry point. Run through rendering.render_project."""
import json
import hashlib
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def linear_color(hex_color):
    def channel(v):
        v=int(v,16)/255
        return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
    return tuple(channel(hex_color[i:i+2]) for i in (1,3,5)) + (1,)


def solid(name, color, roughness=.65, metallic=0):
    material=bpy.data.materials.new(name)
    material.diffuse_color=color
    material.use_nodes=True
    principled=material.node_tree.nodes.get('Principled BSDF')
    principled.inputs['Base Color'].default_value=color
    principled.inputs['Roughness'].default_value=roughness
    principled.inputs['Metallic'].default_value=metallic
    return material


def facade(identifier, props):
    kind=props.get('material','stone')
    color=linear_color(props.get('color','#d9bd89'))
    material=solid('facade:'+identifier+':'+kind,color,.18 if kind=='glass' else .72,.25 if kind=='glass' else 0)
    nodes=material.node_tree.nodes
    links=material.node_tree.links
    if kind in ('stone','brick','plaster'):
        coordinates=nodes.new('ShaderNodeTexCoord')
        texture=nodes.new('ShaderNodeTexNoise')
        texture.inputs['Scale'].default_value=5 if kind=='plaster' else 1.6
        texture.inputs['Detail'].default_value=2
        links.new(coordinates.outputs['Object'],texture.inputs['Vector'])
        bump=nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value=.18
        bump.inputs['Distance'].default_value=.08 if kind!='plaster' else .025
        links.new(texture.outputs['Fac'],bump.inputs['Height'])
        links.new(bump.outputs['Normal'],nodes.get('Principled BSDF').inputs['Normal'])
        if kind=='brick':
            brick=nodes.new('ShaderNodeTexBrick')
            brick.inputs['Color1'].default_value=color
            brick.inputs['Color2'].default_value=tuple(c*.8 for c in color[:3])+(1,)
            brick.inputs['Mortar'].default_value=(.31,.29,.25,1)
            brick.inputs['Scale'].default_value=1.8
            brick.inputs['Mortar Size'].default_value=.025
            links.new(coordinates.outputs['UV'],brick.inputs['Vector'])
            links.new(brick.outputs['Color'],nodes.get('Principled BSDF').inputs['Base Color'])
    return material


def mesh(name, vertices, faces, material, owner=0):
    data=bpy.data.meshes.new(name)
    data.from_pydata(vertices,[],faces)
    data.update()
    # World-meter UVs keep brick courses horizontal on each facade instead of
    # projecting an XY texture down onto vertical walls.
    uv=data.uv_layers.new(name='Architectural meters')
    for polygon in data.polygons:
        normal=polygon.normal
        for loop_index in polygon.loop_indices:
            point=data.vertices[data.loops[loop_index].vertex_index].co
            if abs(normal.z)>.5:
                coordinate=(point.x,point.y)
            elif abs(normal.x)>abs(normal.y):
                coordinate=(point.y,point.z)
            else:
                coordinate=(point.x,point.z)
            uv.data[loop_index].uv=coordinate
    obj=bpy.data.objects.new(name,data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    obj['building_index']=owner
    obj['persistent_id']=name
    return obj


def prism(name, ring, z, height, material, owner=0):
    n=len(ring)
    vertices=[(x,y,z) for x,y in ring]+[(x,y,z+height) for x,y in ring]
    # Normalize winding so roof faces point up and facade normals point out.
    area=sum(ring[i][0]*ring[(i+1)%n][1]-ring[(i+1)%n][0]*ring[i][1] for i in range(n))
    if area<0:
        return prism(name,list(reversed(ring)),z,height,material,owner)
    faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]
    faces.extend((i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n))
    return mesh(name,vertices,faces,material,owner)


def polygon_prism(name, geometry, z, height, material, owner=0):
    key=hashlib.sha256(json.dumps(geometry,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    prepared=POLYGON_MESHES.get(key)
    if not prepared:
        return prism(name,ring_local(geometry,ORIGIN),z,height,material,owner)
    vertices=[];faces=[]
    for triangle in prepared['triangles']:
        ring=[(p[0]-ORIGIN[0],p[1]-ORIGIN[1]) for p in triangle]
        area=sum(ring[i][0]*ring[(i+1)%3][1]-ring[(i+1)%3][0]*ring[i][1] for i in range(3))
        if area<0: ring.reverse()
        index=len(vertices)
        vertices.extend((x,y,z) for x,y in ring)
        vertices.extend((x,y,z+height) for x,y in ring)
        faces.extend([(index+2,index+1,index),(index+3,index+4,index+5)])
    # Inner loops wind in the opposite direction: wall normals face the hole.
    for ring_index,source in enumerate(prepared['rings']):
        ring=[(p[0]-ORIGIN[0],p[1]-ORIGIN[1]) for p in source]
        area=sum(ring[i][0]*ring[(i+1)%len(ring)][1]-ring[(i+1)%len(ring)][0]*ring[i][1] for i in range(len(ring)))
        if (area<0 and ring_index==0) or (area>0 and ring_index>0):ring.reverse()
        for i,a in enumerate(ring):
            b=ring[(i+1)%len(ring)];index=len(vertices)
            vertices.extend([(a[0],a[1],z),(b[0],b[1],z),(b[0],b[1],z+height),(a[0],a[1],z+height)])
            faces.append((index,index+1,index+2,index+3))
    return mesh(name,vertices,faces,material,owner)


def beam(name,a,b,radius,material,owner=0):
    direction=Vector(b)-Vector(a)
    bpy.ops.mesh.primitive_cylinder_add(vertices=8,radius=radius,depth=direction.length,location=(Vector(a)+Vector(b))*.5)
    obj=bpy.context.object
    obj.name=name
    obj.rotation_euler=direction.to_track_quat('Z','Y').to_euler()
    obj.data.materials.append(material)
    obj['building_index']=owner
    obj['persistent_id']=name
    return obj


def windows(identifier,ring,height,floors,material,owner,base_elevation=0):
    vertices=[]
    faces=[]
    area=sum(ring[i][0]*ring[(i+1)%len(ring)][1]-ring[(i+1)%len(ring)][0]*ring[i][1] for i in range(len(ring)))
    if area<0: ring=list(reversed(ring))
    level=height/floors
    for i,a in enumerate(ring):
        b=ring[(i+1)%len(ring)]
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=math.hypot(dx,dy)
        if length<2: continue
        along=(dx/length,dy/length)
        outward=(along[1]*.045,-along[0]*.045)
        count=max(1,int(length/3.8))
        width=min(1.5,length/(count+1)*.55)
        win_height=min(1.75,level*.53)
        for floor in range(floors):
            z=base_elevation+floor*level+level*.26
            for window in range(count):
                offset=(window+1)*length/(count+1)
                x=a[0]+along[0]*offset+outward[0]
                y=a[1]+along[1]*offset+outward[1]
                base=len(vertices)
                vertices.extend([(x-along[0]*width*.5,y-along[1]*width*.5,z),(x+along[0]*width*.5,y+along[1]*width*.5,z),(x+along[0]*width*.5,y+along[1]*width*.5,z+win_height),(x-along[0]*width*.5,y-along[1]*width*.5,z+win_height)])
                faces.append((base,base+1,base+2,base+3))
    return mesh('windows:'+identifier,vertices,faces,material,owner)


def ring_local(geometry,origin):
    ring=geometry['coordinates'][0]
    if ring[0]==ring[-1]: ring=ring[:-1]
    return [(p[0]-origin[0],p[1]-origin[1]) for p in ring]


def scene_inventory(scene):
    records=[]
    for obj in sorted(scene.objects,key=lambda item:item.name):
        record={'name':obj.name,'type':obj.type,'persistent_id':obj.get('persistent_id',obj.name)}
        if obj.type=='MESH':
            obj.data.calc_loop_triangles()
            corners=[obj.matrix_world@vertex.co for vertex in obj.data.vertices]
            record.update(vertices=len(obj.data.vertices),unique_vertices=len({tuple(vertex.co) for vertex in obj.data.vertices}),polygons=len(obj.data.polygons),triangles=len(obj.data.loop_triangles),bounds_min=[min(p[i] for p in corners) for i in range(3)],bounds_max=[max(p[i] for p in corners) for i in range(3)])
        records.append(record)
    return {'objects':records,'counts':{'objects':len(records),'mesh_objects':sum(record['type']=='MESH' for record in records),'cameras':sum(record['type']=='CAMERA' for record in records),'lights':sum(record['type']=='LIGHT' for record in records),'vertices':sum(record.get('vertices',0) for record in records),'unique_vertices':sum(record.get('unique_vertices',0) for record in records),'polygons':sum(record.get('polygons',0) for record in records),'triangles':sum(record.get('triangles',0) for record in records)},'camera':{'name':scene.camera.name,'position':list(scene.camera.location),'rotation_euler':list(scene.camera.rotation_euler),'vertical_fov':math.degrees(scene.camera.data.angle_y),'lens_mm':scene.camera.data.lens,'forward':list((scene.camera.matrix_world.to_3x3()@Vector((0,0,-1))).normalized()),'up':list((scene.camera.matrix_world.to_3x3()@Vector((0,1,0))).normalized())},'blender_version':bpy.app.version_string}


def export_models(scene, manifest):
    """Export static geometry/camera with explicit constant PBR materials.

    Procedural shaders and the world sky remain in the native Blender scene;
    OBJ/glTF receive their constant base color, roughness and metallic values.
    """
    originals={obj:tuple(obj.data.materials) for obj in scene.objects if obj.type=='MESH'}
    replacements={}
    try:
        for obj,materials in originals.items():
            obj.data.materials.clear()
            for material in materials:
                if material not in replacements:
                    shader=material.node_tree.nodes.get('Principled BSDF') if material.use_nodes else None
                    roughness=shader.inputs['Roughness'].default_value if shader else .65
                    metallic=shader.inputs['Metallic'].default_value if shader else 0
                    replacements[material]=solid('export:'+material.name,material.diffuse_color,roughness,metallic)
                obj.data.materials.append(replacements[material])
        if manifest.get('obj'):
            bpy.ops.wm.obj_export(filepath=manifest['obj'],check_existing=False,export_animation=False,forward_axis='Y',up_axis='Z',export_uv=True,export_normals=True,export_materials=True,export_triangulated_mesh=False,export_selected_objects=False)
            obj_path=Path(manifest['obj'])
            obj_path.write_text('# GOLDEN CITY axes: forward=Y up=Z\n# GOLDEN CITY coordinates: local meters x=easting y=northing z=up\n'+obj_path.read_text(encoding='utf-8'),encoding='utf-8')
        if manifest.get('glb'):
            bpy.ops.export_scene.gltf(filepath=manifest['glb'],check_existing=False,export_format='GLB',export_cameras=True,export_lights=True,export_extras=True,export_yup=True,export_animations=False)
    finally:
        for obj,materials in originals.items():
            obj.data.materials.clear()
            for material in materials:obj.data.materials.append(material)


def main(manifest_path):
    global ORIGIN,POLYGON_MESHES
    manifest=json.loads(Path(manifest_path).read_text())
    ORIGIN=manifest['origin']
    POLYGON_MESHES=manifest.get('polygon_meshes',{})
    project=manifest['project']
    synthetic=project.get('synthetic') is True
    origin=manifest['origin']
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene=bpy.context.scene
    scene.render.engine='CYCLES'
    scene.cycles.device='CPU'
    scene.cycles.samples=manifest['samples']
    scene.cycles.seed=42
    scene.cycles.use_animated_seed=False
    scene.cycles.use_denoising=False  # Portable: this Blender build lacks OpenImageDenoise.
    scene.cycles.max_bounces=5
    scene.render.resolution_x=manifest['width']
    scene.render.resolution_y=manifest['height']
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGBA'
    scene.render.film_transparent=False
    scene.world.use_nodes=True
    background=scene.world.node_tree.nodes.get('Background')
    sky=scene.world.node_tree.nodes.new('ShaderNodeTexSky')
    sky.sky_type='NISHITA'
    sky.sun_elevation=math.radians(35)
    sky.sun_rotation=math.radians(125)
    sky.sun_disc=False
    scene.world.node_tree.links.new(sky.outputs['Color'],background.inputs['Color'])
    background.inputs['Strength'].default_value=.22
    scene.view_settings.view_transform='AgX'
    ring=ring_local(project['boundary'],origin)
    xs=[p[0] for p in ring];ys=[p[1] for p in ring]
    extent=max(max(xs)-min(xs),max(ys)-min(ys))
    ground=solid('schematic unmeasured ground',(0.26,.31,.23,1))
    pavement=solid('pavement',(0.37,.38,.36,1))
    road=solid('synthetic street',(0.065,.07,.08,1))
    glass=solid('window glazing',(0.027,.07,.11,1),.16,.55)
    rails=solid('balcony railing',(.10,.12,.14,1),.3,.6)
    roof=solid('roof coping',(.58,.57,.51,1))
    ground_size=extent*4
    prism('context:ground',[(-ground_size,-ground_size),(ground_size,-ground_size),(ground_size,ground_size),(-ground_size,ground_size)],-.15,.1,ground)
    polygon_prism('context:parcel',project['boundary'],-.05,.05,solid('parcel landscape',(.28,.34,.19,1)))
    if synthetic:
        street_y=min(ys)-10
        prism('context:street',[(-ground_size,street_y-6),(ground_size,street_y-6),(ground_size,street_y+6),(-ground_size,street_y+6)],-.02,.015,road)
    for i,feature in enumerate(project['buildings']):
        props=feature['properties'];identifier=str(props['id']);owner=i+1
        ring_b=ring_local(feature['geometry'],origin)
        height=float(props['height'])
        base_elevation=float(props.get('base_elevation',0))
        building=polygon_prism('building:'+identifier,feature['geometry'],base_elevation,height,facade(identifier,props),owner)
        building['building_id']=identifier
        windows(identifier,ring_b,height,int(props['floors']),glass,owner,base_elevation)
        polygon_prism('roof:'+identifier,feature['geometry'],base_elevation+height,.18,roof,owner)
        # Decorative roads/aprons are allowed only for an explicitly marked
        # synthetic fixture; never invent them for a real-data project.
        if synthetic:
            xmin=min(p[0] for p in ring_b);xmax=max(p[0] for p in ring_b);ymin=min(p[1] for p in ring_b);ymax=max(p[1] for p in ring_b)
            prism('context:apron:'+identifier,[(xmin-1.2,ymin-1.2),(xmax+1.2,ymin-1.2),(xmax+1.2,ymax+1.2),(xmin-1.2,ymax+1.2)],.002,.015,pavement,owner)
        for balcony in props.get('balconies',[]):
            bid=str(balcony.get('id','balcony'))
            footprint=ring_local(balcony['footprint'],origin)
            elevation=float(balcony['elevation']);thickness=float(balcony.get('thickness',.25))
            railing_height=float(balcony.get('railing_height',1.05))
            top=elevation+railing_height
            polygon_prism('balcony:'+identifier+':'+bid,balcony['footprint'],elevation-thickness,thickness,roof,owner)
            if railing_height<=0:continue
            for n,a in enumerate(footprint):
                b=footprint[(n+1)%len(footprint)]
                beam('rail:'+identifier+':'+bid+':'+str(n),(a[0],a[1],top),(b[0],b[1],top),.035,rails,owner)
                distance=math.hypot(b[0]-a[0],b[1]-a[1])
                count=max(1,int(distance/.9))
                for k in range(count):
                    ratio=k/count
                    x=a[0]+(b[0]-a[0])*ratio;y=a[1]+(b[1]-a[1])*ratio
                    beam('post:'+identifier+':'+bid+':'+str(n)+':'+str(k),(x,y,elevation),(x,y,top),.022,rails,owner)
        for slab in props.get('slabs',[]):
            footprint=ring_local(slab['footprint'],origin)
            top=float(slab['elevation']);thickness=float(slab.get('thickness',.2))
            polygon_prism('slab:'+identifier+':'+str(slab['id']),slab['footprint'],top-thickness,thickness,roof,owner)
    for obstacle in project.get('overhead_obstructions',[]):
        footprint=ring_local(obstacle['footprint'],origin)
        top=float(obstacle['elevation']);thickness=float(obstacle.get('thickness',.2))
        polygon_prism('context:overhead:'+str(obstacle['id']),obstacle['footprint'],top-thickness,thickness,roof)
    sun_data=bpy.data.lights.new('synthetic afternoon sun','SUN')
    sun_data.energy=2.8;sun_data.angle=math.radians(15)
    sun=bpy.data.objects.new('lighting:sun',sun_data);scene.collection.objects.link(sun)
    sun.rotation_euler=(math.radians(28),math.radians(-22),math.radians(-35))
    camera=manifest['camera']
    cam_data=bpy.data.cameras.new('saved camera:'+str(camera.get('id','custom')))
    cam=bpy.data.objects.new('camera:'+str(camera.get('id','custom')),cam_data);scene.collection.objects.link(cam)
    cam.location=camera['position']
    direction=Vector(camera['target'])-cam.location
    cam.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
    cam_data.type='PERSP'
    # Three.js PerspectiveCamera.fov is vertical; use the same explicit axis.
    cam_data.sensor_fit='VERTICAL'
    cam_data.lens=cam_data.sensor_height/(2*math.tan(math.radians(camera['fov'])/2))
    cam_data.clip_end=max(5000,extent*10);cam_data.clip_start=.05
    cam['saved_target']=camera['target'];scene.camera=cam
    scene.render.filepath=manifest['output']
    bpy.context.view_layer.update()
    if manifest.get('inventory'):
        Path(manifest['inventory']).write_text(json.dumps(scene_inventory(scene),ensure_ascii=False,indent=2))
    if manifest.get('blend'):
        bpy.context.preferences.filepaths.save_version=0
        bpy.ops.wm.save_as_mainfile(filepath=manifest['blend'])
    if manifest.get('obj') or manifest.get('glb'):
        export_models(scene,manifest)
    bpy.ops.render.render(write_still=True)
    if manifest.get('mask'):
        # An independent flat-label render has no shadows or light transport.
        # Background=black; object owner encoded in deterministic RGB. IDs are
        # stored on every building-owned mesh, including windows and balconies.
        for obj in scene.objects:
            if obj.type!='MESH': continue
            owner=int(obj.get('building_index',0))
            mat=bpy.data.materials.new('object ID:'+str(owner))
            mat.use_nodes=True
            nodes=mat.node_tree.nodes;nodes.clear()
            emission=nodes.new('ShaderNodeEmission')
            encoded=(owner*2654435761)&0xffffff
            emission.inputs['Color'].default_value=(((encoded>>16)&255)/255,((encoded>>8)&255)/255,(encoded&255)/255,1)
            emission.inputs['Strength'].default_value=1
            output=nodes.new('ShaderNodeOutputMaterial')
            mat.node_tree.links.new(emission.outputs['Emission'],output.inputs['Surface'])
            obj.data.materials.clear();obj.data.materials.append(mat)
        background=scene.world.node_tree.nodes.get('Background')
        for link in list(background.inputs['Color'].links):
            scene.world.node_tree.links.remove(link)
        background.inputs['Color'].default_value=(0,0,0,1)
        scene.world.node_tree.nodes.get('Background').inputs['Strength'].default_value=0
        scene.view_settings.view_transform='Raw'
        scene.view_settings.look='None'
        scene.view_settings.exposure=0;scene.view_settings.gamma=1
        scene.cycles.samples=1;scene.cycles.use_denoising=False;scene.cycles.filter_width=.01
        scene.render.dither_intensity=0
        scene.render.filepath=manifest['mask']
        bpy.ops.render.render(write_still=True)
    print('GOLDEN_CITY_RENDER_COMPLETE')


if __name__=='__main__':
    main(sys.argv[sys.argv.index('--')+1])
