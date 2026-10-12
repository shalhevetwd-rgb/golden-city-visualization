"""Visible-scene fingerprints and isolated facade material editing.

Snapshots compare scene data, not rendered pixels: indirect light may change
legitimately after a facade recolour. No geometry is changed by this module.
"""
import array
import hashlib
import json
import math
from pathlib import Path
import bpy
try:
    import numpy as _numpy
except ImportError:
    _numpy = None


def _plain(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Non-finite value in scene snapshot')
        return value
    if isinstance(value, dict) or hasattr(value, 'to_dict'):
        return {str(k): _plain(v) for k, v in dict(value).items()}
    try:
        return [_plain(v) for v in value]
    except TypeError:
        return str(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _properties(data):
    """Writable scalar RNA settings; dependencies are handled separately."""
    if data is None:
        return None
    result = {}
    for prop in data.bl_rna.properties:
        key = prop.identifier
        if key == 'rna_type' or prop.type in {'POINTER', 'COLLECTION'} or prop.is_readonly:
            continue
        try:
            result[key] = _plain(getattr(data, key))
        except (AttributeError, TypeError):
            continue
    return result


def _image(image):
    if image is None:
        return None
    packed = []
    if image.packed_file is not None:
        packed.append(hashlib.sha256(image.packed_file.data).hexdigest())
    for item in getattr(image, 'packed_files', []):
        packed.append(hashlib.sha256(item.packed_file.data).hexdigest())
    files = []
    filepath = bpy.path.abspath(image.filepath, library=image.library)
    if filepath and image.source not in {'GENERATED', 'VIEWER'}:
        path = Path(filepath)
        if path.is_file():
            files.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    pixel_hash = None
    if image.is_dirty or image.source == 'GENERATED':
        values = array.array('f', [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        if any(not math.isfinite(v) for v in values):
            raise ValueError('Non-finite texture pixels')
        pixel_hash = hashlib.sha256(values.tobytes()).hexdigest()
    return {'name': image.name, 'settings': _properties(image),
            'colorspace': _properties(image.colorspace_settings),
            'size': list(image.size), 'packed': packed, 'files': files,
            'dirty_pixel_sha256': pixel_hash}


def _node_tree(tree, active=None):
    if tree is None:
        return None
    active = set() if active is None else active
    if tree.as_pointer() in active:
        raise ValueError('Recursive shader node group')
    active = active | {tree.as_pointer()}
    nodes = []
    for node in sorted(tree.nodes, key=lambda n: n.name):
        record = {'name': node.name, 'type': node.bl_idname,
                  'settings': _properties(node),
                  'inputs': [[i.identifier, _plain(i.default_value)] for i in node.inputs
                             if hasattr(i, 'default_value')],
                  'outputs': [[o.identifier, _plain(o.default_value)] for o in node.outputs
                              if hasattr(o, 'default_value')]}
        ramp = getattr(node, 'color_ramp', None)
        if ramp is not None:
            record['color_ramp'] = {'settings': _properties(ramp),
                                    'elements': [[e.position, list(e.color)] for e in ramp.elements]}
        curve = getattr(node, 'mapping', None)
        if curve is not None and hasattr(curve, 'curves'):
            record['curve_mapping'] = {'settings': _properties(curve),
                                      'curves': [[{'location': list(p.location), 'handle_type': p.handle_type}
                                                  for p in c.points] for c in curve.curves]}
        for key in ('texture_mapping', 'color_mapping', 'image_user'):
            if hasattr(node, key):
                record[key] = _properties(getattr(node, key))
        if hasattr(node, 'image'):
            record['image'] = _image(node.image)
        if hasattr(node, 'node_tree'):
            record['group'] = _node_tree(node.node_tree, active)
        dependencies = {}
        for prop in node.bl_rna.properties:
            if prop.type != 'POINTER' or prop.identifier in {'rna_type', 'image', 'node_tree'}:
                continue
            value = getattr(node, prop.identifier, None)
            if isinstance(value, bpy.types.ID):
                dependency = {'name': value.name, 'type': value.bl_rna.identifier,
                              'library': value.library.filepath if value.library else None}
                if isinstance(value, bpy.types.Text):
                    dependency['sha256'] = hashlib.sha256(value.as_string().encode()).hexdigest()
                dependencies[prop.identifier] = dependency
        record['dependencies'] = dependencies
        filepath = getattr(node, 'filepath', None)
        if filepath:
            path = Path(bpy.path.abspath(filepath))
            record['external_file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        nodes.append(record)
    links = sorted([link.from_node.name, link.from_socket.identifier,
                    link.to_node.name, link.to_socket.identifier] for link in tree.links)
    return {'nodes': nodes, 'links': links}


def shader(material):
    if material is None:
        return None
    return {'name': material.name, 'settings': _properties(material),
            'metadata': {key: _plain(material[key]) for key in sorted(material.keys())},
            'diffuse': list(material.diffuse_color) if hasattr(material, 'diffuse_color') else None,
            'node_tree': _node_tree(material.node_tree) if material.use_nodes else None}


def _channel(collection, field, typecode, components=1):
    """Copy an entire RNA channel in C and hash every element, not a sample."""
    values = array.array(typecode, [0]) * (len(collection) * components)
    if values:
        collection.foreach_get(field, values)
    if typecode in {'f', 'd'}:
        finite = bool(_numpy.isfinite(_numpy.asarray(values)).all()) if _numpy is not None else all(math.isfinite(v) for v in values)
        if not finite:
            raise ValueError('Non-finite mesh channel: ' + field)
    # Blender mesh channels are float32/int32/bool; pin their exact bits.
    # Standardise byte order so persisted fingerprints are portable.
    import sys
    if sys.byteorder != 'little' and values.itemsize > 1:
        values.byteswap()
    return {'count': len(collection), 'components': components,
            'type': typecode, 'sha256': hashlib.sha256(values.tobytes()).hexdigest()}


def _mesh(mesh):
    typed_attributes = {
        'FLOAT': ('value', 'f', 1), 'INT': ('value', 'i', 1),
        'FLOAT_VECTOR': ('vector', 'f', 3), 'FLOAT_COLOR': ('color', 'f', 4),
        'BYTE_COLOR': ('color', 'f', 4), 'BOOLEAN': ('value', 'b', 1),
        'FLOAT2': ('vector', 'f', 2), 'INT8': ('value', 'b', 1),
        'INT32_2D': ('value', 'i', 2), 'QUATERNION': ('value', 'f', 4),
        'FLOAT4X4': ('value', 'f', 16),
    }
    attributes = []
    for attr in sorted(mesh.attributes, key=lambda a: a.name):
        if attr.data_type in typed_attributes:
            values = _channel(attr.data, *typed_attributes[attr.data_type])
        else:
            # String/future attribute formats retain a complete fallback;
            # unknown formats are never silently dropped from the audit.
            values = [{key: _plain(getattr(item, key))
                       for key in ('value', 'vector', 'color', 'uv') if hasattr(item, key)}
                      for item in attr.data]
            if attr.data and not any(values):
                raise ValueError('Unsupported attribute storage: ' + attr.data_type)
        attributes.append({'name': attr.name, 'domain': attr.domain,
                           'data_type': attr.data_type, 'values': values})
    return {'fingerprint_format': 'complete-rna-channel-sha256-v2',
            'vertices': _channel(mesh.vertices, 'co', 'f', 3),
            'edges': _channel(mesh.edges, 'vertices', 'i', 2),
            # Loops + starts/totals preserve every original polygon vertex
            # and ordering, including arbitrary non-triangular faces.
            'loop_vertices': _channel(mesh.loops, 'vertex_index', 'i'),
            'polygon_loop_start': _channel(mesh.polygons, 'loop_start', 'i'),
            'polygon_loop_total': _channel(mesh.polygons, 'loop_total', 'i'),
            'material_indices': _channel(mesh.polygons, 'material_index', 'i'),
            'smooth': _channel(mesh.polygons, 'use_smooth', 'b'),
            'uv_layers': [{'name': u.name, 'active_render': u.active_render,
                           'uv': _channel(u.data, 'uv', 'f', 2)} for u in mesh.uv_layers],
            'attributes': attributes,
            'corner_normals': _channel(mesh.corner_normals, 'vector', 'f', 3)}


def _modifier(modifier):
    result = {'name': modifier.name, 'type': modifier.type, 'settings': _properties(modifier)}
    for prop in modifier.bl_rna.properties:
        if prop.type == 'POINTER' and prop.identifier != 'rna_type':
            value = getattr(modifier, prop.identifier, None)
            result[prop.identifier] = getattr(value, 'name', None)
    if modifier.type == 'NODES':
        result['node_group'] = _node_tree(modifier.node_group)
        result['socket_values'] = {key: _plain(modifier[key]) for key in sorted(modifier.keys())}
    return result


def _curve(data):
    return {'settings': _properties(data),
            'splines': [{'settings': _properties(spline),
                         'points': [_properties(point) for point in spline.points],
                         'bezier_points': [_properties(point) for point in spline.bezier_points]}
                        for spline in getattr(data, 'splines', [])],
            'bevel_object': getattr(getattr(data, 'bevel_object', None), 'name', None),
            'taper_object': getattr(getattr(data, 'taper_object', None), 'name', None)}


def snapshot():
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    out = {}
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        geometry = None
        if obj.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            try:
                geometry = _digest({'raw': _mesh(obj.data) if obj.type == 'MESH' else _curve(obj.data), 'evaluated': _mesh(mesh),
                                    'modifiers': [_modifier(m) for m in obj.modifiers],
                                    'vertex_groups': [[g.name, g.lock_weight] for g in obj.vertex_groups]})
            finally:
                evaluated.to_mesh_clear()
        visibility = {key: getattr(obj, key) for key in
                      ('hide_render', 'hide_viewport', 'visible_camera', 'visible_diffuse',
                       'visible_glossy', 'visible_transmission', 'visible_volume_scatter',
                       'visible_shadow', 'is_holdout', 'is_shadow_catcher') if hasattr(obj, key)}
        try:
            visibility['view_layer_hidden'] = obj.hide_get()
        except RuntimeError:
            visibility['view_layer_hidden'] = None
        out[obj.name] = {'owner': obj.get('owner', 'context'), 'role': obj.get('role'),
                         'metadata': {key: _plain(obj[key]) for key in sorted(obj.keys())},
                         'geometry': geometry, 'transform': [list(r) for r in obj.matrix_world],
                         'visibility': visibility, 'type': obj.type,
                         'collections': sorted(c.name for c in obj.users_collection),
                         'instance_type': obj.instance_type,
                         'instance_collection': obj.instance_collection.name if obj.instance_collection else None,
                         'materials': [{'link': slot.link, 'shader': shader(slot.material)}
                                       for slot in obj.material_slots],
                         'light': {'settings': _properties(obj.data),
                                   'shader': _node_tree(obj.data.node_tree) if obj.data.use_nodes else None}
                                  if obj.type == 'LIGHT' else None,
                         'camera': {'settings': _properties(obj.data), 'dof': _properties(obj.data.dof)}
                                   if obj.type == 'CAMERA' else None}
    out['__world__'] = {'owner': 'context', 'geometry': None, 'shader': shader(scene.world)}
    out['__scene__'] = {'owner': 'context', 'geometry': None,
                         'camera': scene.camera.name if scene.camera else None,
                         'frame': scene.frame_current, 'render': _properties(scene.render),
                         'image_settings': _properties(scene.render.image_settings),
                         'cycles': _properties(scene.cycles),
                         'view_settings': _properties(scene.view_settings),
                         'display_settings': _properties(scene.display_settings),
                         'unit_settings': _properties(scene.unit_settings),
                         'root_collection': _properties(scene.collection),
                         'collections': {c.name: _properties(c) for c in bpy.data.collections},
                         'view_layers': [{
                             'settings': _properties(layer),
                             'material_override': layer.material_override.name if layer.material_override else None,
                             'collections': _layer_collections(layer.layer_collection),
                         } for layer in scene.view_layers]}
    return out


def _layer_collections(collection):
    return {'name': collection.name, 'exclude': collection.exclude,
            'hide_viewport': collection.hide_viewport, 'holdout': collection.holdout,
            'indirect_only': collection.indirect_only,
            'children': [_layer_collections(child) for child in collection.children]}


def _validate_color(color):
    if not isinstance(color, (list, tuple)) or len(color) != 3:
        raise ValueError('Facade color must contain three linear RGB numbers')
    if any(not _unit_number(c) for c in color):
        raise ValueError('Facade color must contain finite linear RGB numbers in [0,1]')
    return tuple(color)


def _unit_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except (ValueError, OverflowError):
        return False


def _recolor(material, color, suffix):
    copy = material.copy()
    copy.name = 'Targeted facade finish ' + suffix
    scale = material.get('facade_tint_scale', 1)
    color = tuple(value * scale for value in color)
    copy.diffuse_color = (*color, 1)
    bsdf = copy.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = (*color, 1)
    links = list(bsdf.inputs['Base Color'].links)
    node = links[0].from_node if len(links) == 1 else None
    # A palette may also drive roughness or displacement. Recolour its output
    # only when that colour output serves this Base Color alone.
    shared_output = bool(links) and any(
        link.from_socket == links[0].from_socket and link not in links
        for link in copy.node_tree.links)
    if node is not None and node.type == 'VALTORGB' and not shared_output:
        for index, element in enumerate(node.color_ramp.elements):
            element.color = (*(v * (.86 if index == 0 else 1) for v in color), element.color[3])
    elif node is not None and node.type == 'TEX_BRICK' and not shared_output:
        for key, factor in (('Color1', .8), ('Color2', 1), ('Mortar', .45)):
            node.inputs[key].default_value = (*(v * factor for v in color), 1)
    elif links:
        # Updating a default value has no visible effect on a linked image or
        # group shader. Tint its existing output without editing shared groups.
        source = links[0].from_socket
        for link in links:
            copy.node_tree.links.remove(link)
        tint = copy.node_tree.nodes.new('ShaderNodeMixRGB')
        tint.name = 'Targeted facade tint'
        tint.blend_type = 'MULTIPLY'
        tint.inputs[0].default_value = 1
        tint.inputs[2].default_value = (*color, 1)
        copy.node_tree.links.new(source, tint.inputs[1])
        copy.node_tree.links.new(tint.outputs[0], bsdf.inputs['Base Color'])
    return copy


def _check_material(material):
    if material is None or not material.use_nodes or material.node_tree.nodes.get('Principled BSDF') is None:
        raise ValueError('Every target facade material requires a Principled BSDF')
    scale = material.get('facade_tint_scale', 1)
    if not _unit_number(scale):
        raise ValueError('Facade tint scale must be a finite number in [0,1]')
    bsdf = material.node_tree.nodes['Principled BSDF']
    if not any(link.from_node == bsdf and link.to_node.type == 'OUTPUT_MATERIAL'
               and link.to_node.is_active_output and link.to_socket.name == 'Surface'
               for link in material.node_tree.links):
        raise ValueError('Facade editing requires Principled BSDF connected directly to the active material surface')


def apply_facade_color(target, color):
    """Compatibility helper: recolour only the first slot on one mesh object."""
    color = _validate_color(color)
    if target.type != 'MESH' or not target.material_slots:
        raise ValueError('Target facade mesh has no material')
    slot = target.material_slots[0]
    _check_material(slot.material)
    copy = _recolor(slot.material, color, str(target.get('building_id', target.name)))
    slot.link = 'OBJECT'
    slot.material = copy


def apply_building_facade_color(target_id, color):
    """Recolour facade body/skin only; object-linked slots isolate shared meshes."""
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError('Target building id must be a nonempty string')
    color = _validate_color(color)
    targets = sorted([o for o in bpy.data.objects if o.type == 'MESH'
                      and o.get('owner') == target_id and o.get('role') == 'facade'], key=lambda o: o.name)
    if not targets:
        raise ValueError('No facade-tagged meshes for target building')
    materials = {}
    for obj in targets:
        if not obj.material_slots:
            raise ValueError('Target facade mesh has no material')
        for slot in obj.material_slots:
            _check_material(slot.material)
            materials[slot.material.as_pointer()] = slot.material
    copies = {key: _recolor(mat, color, target_id + ' ' + mat.name) for key, mat in materials.items()}
    for obj in targets:
        for slot in obj.material_slots:
            original = slot.material
            slot.link = 'OBJECT'
            slot.material = copies[original.as_pointer()]
    return [obj.name for obj in targets]
