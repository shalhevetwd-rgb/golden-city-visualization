"""Explicit simulated human consent for test actors only; not production consent."""
import copy
from planning_brief import normalize,review,approve

def known_brief(plan):
 p=plan['buildings'][0]['properties'];units=sum(f['properties']['program']['units'] for f in plan['buildings'])
 return {'answers':{'existing_units':3,'units':units,'building_count':len(plan['buildings']),'residential_floors':p['floors']-1,'height_m':p['height'],'has_site_plan':True,'constraints':'synthetic test envelope; no statutory approval','sukkah':'unknown','additional_uses':'none in supplied test plan','design':'retained test geometry','views':['aerial','street'],'floor_schedule':{'entrance':1,'service':0,'commercial':0,'included_in_total':True}},'separate_areas_m2':p['program']['separate_areas_m2'],'categories_are_disjoint':True,'provenance':{'input':'simulated test user'}}

def approved_scene(scene,plan):
 out=copy.deepcopy(scene);b=known_brief(plan);packet=review(out,b,plan)
 out['planning_brief']=approve(out,b,plan,{'review_sha256':packet['review_sha256'],'confirmed_by_user':True,'origin':'user_interface','reviewer':'SIMULATED HUMAN TEST ACTOR'})
 return out
