"""General evidence gates. Synthetic test designation never implies site accuracy."""
import hashlib,json,math

def site_gate(p):
 required=['parcel_geometry_verified','alignment_approved','building_positions_verified','floors_verified','heights_verified','terrain_verified']
 evidence=p.get('evidence',{})
 def verified(k):
  r=evidence.get(k,{})
  return p.get(k) is True and bool(r.get('source')) and bool(r.get('checked_at')) and r.get('verification')=='professional_review' and bool(p.get('id')) and r.get('project_id')==p.get('id')
 missing=[k for k in required if not verified(k)]
 return {'final_render_allowed':not missing or p.get('synthetic') is True,'classification':'synthetic_test' if p.get('synthetic') else 'site_verified' if not missing else 'blocked_for_missing_evidence','missing':missing,'accurate_real_site':not missing and not p.get('synthetic',False)}

def sukkah_geometry(spec,obstructions,policy):
 """Geometric screening only; policy must come from the project's authority."""
 if not policy or any(k not in policy for k in ['min_width_m','min_length_m','min_height_m']):return {'status':'missing_project_policy','halakhic_approval':False}
 x0,y0,x1,y1=spec['rectangle'];height=spec['clear_height_m'];z=spec['floor_z']
 obstructed=any(o['z']>z and min(x1,o['rectangle'][2])>max(x0,o['rectangle'][0]) and min(y1,o['rectangle'][3])>max(y0,o['rectangle'][1]) for o in obstructions)
 size=x1-x0>=policy['min_width_m'] and y1-y0>=policy['min_length_m'] and height>=policy['min_height_m']
 return {'status':'geometric_candidate' if size and not obstructed else 'geometry_failed','sky_obstructed':obstructed,'dimension_screen_passed':size,'halakhic_approval':False,'requires':['סכך','דפנות','פסיקה ואישור מקצועי לפרויקט']}

def compare_snapshots(before,after,target):
 changed=[k for k in before if before[k]!=after.get(k)];added=sorted(set(after)-set(before));removed=sorted(set(before)-set(after))
 outside=[k for k in changed if before[k].get('owner')!=target]
 geometry_changed=[k for k in changed if before[k].get('geometry')!=after.get(k,{}).get('geometry') or before[k].get('transform')!=after.get(k,{}).get('transform')]
 return {'changed_objects':changed,'added_objects':added,'removed_objects':removed,'outside_target_changed':outside,'geometry_changed':geometry_changed,'passed':not outside and not geometry_changed and not added and not removed,'pixel_identity_claim':False}
