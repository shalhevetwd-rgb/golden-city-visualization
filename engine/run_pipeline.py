"""Reproducible synthetic exercise. No third-party service or pip installation."""
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for script,extra in [('build_scene.py',['--','--build-only']),('calibrate_scene.py',[]),('refine_vegetation.py',[]),('render_saved.py',['--','--views-only']),('render_targeted_edit.py',[]),('verify_geometry.py',[])]:
 subprocess.run(['/usr/bin/blender','-b','-t','8','--python-exit-code','1','--python',str(ROOT/'engine'/script),*extra],cwd=ROOT,check=True)
subprocess.run([sys.executable,str(ROOT/'engine/check_outputs.py')],cwd=ROOT,check=True)
subprocess.run([sys.executable,str(ROOT/'engine/build_viewer.py')],cwd=ROOT,check=True)
