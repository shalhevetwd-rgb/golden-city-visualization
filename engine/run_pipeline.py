"""Reproducible synthetic architectural v3 workflow; never overwrites v2."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--draft',action='store_true')
parser.add_argument('--config',default='config/synthetic-design-v3.json')
parser.add_argument('--output',default='outputs/v3')
parser.add_argument('--reuse-model',action='store_true',help='Reuse the built/landscaped model for a reviewed final render')
parser.add_argument('--build-only',action='store_true')
args=parser.parse_args()
env={**os.environ,'GOLDEN_CITY_DESIGN_CONFIG':str((ROOT/args.config).resolve()),'GOLDEN_CITY_OUTPUT_DIR':str((ROOT/args.output).resolve()),'GOLDEN_CITY_RENDER_MODE':'draft' if args.draft else 'final'}
if (ROOT/args.output).resolve().is_relative_to((ROOT/'outputs/v2').resolve()):raise SystemExit('V3 cannot replace the preserved v2 artifacts')
steps=[] if args.reuse_model else ['build_scene.py','calibrate_scene.py','refine_vegetation.py']
if not args.build_only:steps+=['render_saved.py','render_targeted_edit.py','verify_geometry.py']
for script in steps:
    print('PIPELINE STEP',script,flush=True)
    subprocess.run(['/usr/bin/blender','--background','--threads','8','--python-exit-code','1','--python',str(ROOT/'engine'/script)],cwd=ROOT,env=env,check=True)
if not args.build_only:
    subprocess.run([sys.executable,str(ROOT/'engine/check_outputs.py')],cwd=ROOT,env=env,check=True)
print('ARCHITECTURAL V3 PIPELINE COMPLETE',args.output,flush=True)
