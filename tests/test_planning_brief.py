import copy,tempfile,unittest
from pathlib import Path
from planning_brief import initial,normalize,quantities,alternatives,review,approve,require_approval,sha
from workflow import dossier
from scripts.site_clearing_demo import fixture
from site_reconstruction import clear,approve_clearing,place,api_action,save_version,digest
from tests.planning_helpers import known_brief,approved_scene

class PlanningTests(unittest.TestCase):
 def setUp(self):
  scene,self.plan=fixture();self.scene=clear(scene,approve_clearing(scene,['remove-A'],'synthetic source test actor',True))
 def test_address_only_has_mandatory_questionnaire_and_no_geometry(self):
  p=dossier({'name':'case','address':'user provided address','block':30207});self.assertEqual(p['buildings'],[]);self.assertIsNone(p['boundary']);self.assertEqual(p['planning_brief']['apartment_paldalet_m2'],100)
  with self.assertRaises(ValueError):require_approval(p,self.plan)
 def test_no_brief_no_proposed_model_direct_or_api(self):
  old=copy.deepcopy(self.scene)
  with self.assertRaisesRegex(ValueError,'פרוגרמה'):place(self.scene,self.plan)
  self.assertEqual(old,self.scene)
  with tempfile.TemporaryDirectory() as d:
   r=save_version(self.scene,d)
   with self.assertRaises(ValueError):api_action('place',{'version_id':r['version_id'],'expected_sha256':r['sha256'],'plan':self.plan},d)
   self.assertEqual(len(list(Path(d).glob('*.json'))),1)
 def test_known_complete_requires_human_approval_before_creation(self):
  b=known_brief(self.plan);s=copy.deepcopy(self.scene);s['planning_brief']=normalize(b)
  with self.assertRaises(ValueError):place(s,self.plan)
  p=review(s,b,self.plan);self.assertTrue(p['can_approve']);self.assertFalse(p['approved'])
  a=approved_scene(s,self.plan);result=place(a,self.plan);self.assertEqual(len(result['objects']),len(s['objects'])+1)
  for o in s['objects']:self.assertEqual(o,next(x for x in result['objects'] if x['id']==o['id']))
  self.assertEqual(result['terrain'],s['terrain'])
 def test_agent_automatic_or_unchecked_approval_rejected(self):
  b=known_brief(self.plan);p=review(self.scene,b,self.plan)
  for origin,checked in [('agent',True),('auto',True),('user_interface',False)]:
   with self.assertRaises(ValueError):approve(self.scene,b,self.plan,{'review_sha256':p['review_sha256'],'confirmed_by_user':checked,'origin':origin,'reviewer':'tester'})
 def test_stale_program_plan_boundary_terrain_and_objects_reject(self):
  s=approved_scene(self.scene,self.plan)
  for mutation in ('program','boundary','terrain','objects'):
   r=copy.deepcopy(s)
   if mutation=='program':r['planning_brief']['answers']['units']+=1
   elif mutation=='boundary':r['boundary']['coordinates'][0][0][0]+=.1
   elif mutation=='terrain':r['terrain']['vertices'][0][2]+=.1
   else:r['objects'][0]['height']+=.1
   with self.assertRaises(ValueError):place(r,self.plan)
  plan=copy.deepcopy(self.plan);plan['max_residual_m']+=.1
  with self.assertRaises(ValueError):place(s,plan)
 def test_unknown_answers_do_not_authorize_but_sources_and_clear_still_work(self):
  b={'answers':{k:'unknown' for k in __import__('planning_brief').QUESTIONS}};n=normalize(b);self.assertEqual(n['missing_questions'],[])
  self.assertFalse(review(self.scene,b,None)['can_approve']);self.assertIsNone(n['quantities']['accounted_total_m2'])
  scene,_=fixture();scene['planning_brief']=n;c=clear(scene,approve_clearing(scene,['remove-A'],'test user',True));self.assertEqual(c['stage'],'cleared')
 def test_alternatives_missing_context_never_invent_site(self):
  b=known_brief(self.plan);a=alternatives(b,{});self.assertEqual(a['alternatives'],[]);self.assertFalse(a['geometry_created']);self.assertIn('terrain_verified',a['missing'])
 def test_requested_alternatives_are_numerical_nonapproved_and_do_not_create_model(self):
  b=known_brief(self.plan);b['answers'].update(building_count='unknown',height_m='unknown',residential_floors='unknown',has_site_plan=False)
  context={'buildable_area_m2':4900,'max_coverage_ratio':.5,'max_total_floors':8,'terrain_verified':True,'building_lines_verified':True,'policy_reference':'supplied synthetic feasibility constraints, not statutory'}
  old=digest(self.scene);a=alternatives(b,context);self.assertGreaterEqual(len(a['alternatives']),2);self.assertFalse(a['planning_approved']);self.assertEqual(digest(self.scene),old)
  for o in a['alternatives']:self.assertEqual(o['units'],6);self.assertGreater(o['estimated_residential_core_m2'],0);self.assertLessEqual(o['estimated_aggregate_footprint_m2'],2450);self.assertTrue(o['assumptions'])
  self.assertFalse(review(self.scene,b,self.plan)['can_approve'])
  # User chooses a numeric alternative and provides the missing source plan and height.
  choice=a['alternatives'][0];b['answers'].update(building_count=choice['buildings'],residential_floors=3,height_m=12,has_site_plan=True)
  self.assertTrue(review(self.scene,b,self.plan)['can_approve'])
 def test_unknown_unit_target_gets_capacity_options_without_auto_program_or_model(self):
  b=known_brief(self.plan);b['answers'].update(units='unknown',building_count='unknown',residential_floors='unknown',height_m='unknown')
  context={'buildable_area_m2':4900,'max_coverage_ratio':.5,'max_total_floors':8,'terrain_verified':True,'building_lines_verified':True,'policy_reference':'supplied synthetic feasibility parameters'}
  before=copy.deepcopy(b);a=alternatives(b,context);self.assertEqual(len(a['alternatives']),3);self.assertEqual(b,before);self.assertFalse(a['geometry_created']);self.assertFalse(a['planning_approved'])
  for option in a['alternatives']:
   self.assertGreater(option['units'],0);self.assertIn('ceiling',option['unit_target_origin']);self.assertLessEqual(option['estimated_aggregate_footprint_m2'],2450);self.assertTrue(option['assumptions'])
  self.assertFalse(review(self.scene,b,self.plan)['can_approve'])
 def test_core_changes_with_height_and_units_per_floor(self):
  b=normalize(known_brief(self.plan));low=quantities(b)['core_estimate'];b['answers'].update(residential_floors=10,total_floors=11);high=quantities(b)['core_estimate'];self.assertNotEqual(low['per_building_per_residential_floor_m2'],high['per_building_per_residential_floor_m2']);self.assertFalse(high['approved']);b['answers'].update(residential_floors=3,total_floors=4,height_m=35);tall=quantities(b)['core_estimate'];self.assertGreater(tall['per_building_per_residential_floor_m2']['elevators'],low['per_building_per_residential_floor_m2']['elevators'])
 def test_paldalet_separate_floor_schedule_and_no_double_count(self):
  b=normalize(known_brief(self.plan));self.assertEqual(b['quantities']['apartments_paldalet_m2'],600);self.assertEqual(b['answers']['total_floors'],4);self.assertIsNone(b['quantities']['accounted_total_m2']);self.assertIn('service',b['quantities']['missing_area_categories']);b['answers']['floor_schedule']['included_in_total']=False;b=normalize(b);self.assertEqual(b['answers']['defined_floor_count'],3);self.assertEqual(b['answers']['total_floors'],4)
  b['separate_areas_m2'].update(resident_room=0,service=0,commercial=0,public=0,parking=0);b=normalize(b);self.assertEqual(b['quantities']['accounted_total_m2'],1090)
  b['categories_are_disjoint']=False;self.assertIsNone(normalize(b)['quantities']['accounted_total_m2'])
 def test_prefill_and_immutable_brief_versions(self):
  p=dossier({'name':'case','address':'source address','block':30207,'planning_answers':{'existing_units':12,'views':['street']}});self.assertEqual(p['planning_brief']['answers']['existing_units'],12)
  with tempfile.TemporaryDirectory() as d:
   r=save_version(self.scene,d);old=Path(d,r['version_id']+'.json').read_bytes();updated=api_action('planning-save',{'version_id':r['version_id'],'expected_sha256':r['sha256'],'brief':known_brief(self.plan)},d)['version'];self.assertEqual(old,Path(d,r['version_id']+'.json').read_bytes());self.assertNotEqual(updated['version_id'],r['version_id']);self.assertEqual(updated['scene']['objects'],self.scene['objects'])
 def test_wrong_floor_height_building_and_units_block_review(self):
  for key,value in [('units',7),('building_count',2),('height_m',15),('residential_floors',5)]:
   b=known_brief(self.plan);b['answers'][key]=value;self.assertFalse(review(self.scene,b,self.plan)['can_approve'])
 def test_boolean_nan_negative_count_rejected(self):
  for value in (True,float('nan'),-1,1.5):
   b=known_brief(self.plan);b['answers']['units']=value
   with self.assertRaises(ValueError):normalize(b)
