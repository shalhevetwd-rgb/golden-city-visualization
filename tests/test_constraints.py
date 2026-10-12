import unittest,sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
from constraints import site_gate,sukkah_geometry,compare_snapshots
from intake import inspect
class Gates(unittest.TestCase):
 def test_unknown_real_site_blocked(self):self.assertFalse(site_gate({})['final_render_allowed'])
 def test_synthetic_never_real_accuracy(self):self.assertFalse(site_gate({'synthetic':True})['accurate_real_site'])
 def test_verified_evidence_still_missing_terrain_blocked(self):
  p={k:True for k in ['parcel_geometry_verified','alignment_approved','building_positions_verified','floors_verified','heights_verified']};self.assertFalse(site_gate(p)['final_render_allowed'])
 def test_sukkah_no_policy_does_not_assume_rules(self):self.assertEqual(sukkah_geometry({},[],None)['status'],'missing_project_policy')
 def test_sukkah_geometry_never_halakhic_approval(self):
  r=sukkah_geometry({'rectangle':[0,0,3,3],'floor_z':0,'clear_height_m':3},[],{'min_width_m':2,'min_length_m':2,'min_height_m':2});self.assertEqual(r['status'],'geometric_candidate');self.assertFalse(r['halakhic_approval'])
 def test_overhang_detected(self):
  r=sukkah_geometry({'rectangle':[0,0,3,3],'floor_z':0,'clear_height_m':3},[{'rectangle':[1,1,4,4],'z':5}],{'min_width_m':2,'min_length_m':2,'min_height_m':2});self.assertTrue(r['sky_obstructed'])
 def test_nearby_nonoverlapping_slab_not_obstruction(self):
  r=sukkah_geometry({'rectangle':[0,0,3,3],'floor_z':0,'clear_height_m':3},[{'rectangle':[4,4,6,6],'z':5}],{'min_width_m':2,'min_length_m':2,'min_height_m':2});self.assertFalse(r['sky_obstructed'])
 def test_edit_outside_target_rejected(self):
  a={'wall':{'owner':'B','geometry':'x','material':'old'}};b={'wall':{'owner':'B','geometry':'x','material':'new'}};self.assertFalse(compare_snapshots(a,b,'A')['passed'])
 def test_geometry_edit_rejected(self):
  a={'wall':{'owner':'A','geometry':'x'}};b={'wall':{'owner':'A','geometry':'y'}};self.assertFalse(compare_snapshots(a,b,'A')['passed'])
 def test_material_edit_passes_without_pixel_claim(self):
  a={'wall':{'owner':'A','geometry':'x','material':'old'}};b={'wall':{'owner':'A','geometry':'x','material':'new'}};r=compare_snapshots(a,b,'A');self.assertTrue(r['passed']);self.assertFalse(r['pixel_identity_claim'])
 def test_removed_context_rejected(self):self.assertFalse(compare_snapshots({'tree':{'owner':'context','geometry':'x'}},{},'A')['passed'])
 def test_actual_source_glb_read(self):self.assertEqual(inspect(ROOT/'baseline/aerial.glb')['meshes'],15)
 def test_actual_source_png_read(self):self.assertEqual(inspect(ROOT/'aerial.png')['dimensions'],[960,720])
 def test_unsupported_not_claimed(self):self.assertFalse(inspect(ROOT/'README.md')['supported'])
 def test_original_delivery_files_preserved(self):
  manifest=json.loads((ROOT/'manifest.json').read_text())
  for r in manifest['files']:self.assertEqual(hashlib.sha256((ROOT/r['name']).read_bytes()).hexdigest(),r['sha256'])

 def test_claimed_flags_without_evidence_not_accuracy(self):
  p={k:True for k in ['parcel_geometry_verified','alignment_approved','building_positions_verified','floors_verified','heights_verified','terrain_verified']};self.assertFalse(site_gate(p)['final_render_allowed'])
 def test_foreign_project_evidence_rejected(self):
  keys=['parcel_geometry_verified','alignment_approved','building_positions_verified','floors_verified','heights_verified','terrain_verified'];p=dict(id='new',evidence={k:{'source':'source','checked_at':'date','verification':'professional_review','project_id':'old'} for k in keys});p.update({k:True for k in keys});self.assertFalse(site_gate(p)['final_render_allowed'])

 def test_target_movement_rejected_even_without_vertex_change(self):
  a={'wall':{'owner':'A','geometry':'same','transform':[0,0,0]}};b={'wall':{'owner':'A','geometry':'same','transform':[1,0,0]}};self.assertFalse(compare_snapshots(a,b,'A')['passed'])
