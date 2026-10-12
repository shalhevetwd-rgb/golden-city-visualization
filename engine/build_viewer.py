"""Build v3 from the existing offline viewer's licensed, embedded Three modules.

Rendering and source files are read without modification. The generated viewer
keeps PNG/GLB originals byte-for-byte; browser edits are separate previews.
"""
import argparse
import base64
import hashlib
import html
import json
import math
import re
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def sha256(data):
    return hashlib.sha256(data).hexdigest()

def shared_bin_parts(raw):
    """Return exact raw chunks only for the standard two-chunk GLB layout."""
    if len(raw) < 20 or raw[:4] != b"glTF":
        return None
    offset, chunks = 12, []
    while offset < len(raw):
        if offset + 8 > len(raw):
            return None
        length, kind = struct.unpack_from("<II", raw, offset)
        end = offset + 8 + length
        if end > len(raw) or length % 4:
            return None
        chunks.append((kind, raw[offset:end]))
        offset = end
    if len(chunks) != 2 or chunks[0][0] != 0x4E4F534A or chunks[1][0] != 0x004E4942:
        return None
    return raw[:12], chunks[0][1], chunks[1][1]


def build(output, config, base_viewer):
    output, config, base_viewer = Path(output), Path(config), Path(base_viewer)
    original = base_viewer.read_text()
    importmap = re.search(r'<script type="importmap">.*?</script>', original, re.S).group(0)
    license_block = re.search(r'<details><summary>רישיון Three.js</summary>.*?</details>', original, re.S).group(0)
    design = json.loads(config.read_text())
    if design.get("synthetic") is not True:
        raise ValueError("The embedded v3 demonstration requires explicitly synthetic design data")
    images, image_sources = [], {}
    views = [("aerial", "מבט אווירי"), ("street", "מבט רחוב"), ("architectural", "מבט אדריכלי"), ("edited-aerial", "לאחר שינוי חזית A")]
    figures = []
    for key, label in views:
        name = key + ".png"
        raw = (output / name).read_bytes()
        with Image.open(output / name) as image:
            image.verify()
        data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
        image_sources[key] = data_url
        images.append({"id": "image-" + key, "name": name, "bytes": len(raw), "sha256": sha256(raw)})
        figures.append('<figure><img id="image-' + key + '" alt="' + html.escape(label) +
                       ' — ניסוי סינתטי" src="' + data_url + '"><figcaption><strong>' +
                       html.escape(label) + '</strong><button data-image-download="' + name +
                       '">הורדת PNG</button></figcaption></figure>')
    models, originals = [], {}
    for key, name in [("baseline", "model.glb"), ("edited", "edited-model.glb")]:
        raw = (output / name).read_bytes()
        if len(raw) < 20 or raw[:4] != b"glTF" or struct.unpack_from("<II", raw, 4) != (2, len(raw)):
            raise ValueError("Invalid GLB header: " + name)
        blend = output / name.replace(".glb", ".blend")
        originals[key] = raw
        models.append({"key": key, "name": name, "bytes": len(raw), "sha256": sha256(raw),
                       "blend_sha256": sha256(blend.read_bytes()) if blend.exists() else None})
    pieces = {key: shared_bin_parts(raw) for key, raw in originals.items()}
    shared_binary = None
    if all(pieces.values()) and pieces["baseline"][2] == pieces["edited"][2]:
        shared_binary = pieces["baseline"][2]
        for record in models:
            header, json_chunk, bin_chunk = pieces[record["key"]]
            if header + json_chunk + shared_binary != originals[record["key"]]:
                raise ValueError("Shared BIN reconstruction differs from the original GLB")
            record.update(encoding="glb_shared_bin", header_base64=base64.b64encode(header).decode(),
                          json_chunk_base64=base64.b64encode(json_chunk).decode())
    else:
        for record in models:
            record.update(encoding="base64", base64=base64.b64encode(originals[record["key"]]).decode())
    geometry_report = output / "geometry-report.json"
    resolved_cameras = json.loads(geometry_report.read_text()).get("cameras", {}) if geometry_report.exists() else {}
    cameras = {}
    for name, camera in design["cameras"].items():
        record = {**camera, **resolved_cameras.get(name, {})}
        if any(value is None for value in record["position"]):
            raise ValueError("A camera height requires the resolved geometry-report.json: " + name)
        if "vertical_fov" in record:
            record["fov"] = record["vertical_fov"]
        if "fov" not in record:
            sensor_height = record.get("sensor_height_mm", 24)
            record["fov"] = math.degrees(2 * math.atan(sensor_height / (2 * record["lens_mm"])))
        record.setdefault("shift_y", 0)
        cameras[name] = record
    payload = {"version": "architectural-v3", "synthetic": True, "design_status": design.get("design_status"),
               "images": images, "models": models, "cameras": cameras,
               "source_manifest": [{k: model[k] for k in ("key","name","bytes","sha256","blend_sha256","encoding")} for model in models],
               "capabilities": [
                   {"extension": "PNG/JPEG", "status": "browser_decode", "label": "פענוח תמונה; לא נלמדים מיקום, קנה מידה או גובה"},
                   {"extension": "GLB", "status": "geometry_decode", "label": "קליטת גיאומטריה מקומית; מערכת קרקע וקומות אינן מוסקות"},
                   {"extension": "GeoJSON", "status": "syntax_only", "label": "בדיקת מבנה JSON בלבד בקובץ העצמאי"},
                   {"extension": "PDF/DWG/DXF", "status": "not_parsed", "label": "שמירת פרטי מקור בלבד; אין כאן חילוץ תוכנית"},
               ]}
    if shared_binary is not None:
        payload["shared_glb_bin_base64"] = base64.b64encode(shared_binary).decode()
        payload["shared_glb_bin_sha256"] = sha256(shared_binary)
    template = (ROOT / "engine" / "viewer_template.html").read_text()
    module = (ROOT / "engine" / "viewer_module.mjs").read_text()
    hash_helper = (ROOT / "engine" / "hash_sha256.mjs").read_text().replace("export ", "")
    module = module.replace("// __HASH_HELPER__", hash_helper)
    values = {"__IMPORTMAP__": importmap, "__LICENSE__": license_block, "__IMAGES__": "".join(figures),
              "__BEFORE_SRC__": image_sources["aerial"], "__AFTER_SRC__": image_sources["edited-aerial"],
              "__PAYLOAD__": json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
              "__MODULE__": module}
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    if len(template.encode()) >= 99_000_000:
        raise ValueError("The standalone viewer exceeds the 99 MB delivery budget; reduce geometry or use a separately validated compressed embedding")
    output.mkdir(parents=True, exist_ok=True)
    (output / "viewer.html").write_text(template)
    (output / "viewer-module.mjs").write_text(module)
    result = {"viewer": str(output / "viewer.html"), "sha256": sha256(template.encode()), "bytes": len(template.encode()),
              "embedded_sources": payload["source_manifest"], "synthetic": True, "source_files_modified": False}
    (output / "viewer-build.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "v3")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "synthetic-design-v3.json")
    parser.add_argument("--base-viewer", type=Path, default=ROOT / "golden-city-viewer.html")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.config, args.base_viewer), ensure_ascii=False, indent=2))
