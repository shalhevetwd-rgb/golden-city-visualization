"""Deterministic botanical detail, confined to the synthetic test parcel.

Every curved leaf attaches to a visible twig through a petiole.  Geometry is
generated here, without external plant assets. Source meshes stay unchanged.
"""
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from render_context import load_context

PARCEL_MIN, PARCEL_MAX = -50.0, 50.0


def geometry_digest(obj):
    record = {
        'vertices': [list(vertex.co) for vertex in obj.data.vertices],
        'faces': [list(face.vertices) for face in obj.data.polygons],
        'matrix': [list(row) for row in obj.matrix_world],
    }
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def bounds(obj):
    points = [obj.matrix_world @ Vector(point) for point in obj.bound_box]
    return (
        tuple(min(point[axis] for point in points) for axis in range(3)),
        tuple(max(point[axis] for point in points) for axis in range(3)),
    )


class PlantMesh:
    """Accumulate one compact mesh rather than thousands of Blender objects."""

    def __init__(self, materials):
        self.vertices, self.faces, self.material_ids = [], [], []
        self.materials = materials
        self.leaf_count = 0

    def face(self, indices, material):
        self.faces.append(tuple(indices))
        self.material_ids.append(material)

    def branch(self, start, end, radius, end_radius=None, sides=6):
        start, end = Vector(start), Vector(end)
        delta = end - start
        if delta.length < 0.00001:
            return
        axis = delta.normalized()
        reference = Vector((0, 0, 1)) if abs(axis.z) < 0.95 else Vector((1, 0, 0))
        u = axis.cross(reference).normalized()
        v = axis.cross(u).normalized()
        offset = len(self.vertices)
        end_radius = radius * 0.60 if end_radius is None else end_radius
        for point, ring_radius in ((start, radius), (end, end_radius)):
            for index in range(sides):
                angle = index * math.tau / sides
                self.vertices.append(tuple(point + (u * math.cos(angle) + v * math.sin(angle)) * ring_radius))
        for index in range(sides):
            next_index = (index + 1) % sides
            self.face((offset + index, offset + next_index, offset + sides + next_index, offset + sides + index), 0)
        self.face(tuple(offset + index for index in reversed(range(sides))), 0)
        self.face(tuple(offset + sides + index for index in range(sides)), 0)

    def leaf(self, anchor, outward, length, width, material, rng):
        """Seven-vertex curved lamina, connected by a narrow petiole."""
        outward = Vector(outward).normalized()
        lateral = outward.cross(Vector((0, 0, 1)))
        if lateral.length < 0.01:
            lateral = Vector((1, 0, 0))
        lateral.normalize()
        normal = lateral.cross(outward).normalized()
        roll = rng.uniform(-0.65, 0.65)
        lateral, normal = (
            lateral * math.cos(roll) + normal * math.sin(roll),
            normal * math.cos(roll) - lateral * math.sin(roll),
        )
        anchor = Vector(anchor)
        base = anchor + outward * length * 0.13
        stem_width = length * 0.007
        offset = len(self.vertices)
        self.vertices.extend(tuple(point) for point in (
            anchor - lateral * stem_width, anchor + lateral * stem_width,
            base + lateral * stem_width, base - lateral * stem_width,
        ))
        self.face((offset, offset + 1, offset + 2, offset + 3), 0)
        offset = len(self.vertices)
        self.vertices.extend(tuple(point) for point in (
            base,
            base + outward * length * 0.36 - lateral * width * 0.50 - normal * length * 0.035,
            base + outward * length * 0.73 - lateral * width * 0.32 - normal * length * 0.015,
            base + outward * length - normal * length * 0.11,
            base + outward * length * 0.73 + lateral * width * 0.32 - normal * length * 0.015,
            base + outward * length * 0.36 + lateral * width * 0.50 - normal * length * 0.035,
            base + outward * length * 0.47 + normal * length * 0.065,
        ))
        for index in range(6):
            self.face((offset + index, offset + (index + 1) % 6, offset + 6), material)
        self.leaf_count += 1

    def finish(self, name, root, role):
        mesh = bpy.data.meshes.new(name + ':mesh')
        root = Vector(root)
        mesh.from_pydata([tuple(Vector(point) - root) for point in self.vertices], [], self.faces)
        for material in self.materials:
            mesh.materials.append(material)
        for polygon, material in zip(mesh.polygons, self.material_ids):
            polygon.material_index = material
            polygon.use_smooth = True
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.location = root
        obj['owner'] = 'context'
        obj['classification'] = 'synthetic_design_proposal'
        obj['asset_role'] = 'vegetation'
        obj['vegetation_role'] = role
        obj['procedural_vegetation'] = True
        obj['asset_provenance'] = 'deterministic_original_procedural_geometry_no_external_assets'
        obj['leaf_count'] = self.leaf_count
        obj['root_xy'] = [root.x, root.y]
        bpy.context.view_layer.update()
        return obj


def grow_tree(index, root_xy, materials, seed):
    rng = random.Random(seed + index * 7919)
    mesh = PlantMesh(materials)
    x, y = root_xy
    height = 6.45 + (index % 3) * 0.33 + rng.uniform(-0.12, 0.12)
    base = Vector((x, y, 0.0))
    trunk_nodes = [base]
    for level in range(1, 9):
        fraction = level / 8
        trunk_nodes.append(base + Vector((0.14 * math.sin(fraction * 2.7), 0.09 * math.sin(fraction * 3.8), height * 0.92 * fraction)))
        mesh.branch(trunk_nodes[-2], trunk_nodes[-1], 0.155 * (1 - (level - 1) / 10), 0.155 * (1 - level / 10), sides=9)
    for rib in range(5):
        angle = rib * math.tau / 5
        mesh.branch(base + Vector((math.cos(angle) * 0.32, math.sin(angle) * 0.32, 0.012)), trunk_nodes[1], 0.045, 0.065)
    for branch_index in range(12):
        angle = branch_index * 2.399963 + rng.uniform(-0.16, 0.16)
        level = 0.41 + (branch_index / 11) * 0.39
        origin = base + Vector((0.11, 0.04, height * level))
        radius = 1.45 + 0.44 * math.sin((1 - level) * math.pi) + rng.uniform(-0.15, 0.12)
        radial = Vector((math.cos(angle), math.sin(angle), 0))
        side = Vector((-math.sin(angle), math.cos(angle), 0))
        elbow = origin + radial * radius * 0.54 + Vector((0, 0, 0.37))
        terminal = origin + radial * radius + Vector((0, 0, 0.60 + rng.uniform(-0.12, 0.20)))
        mesh.branch(origin, elbow, 0.050 - branch_index * 0.0018, 0.027)
        mesh.branch(elbow, terminal, 0.027, 0.013)
        for lateral_index in range(5):
            t = 0.34 + lateral_index * 0.145
            joint = elbow.lerp(terminal, max(0.0, (t - 0.54) / 0.46)) if t >= 0.54 else origin.lerp(elbow, t / 0.54)
            handed = -1 if lateral_index % 2 else 1
            direction = (radial * 0.52 + side * handed * 0.75 + Vector((0, 0, rng.uniform(0.14, 0.56)))).normalized()
            branch_tip = joint + direction * rng.uniform(0.65, 1.05)
            mesh.branch(joint, branch_tip, 0.016, 0.006, sides=5)
            for shoot_index in range(3):
                shoot_start = joint.lerp(branch_tip, 0.40 + shoot_index * 0.20)
                shoot_direction = (direction + radial * rng.uniform(-0.18, 0.40) + side * (shoot_index - 1) * 0.65 + Vector((0, 0, rng.uniform(-0.10, 0.47)))).normalized()
                shoot_end = shoot_start + shoot_direction * rng.uniform(0.48, 0.82)
                mesh.branch(shoot_start, shoot_end, 0.0055, 0.0014, sides=4)
                leaf_side = shoot_direction.cross(Vector((0, 0, 1))).normalized()
                for node in range(9):
                    anchor = shoot_start.lerp(shoot_end, 0.12 + node * 0.098)
                    for handed in (-1, 1):
                        outward = (shoot_direction * 0.43 + leaf_side * handed * 0.84 + Vector((0, 0, rng.uniform(-0.26, 0.26)))).normalized()
                        length = rng.uniform(0.29, 0.43) * (0.89 if node > 6 else 1.0)
                        tone = rng.choices((1, 2, 3), weights=(3, 5, 2))[0]
                        mesh.leaf(anchor, outward, length, length * rng.uniform(0.48, 0.66), tone, rng)
    obj = mesh.finish('landscape:tree:' + str(index), base, 'branched_broadleaf_tree')
    obj['procedural_seed'] = seed + index * 7919
    return obj


def exclusion_rectangles():
    rectangles = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if obj.name.startswith(('building:', 'context:apron:', 'context:street', 'landscape:path:', 'bench:')):
            low, high = bounds(obj)
            margin = 0.28 if obj.name.startswith(('landscape:path:', 'bench:')) else 0.40
            rectangles.append((low[0] - margin, low[1] - margin, high[0] + margin, high[1] + margin))
    return rectangles


def clear_ground(x, y, radius, exclusions):
    if not (PARCEL_MIN + radius <= x <= PARCEL_MAX - radius and PARCEL_MIN + radius <= y <= PARCEL_MAX - radius):
        return False
    for left, bottom, right, top in exclusions:
        nearest_x = min(max(x, left), right)
        nearest_y = min(max(y, bottom), top)
        if (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius ** 2:
            return False
    return True


def inside_bed(x, y, radius, rectangle):
    left, bottom, right, top = rectangle
    return left + radius <= x <= right - radius and bottom + radius <= y <= top - radius


def grow_shrub(index, position, materials, seed):
    rng = random.Random(seed + 100003 + index * 3571)
    mesh = PlantMesh(materials)
    base = Vector((*position, 0.015))
    for stem in range(14):
        angle = stem * 2.399963
        radius = rng.uniform(0.28, 0.50)
        direction = Vector((math.cos(angle), math.sin(angle), 0))
        endpoint = base + direction * radius + Vector((0, 0, rng.uniform(0.43, 0.80)))
        mesh.branch(base, endpoint, 0.010, 0.002, sides=4)
        for shoot in range(3):
            origin = base.lerp(endpoint, 0.42 + shoot * 0.22)
            lateral = Vector((-direction.y, direction.x, 0))
            shoot_tip = origin + direction * 0.18 + lateral * rng.uniform(-0.22, 0.22) + Vector((0, 0, 0.13))
            mesh.branch(origin, shoot_tip, 0.0028, 0.0006, sides=3)
            for node in range(7):
                anchor = origin.lerp(shoot_tip, 0.13 + node * 0.13)
                for handed in (-1, 1):
                    outward = direction * 0.30 + lateral * handed * 0.70 + Vector((0, 0, rng.uniform(-0.05, 0.40)))
                    length = rng.uniform(0.075, 0.13)
                    mesh.leaf(anchor, outward, length, length * 0.58, rng.choice((1, 2, 2, 3)), rng)
    return mesh.finish('landscape:vegetation:shrub:' + str(index), base, 'branched_low_shrub')


def grow_grass(index, center, rectangle, materials, seed, exclusions, tuft_count=320):
    rng = random.Random(seed + 200003 + index * 2371)
    mesh = PlantMesh(materials)
    accepted = 0
    for _ in range(tuft_count * 8):
        x = rng.uniform(rectangle[0] + 0.18, rectangle[2] - 0.18)
        y = rng.uniform(rectangle[1] + 0.18, rectangle[3] - 0.18)
        radial = math.hypot(x - center[0], y - center[1])
        angle = math.atan2(y - center[1], x - center[0])
        if radial < 0.38 or not clear_ground(x, y, 0.18, exclusions):
            continue
        accepted += 1
        for blade in range(5):
            turn = angle + blade * 2.399963 + rng.uniform(-0.45, 0.45)
            direction = Vector((math.cos(turn), math.sin(turn), 0))
            lateral = Vector((-direction.y, direction.x, 0))
            base = Vector((x, y, 0.018)) + direction * rng.uniform(0.0, 0.036)
            height = rng.uniform(0.065, 0.18)
            width = rng.uniform(0.007, 0.017)
            shoulder = base + direction * height * 0.15 + Vector((0, 0, height * 0.55))
            tip = base + direction * height * 0.63 + Vector((0, 0, height * 0.80))
            offset = len(mesh.vertices)
            mesh.vertices.extend(tuple(point) for point in (base - lateral * width, base + lateral * width, shoulder - lateral * width * 0.5, shoulder + lateral * width * 0.5, tip))
            material = rng.choice((0, 0, 1, 2))
            mesh.face((offset, offset + 1, offset + 3, offset + 2), material)
            mesh.face((offset + 2, offset + 3, offset + 4), material)
        if accepted == tuft_count:
            break
    obj = mesh.finish('landscape:vegetation:grass:' + str(index), (0, 0, 0), 'curved_grass_blades')
    obj['tuft_count'] = accepted
    return obj


def soil_material():
    name = 'Procedural planting soil'
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (0.075, 0.052, 0.028, 1)
    nodes = material.node_tree.nodes
    bsdf = nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = material.diffuse_color
    bsdf.inputs['Roughness'].default_value = 0.92
    # Repeated runs reuse the original procedural nodes.
    if not nodes.get('Soil granularity'):
        noise = nodes.new('ShaderNodeTexNoise')
        noise.name = 'Soil granularity'
        noise.inputs['Scale'].default_value = 95
        noise.inputs['Detail'].default_value = 2
        bump = nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value = 0.22
        bump.inputs['Distance'].default_value = 0.004
        material.node_tree.links.new(noise.outputs['Fac'], bump.inputs['Height'])
        material.node_tree.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    return material


def build_bed_surface(bed, material, exclusions):
    """Split a rectangle at obstruction boundaries, retaining only clear cells."""
    left, bottom, right, top = bed['rectangle']
    assert PARCEL_MIN <= left < right <= PARCEL_MAX and PARCEL_MIN <= bottom < top <= PARCEL_MAX
    xcuts, ycuts = {left, right}, {bottom, top}
    for ex_left, ex_bottom, ex_right, ex_top in exclusions:
        if ex_right <= left or ex_left >= right or ex_top <= bottom or ex_bottom >= top:
            continue
        xcuts.update((max(left, ex_left), min(right, ex_right)))
        ycuts.update((max(bottom, ex_bottom), min(top, ex_top)))
    xcuts, ycuts = sorted(xcuts), sorted(ycuts)
    mesh = PlantMesh([material])
    area = 0.0
    for x0, x1 in zip(xcuts, xcuts[1:]):
        for y0, y1 in zip(ycuts, ycuts[1:]):
            if not clear_ground((x0 + x1) / 2, (y0 + y1) / 2, 0, exclusions):
                continue
            offset = len(mesh.vertices)
            mesh.vertices.extend(((x0, y0, 0.016), (x1, y0, 0.016), (x1, y1, 0.016), (x0, y1, 0.016)))
            mesh.face((offset, offset + 1, offset + 2, offset + 3), 0)
            area += (x1 - x0) * (y1 - y0)
    assert mesh.faces, 'Planting bed is completely obstructed: ' + bed['id']
    obj = mesh.finish('landscape:vegetation:bed:' + bed['id'], (0, 0, 0), 'procedural_soil_bed')
    obj['asset_role'] = 'landscape_bed'
    obj['planting_bed_id'] = bed['id']
    obj['clear_surface_area_m2'] = area
    obj['source_paths_preserved'] = True
    return obj


def assert_bed_bounds(obj, rectangle):
    low, high = bounds(obj)
    assert inside_bed(low[0], low[1], 0, rectangle) and inside_bed(high[0], high[1], 0, rectangle), 'Groundcover exceeds its configured planting bed'


def main():
    cfg, out = load_context()
    assert cfg.get('synthetic') is True, 'Procedural landscape is authorized only for this synthetic exercise'
    bpy.ops.wm.open_mainfile(filepath=str(out / 'model.blend'))
    source = {obj.name: geometry_digest(obj) for obj in bpy.data.objects if obj.type == 'MESH' and obj.get('classification') == 'source_geometry_unchanged'}
    for obj in list(bpy.data.objects):
        if obj.name.startswith(('landscape:tree:', 'landscape:vegetation:')) or obj.get('procedural_vegetation') is True:
            assert obj.get('classification') != 'source_geometry_unchanged', 'Cannot replace a source mesh'
            bpy.data.objects.remove(obj, do_unlink=True)
    leaves = [bpy.data.materials['Leaf tone ' + str(index)] for index in range(3)]
    for material in leaves:
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Roughness'].default_value = 0.62
        for name, value in (('Subsurface Weight', 0.035), ('Transmission Weight', 0.045), ('IOR', 1.38), ('Specular IOR Level', 0.28)):
            if name in bsdf.inputs:
                bsdf.inputs[name].default_value = value
        material.use_backface_culling = False
    materials = [bpy.data.materials['Tree bark'], *leaves]
    grass_materials = [bpy.data.materials['Landscape grass']]
    for index, color in enumerate(((0.15, 0.27, 0.075, 1), (0.22, 0.32, 0.09, 1))):
        name = 'Procedural grass blade ' + str(index)
        material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        material.use_nodes = True
        material.diffuse_color = color
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = 0.77
        grass_materials.append(material)
    exclusions = exclusion_rectangles()
    created, tree_records = [], []
    seed, shrub_index = cfg['seed'], 0
    beds = cfg['landscape']['planting_beds']
    soil = soil_material()
    for bed in beds:
        created.append(build_bed_surface(bed, soil, exclusions))
    tree_bed_ids = set()
    for index, root in enumerate(cfg['landscape']['tree_points']):
        assert clear_ground(*root, 0.36, exclusions), 'Configured tree root intersects a building, path or parcel edge'
        tree = grow_tree(index, root, materials, seed)
        low, high = bounds(tree)
        assert low[0] >= PARCEL_MIN and low[1] >= PARCEL_MIN and high[0] <= PARCEL_MAX and high[1] <= PARCEL_MAX, 'Tree crown extends beyond the synthetic parcel'
        assert tuple(tree.location[:2]) == tuple(root), 'Configured tree XY changed'
        created.append(tree)
        tree_records.append({'name': tree.name, 'root_xy': list(root), 'leaves': tree['leaf_count'], 'bounds': [low, high]})
        matching_beds = [bed for bed in beds if inside_bed(*root, 0.01, bed['rectangle'])]
        assert len(matching_beds) == 1, 'Each tree requires one explicit synthetic planting bed'
        bed = matching_beds[0]
        tree_bed_ids.add(bed['id'])
        rectangle = bed['rectangle']
        rng, placed = random.Random(seed + index * 1237), 0
        for candidate in range(12):
            angle = candidate * math.pi + rng.uniform(-0.10, 0.10)
            position = (root[0] + math.cos(angle) * 0.84, root[1] + math.sin(angle) * 0.84)
            if inside_bed(*position, 0.88, rectangle) and clear_ground(*position, 0.88, exclusions):
                shrub = grow_shrub(shrub_index, position, materials, seed)
                shrub['planting_bed_id'] = bed['id']
                assert_bed_bounds(shrub, rectangle)
                created.append(shrub)
                shrub_index += 1
                placed += 1
            if placed == 2:
                break
        grass = grow_grass(index, root, rectangle, grass_materials, seed, exclusions)
        grass['planting_bed_id'] = bed['id']
        assert_bed_bounds(grass, rectangle)
        created.append(grass)
    extra_bed_records = []
    for bed_index, bed in enumerate(bed for bed in beds if bed['id'] not in tree_bed_ids):
        rectangle = bed['rectangle']
        rng = random.Random(seed + 300007 + bed_index * 6841)
        positions = []
        for _ in range(2400):
            position = (
                rng.uniform(rectangle[0] + 0.88, rectangle[2] - 0.88),
                rng.uniform(rectangle[1] + 0.88, rectangle[3] - 0.88),
            )
            if not clear_ground(*position, 0.88, exclusions):
                continue
            if any(math.dist(position, previous) < 1.35 for previous in positions):
                continue
            shrub = grow_shrub(shrub_index, position, materials, seed)
            shrub['planting_bed_id'] = bed['id']
            assert_bed_bounds(shrub, rectangle)
            created.append(shrub)
            positions.append(position)
            shrub_index += 1
            if len(positions) == 6:
                break
        assert len(positions) >= 4, 'Forecourt needs at least four unobstructed shrubs: ' + bed['id']
        center = ((rectangle[0] + rectangle[2]) / 2, (rectangle[1] + rectangle[3]) / 2)
        grass = grow_grass(len(tree_records) + bed_index, center, rectangle, grass_materials, seed, exclusions, tuft_count=560)
        grass['planting_bed_id'] = bed['id']
        assert_bed_bounds(grass, rectangle)
        assert grass['tuft_count'] >= 320, 'Forecourt has insufficient unobstructed groundcover: ' + bed['id']
        created.append(grass)
        extra_bed_records.append({'id': bed['id'], 'shrub_count': len(positions), 'grass_tuft_count': grass['tuft_count']})
    for obj in created:
        low, high = bounds(obj)
        assert low[0] >= PARCEL_MIN and low[1] >= PARCEL_MIN and high[0] <= PARCEL_MAX and high[1] <= PARCEL_MAX, 'Vegetation exceeds parcel'
    changed = [name for name, digest in source.items() if geometry_digest(bpy.data.objects[name]) != digest]
    assert not changed, 'Original source geometry changed: ' + repr(changed)
    report = {
        'synthetic': True, 'real_site_accuracy': False,
        'asset_source': 'Original deterministic procedural plant geometry; no external plant assets',
        'seed': seed, 'tree_roots_preserved': True,
        'parcel_xy_limits': [PARCEL_MIN, PARCEL_MAX],
        'source_meshes_checked': len(source), 'source_geometry_changed': changed,
        'trees': tree_records, 'shrub_count': shrub_index,
        'planting_beds': beds,
        'extra_bed_planting': extra_bed_records,
        'soil_bed_count': sum(obj.get('asset_role') == 'landscape_bed' for obj in created),
        'clear_soil_area_m2': sum(obj.get('clear_surface_area_m2', 0) for obj in created),
        'grass_tuft_count': sum(obj.get('tuft_count', 0) for obj in created),
        'leaf_count': sum(obj.get('leaf_count', 0) for obj in created),
        'vegetation_vertices': sum(len(obj.data.vertices) for obj in created),
        'vegetation_faces': sum(len(obj.data.polygons) for obj in created),
        'botanical_limits': 'Stylized broadleaf forms, not a validated species or planting plan; unmeasured flat synthetic terrain',
    }
    (out / 'vegetation-report.json').write_text(json.dumps(report, indent=2))
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'model.blend'))
    print('PROCEDURAL VEGETATION COMPLETE ' + json.dumps({key: report[key] for key in ('leaf_count', 'vegetation_vertices', 'shrub_count', 'source_meshes_checked')}))


if __name__ == '__main__':
    main()
