"""Create a separately classified synthetic sloping-site clearing experiment."""
from pathlib import Path
import argparse
import sys, json, math, subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from site_reconstruction import propose,approve_clearing,clear,place,approve_placement,digest,prism,elevation,save_version
from shapely.geometry import Polygon,shape
from shapely import constrained_delaunay_triangles


def rectangle(x,y,w,h):
    return {'type':'Polygon','coordinates':[[[x,y],[x+w,y],[x+w,y+h],[x,y+h],[x,y]]]}


def draped_surface(terrain,footprint,offset):
    """Author synthetic original road/sidewalk surfaces on every actual TIN facet."""
    boundary=shape(footprint);vertices=[];triangles=[];lookup={}
    for t in terrain['triangles']:
        clipped=Polygon([terrain['vertices'][i][:2] for i in t]).intersection(boundary)
        for poly in ([clipped] if clipped.geom_type=='Polygon' else getattr(clipped,'geoms',[])):
            if poly.geom_type!='Polygon' or poly.area<1e-9:continue
            for triangle in constrained_delaunay_triangles(poly).geoms:
                points=list(triangle.exterior.coords)[:-1];ids=[]
                a,b,c=points
                if (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])<0:points.reverse()
                for xy in points:
                    if xy not in lookup:lookup[xy]=len(vertices);vertices.append([*xy,elevation(terrain,xy)+offset])
                    ids.append(lookup[xy])
                triangles.append(ids)
    return {'vertices':vertices,'triangles':triangles}


def fixture():
    ox,oy=220000,631000
    vertices=[[ox+x,oy+y,100+.08*x+.04*y+4*math.sin(x/25)*math.sin(y/15)] for y in range(-20,121,10) for x in range(-20,121,10)]
    triangles=[]
    for j in range(14):
        for i in range(14):
            a=j*15+i;triangles.extend([[a,a+1,a+16],[a,a+16,a+15]])
    terrain={'vertices':vertices,'triangles':triangles,'units':'metres','vertical_datum':'synthetic_test_Z_metres',
             'surface_type':'bare_earth_DTM','source':'Own synthetic test grid; not measured real land'}
    objects=[]
    for identifier,x,y,w,h,height,floors in [('remove-A',10,10,20,14,12,4),('keep-B',55,55,15,15,18,6),('outside-C',90,25,18,14,15,5),('crossing-D',75,5,15,13,9,3)]:
        foot=rectangle(ox+x,oy+y,w,h);base=max(elevation(terrain,p) for p in foot['coordinates'][0])+.1
        objects.append({'id':identifier,'kind':'building','footprint':foot,'height':height,'floors':floors,'base_elevation':base,
                        'mesh':prism(foot,base,height),'source':'synthetic authored existing-state object'})
        mass=objects[-1]['mesh']
        for vertex in mass['vertices'][:len(mass['vertices'])//2]:vertex[2]=elevation(terrain,vertex[:2])
    for identifier,x,y,w,h,kind,height in [('road',-10,-10,120,6,'street',.04),('walk',-10,-4,120,2,'sidewalk',.12),('tree',45,30,2,2,'vegetation',3)]:
        foot=rectangle(ox+x,oy+y,w,h);base=elevation(terrain,[ox+x,oy+y]);mesh=prism(foot,base,height)
        # Author the original road/sidewalk against all source TIN facets, not a flat corner span.
        if kind!='vegetation':
            mesh=draped_surface(terrain,foot,height)
        objects.append({'id':identifier,'kind':kind,'footprint':foot,'height':height,'mesh':mesh,'source':'synthetic authored context'})
    scene={'synthetic':True,'stage':'existing','name':'Sloping synthetic clearing trial','crs':'EPSG:2039','origin':[ox,oy,100],
           'boundary':rectangle(ox,oy,80,80),'terrain':terrain,'objects':objects,
           'diagnostic_cameras':{'street_ground_elevation_m':elevation(terrain,[ox,oy-12])},
           'identity':{'address':'Synthetic test only','block':None,'parcels':[],'official_verification':False}}
    planfoot=rectangle(0,0,20,14);entrance=[ox+30,oy+24];base=max(elevation(terrain,p) for p in rectangle(ox+10,oy+10,20,14)['coordinates'][0])+.1
    accounting={'units':6,'apartment_paldalet_m2':100,'categories_are_disjoint':True,
                'separate_areas_m2':{'core':30,'elevators':18,'stairs':54,'corridors':66,'shafts':18,'shared':24,'lobby':280}}
    plan={'buildings':[{'type':'Feature','geometry':planfoot,'properties':{'id':'new-N','height':12,'floors':4,
           'base_elevation':base,'entrance_xy':entrance,'planning_status':'synthetic_concept','program':accounting}}],
          'control_points':[{'plan_xy':[0,0],'target_xy':[ox+10,oy+10]},{'plan_xy':[40,0],'target_xy':[ox+50,oy+10]},{'plan_xy':[0,40],'target_xy':[ox+10,oy+50]},{'plan_xy':[40,40],'target_xy':[ox+50,oy+50]}],
          'max_residual_m':.01,'scale_bounds':[.9999,1.0001],'north_reference':'projected_grid_north','plan_north_xy':[0,1],
          'north_tolerance_degrees':.1,'vertical_datum':terrain['vertical_datum'],'max_entrance_difference_m':.2,
          'buildable_envelope':rectangle(ox+5,oy+5,70,70),
          'classification':'synthetic_concept_not_approved_architecture'}
    return scene,plan


def main():
    raise SystemExit('V4 completed. Automatic demo approval disabled: use the planning UI and explicit human approval before new proposed massing. Existing artifacts are preserved.')

if __name__=='__main__':main()
