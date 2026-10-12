from pathlib import Path
import shutil
root = Path(__file__).resolve().parents[1]
for source, target in [('build/three.module.js','three.module.js'), ('build/three.core.js','three.core.js'), ('examples/jsm/controls/OrbitControls.js','OrbitControls.js'), ('examples/jsm/loaders/OBJLoader.js','OBJLoader.js')]:
    shutil.copyfile(root/'node_modules/three'/source, root/'vendor'/target)
shutil.copyfile(root/'node_modules/three/LICENSE',root/'vendor/LICENSE.three')
