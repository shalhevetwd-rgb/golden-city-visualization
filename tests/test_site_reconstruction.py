import copy,json,tempfile,unittest
from pathlib import Path
from scripts.site_clearing_demo import fixture,rectangle
from site_reconstruction import program,alternatives,propose,approve_clearing,clear,place,approve_placement,digest,elevation,validate_scene,api_action,compare,prism


class SiteTests(unittest.TestCase):
    def setUp(self):self.scene,self.plan=fixture()
    def cleared(self):return clear(self.scene,approve_clearing(self.scene,['remove-A'],'tester',True))
    def place(self,scene,plan):
        from tests.planning_helpers import approved_scene
        return place(approved_scene(scene,plan),plan)
    def test_default_paldalet_is_not_gross(self):
        p=program({'units':12});self.assertEqual(p['apartments_paldalet_total_m2'],1200);self.assertIsNone(p['gross_total_m2']);self.assertIn('lobby',p['missing_area_categories'])
    def test_disjoint_areas_and_lobby_separate(self):
        p=program(self.plan['buildings'][0]['properties']['program']);self.assertEqual(p['apartments_paldalet_total_m2'],600);self.assertEqual(p['separate_areas_m2']['lobby'],280);self.assertEqual(p['gross_total_m2'],1090)
        data=copy.deepcopy(self.plan['buildings'][0]['properties']['program']);data.pop('categories_are_disjoint');self.assertIsNone(program(data)['gross_total_m2'])
    def test_missing_program_does_not_create_geometry(self):
        a=alternatives({});self.assertTrue(a['missing']);self.assertEqual(a['alternatives'],[]);self.assertFalse(a['geometry_created'])
        self.assertIsNone(a['program']['apartments_paldalet_total_m2'])
        self.assertEqual(len(a['conditional_concept_options']),2)
    def test_multiplier_inconsistency_and_invalid_counts(self):
        self.assertTrue(program({'units':12,'existing_units':5,'multiplier':2})['program_inconsistencies'])
        self.assertEqual(program({'units':10,'existing_units':5,'multiplier':2})['target_units_from_multiplier'],10)
        for v in [True,-1,0,float('nan')]:
            with self.assertRaises(ValueError):program({'units':v})
    def test_roof_triangles_face_up_and_cannot_cover_preserved_tree(self):
        m=prism(rectangle(0,0,2,2),3,9);n=len(m['vertices'])//2
        for t in m['triangles']:
            if all(i>=n for i in t):
                a,b,c=[m['vertices'][i] for i in t];self.assertGreater((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]),0)
        source=self.cleared();foot=rectangle(220015,631015,2,2);source['objects'].append({'id':'protected-tree','kind':'vegetation','footprint':foot,'mesh':prism(foot,103,3)})
        self.assertTrue(self.place(source,self.plan)['placement']['errors'])
    def test_concept_arithmetic_is_never_approved(self):
        data=copy.deepcopy(self.plan['buildings'][0]['properties']['program']);data.update(multiplier=2,desired_height_m=12,building_count=1,planning_constraints={'max_coverage_ratio':.5,'residential_floors':3})
        a=alternatives(data,6400);self.assertEqual(len(a['alternatives']),2);self.assertFalse(a['planning_approval'])
        self.assertEqual(a['alternatives'][0]['aggregate_average_floor_area_m2'],280)
        self.assertEqual(a['alternatives'][0]['aggregate_residential_floor_average_m2'],270)
    def test_no_default_demolition_or_crossing(self):
        p=propose(self.scene);self.assertEqual(set(p['candidate_ids']),{'remove-A','keep-B'});self.assertEqual(p['crossing_buildings_not_removable'],['crossing-D'])
        for ids in [[],['outside-C'],['crossing-D'],['remove-A','remove-A']]:
            with self.assertRaises(ValueError):approve_clearing(self.scene,ids,'tester',True)
        with self.assertRaises(ValueError):approve_clearing(self.scene,['remove-A'],'tester',False)
    def test_clear_preserves_original_terrain_and_all_unselected(self):
        sha=digest(self.scene);result=self.cleared();self.assertEqual(digest(self.scene),sha);self.assertEqual(result['terrain'],self.scene['terrain']);self.assertEqual(len(result['objects']),6)
        for o in result['objects']:self.assertEqual(o,next(x for x in self.scene['objects'] if x['id']==o['id']))
        self.assertGreater(result['qa']['elevation_range_m'][1]-result['qa']['elevation_range_m'][0],15)
    def test_stale_boundary_or_terrain_approval_rejected(self):
        approval=approve_clearing(self.scene,['remove-A'],'tester',True);mutated=copy.deepcopy(self.scene);mutated['terrain']['vertices'][0][2]+=.1
        with self.assertRaises(ValueError):clear(mutated,approval)
        mutated=copy.deepcopy(self.scene);mutated['boundary']=rectangle(220000,631000,79,80)
        with self.assertRaises(ValueError):clear(mutated,approval)
    def test_missing_height_coverage_and_dsm_rejected(self):
        source=copy.deepcopy(self.scene);source['objects'][0].pop('mesh')
        with self.assertRaises(ValueError):validate_scene(source)
        source=copy.deepcopy(self.scene);source['terrain']['triangles']=source['terrain']['triangles'][:2]
        with self.assertRaises(ValueError):validate_scene(source)
        with self.assertRaises(ValueError):elevation(self.scene['terrain'],[0,0])
        source=copy.deepcopy(self.scene);source['terrain']['surface_type']='roof_DSM'
        with self.assertRaises(ValueError):validate_scene(source)
    def test_duplicate_terrain_and_conflicting_heights_rejected(self):
        source=copy.deepcopy(self.scene);source['terrain']['triangles'].append(source['terrain']['triangles'][0])
        with self.assertRaises(ValueError):validate_scene(source)
        source=copy.deepcopy(self.scene);v=list(source['terrain']['vertices'][0]);v[2]+=1;source['terrain']['vertices'].append(v)
        with self.assertRaises(ValueError):validate_scene(source)
    def test_real_labels_cannot_unlock_clearing(self):
        source=copy.deepcopy(self.scene);source['synthetic']=False;source['official_verified']=True
        with self.assertRaisesRegex(ValueError,'Real clearing blocked'):clear(source,approve_clearing(source,['remove-A'],'tester',True))
    def test_placement_control_points_terrain_and_accounting(self):
        source=self.cleared();sha=digest(source);p=self.place(source,self.plan);self.assertEqual(digest(source),sha);self.assertEqual(p['placement']['errors'],[]);self.assertLess(p['placement']['alignment']['max_residual_m'],1e-8)
        n=next(o for o in p['objects'] if o['id']=='new-N');self.assertEqual(n['program']['apartments_paldalet_total_m2'],600);self.assertEqual(n['floors'],4);self.assertEqual(n['height'],12)
        self.assertAlmostEqual(p['placement']['coverage_ratio'],280/6400);self.assertEqual(source['terrain'],p['terrain'])
        self.assertAlmostEqual(p['placement']['levels'][0]['difference_m'],.1,places=6)
        for v in n['mesh']['vertices'][:len(n['mesh']['vertices'])//2]:self.assertAlmostEqual(v[2],elevation(source['terrain'],v[:2]))
    def test_wrong_north_vertical_datum_or_controls_rejected(self):
        for key,value in [('plan_north_xy',[1,0]),('vertical_datum','unknown'),('control_points',self.plan['control_points'][:2])]:
            plan=copy.deepcopy(self.plan);plan[key]=value
            with self.assertRaises(ValueError):self.place(self.cleared(),plan)
    def test_deviations_prevent_approval(self):
        plan=copy.deepcopy(self.plan);plan['buildable_envelope']=rectangle(220040,631040,30,30);candidate=self.place(self.cleared(),plan)
        self.assertTrue(candidate['placement']['errors'])
        with self.assertRaises(ValueError):approve_placement(candidate,digest(candidate),'tester')
        plan=copy.deepcopy(self.plan);plan['buildings'][0]['properties']['base_elevation']+=10;candidate=self.place(self.cleared(),plan);self.assertTrue(candidate['placement']['errors'])
        plan=copy.deepcopy(self.plan);plan['buildings'][0]['properties']['program']['units']=100;candidate=self.place(self.cleared(),plan)
        self.assertTrue(any('floorplate capacity' in e for e in candidate['placement']['errors']))
    def test_placement_approval_binds_geometry(self):
        candidate=self.place(self.cleared(),self.plan)
        with self.assertRaises(ValueError):approve_placement(candidate,'bad','tester')
        result=approve_placement(candidate,digest(candidate),'tester');self.assertTrue(result['placement']['approved']);self.assertFalse(result['final_render_ready'])
    def test_guard_detects_protected_terrain_and_boundary_change(self):
        invalid=copy.deepcopy(self.scene);invalid['objects'][0]['height']+=1
        with self.assertRaises(ValueError):validate_scene(invalid)
        for target in ('objects','terrain','boundary'):
            result=self.cleared()
            if target=='objects':result['objects'][0]['mesh']['vertices'][0][2]+=1
            elif target=='terrain':result['terrain']['vertices'][0][2]+=1
            else:result['boundary']=rectangle(220000,631000,79,80)
            with self.assertRaises(ValueError):compare(self.scene,result,['remove-A'])
    def test_persisted_immutable_stages_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=api_action('import',{'scene':self.scene},tmp)['version'];p=Path(tmp)/(r['version_id']+'.json');original=p.read_bytes()
            req={'version_id':r['version_id'],'expected_sha256':r['sha256'],'selected_ids':['remove-A'],'reviewer':'tester','boundary_approved':True}
            cleared=api_action('clear',req,tmp)['version'];self.assertEqual(p.read_bytes(),original)
            from tests.planning_helpers import known_brief
            cleared=api_action('planning-save',{'version_id':cleared['version_id'],'expected_sha256':cleared['sha256'],'brief':known_brief(self.plan)},tmp)['version']
            packet=api_action('planning-review',{'version_id':cleared['version_id'],'expected_sha256':cleared['sha256'],'plan':self.plan},tmp)
            cleared=api_action('planning-approve',{'version_id':cleared['version_id'],'expected_sha256':cleared['sha256'],'plan':self.plan,'review_sha256':packet['review_sha256'],'origin':'user_interface','confirmed_by_user':True,'reviewer':'SIMULATED HUMAN TEST ACTOR'},tmp)['version']
            planted=api_action('place',{'version_id':cleared['version_id'],'expected_sha256':cleared['sha256'],'plan':self.plan},tmp)['version']
            approved=api_action('approve-placement',{'version_id':planted['version_id'],'expected_sha256':planted['sha256'],'reviewer':'tester'},tmp)['version']
            self.assertEqual(len(list(Path(tmp).glob('*.json'))),6);self.assertEqual(approved['scene']['stage'],'placement_approved')
            bad=json.loads(p.read_text());bad['scene']['objects'][0]['height']+=1;p.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):api_action('clear',req,tmp)
