"""Package existing renders for people who cannot access workspace file links.

The gallery is plain HTML with embedded images and reports. It requires no
development server and never modifies its sources. Managed browser policy may
block file-URL navigation; offline HTML rendering is tested independently.
"""
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
IMAGE_SPECS = (
    ("aerial.png", "מבט אווירי", "המודל הסינתטי המקורי במבט מלמעלה."),
    ("street.png", "מבט מגובה הרחוב", "מצלמה בגובה 1.7 מטר במודל הסינתטי."),
    ("edited-aerial.png", "לאחר שינוי מבנה A", "שינוי נקודתי בחזית ובמרפסות של מבנה A."),
    ("comparison.png", "השוואה לפני ואחרי", "מבנים B ו־C נשמרו: הגיאומטריה, החומרים ומסכות האובייקטים זהים. תאורה וצללים עשויים להשתנות."),
)
REPORT_SPECS = (
    ("examples/renders/acceptance-report.json", "דוח מבחן הקבלה"),
    ("examples/validation-report.json", "דוח הבדיקות העדכני"),
    ("examples/delivery-report.json", "דוח המסירה הקודם"),
    ("examples/browser/browser-report.json", "דוח בדיקות הממשק הקודם"),
)
SOURCE_FILES = (
    "examples/renders/baseline-project.json",
    "examples/renders/edited-project.json",
    "examples/renders/aerial.scene.json",
    "examples/renders/street.scene.json",
    "examples/renders/edited-aerial.scene.json",
    "examples/renders/aerial.metadata.json",
    "examples/renders/street.metadata.json",
    "examples/renders/edited-aerial.metadata.json",
    "examples/renders/aerial.obj",
    "examples/renders/aerial.mtl",
    "examples/renders/aerial.glb",
    "examples/renders/edited-aerial.obj",
    "examples/renders/edited-aerial.mtl",
    "examples/renders/edited-aerial.glb",
)


def _data_url(payload, mime):
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _png_dimensions(payload):
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise ValueError("קובץ תמונת הדמיה אינו PNG תקין")
    width, height = struct.unpack(">II", payload[16:24])
    if width == 0 or height == 0:
        raise ValueError("תמונת הדמיה ריקה")
    return width, height


def _write_if_changed(path, payload):
    """Avoid replacing an identical file while browsers may be reading it."""
    if path.exists() and path.read_bytes() == payload:
        return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _validation_passed(evidence):
    return evidence.get("status") == "passed" if "status" in evidence else evidence.get("passed") is True


def create_contact_sheet(source_pngs, output_path):
    """Make a labeled overview of three actual render files, without rerendering."""
    sources = [Path(path) for path in source_pngs]
    if len(sources) != 3:
        raise ValueError("דף התצוגה דורש שלוש תמונות רינדור")
    titles = ("AERIAL VIEW", "STREET VIEW | camera 1.7 m", "EDITED BUILDING A")
    sheet = Image.new("RGB", (2400, 810), "#f5f2e9")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 26)
        small = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default(size=26)
        small = ImageFont.load_default(size=20)
    draw.text((24, 18), "GOLDEN CITY | THREE ACTUAL BLENDER RENDERS", font=font, fill="#242f3a")
    for index, (source, title) in enumerate(zip(sources, titles)):
        with Image.open(source) as original:
            original.load()
            if original.format != "PNG":
                raise ValueError("דף התצוגה מקבל תמונות PNG בלבד")
            dimensions = original.size
            preview = ImageOps.contain(original.convert("RGB"), (768, 576), Image.Resampling.LANCZOS)
        left = index * 800
        draw.text((left + 16, 78), title, font=small, fill="#242f3a")
        draw.rectangle((left + 16, 116, left + 784, 692), fill="#e9e5dc")
        sheet.paste(preview, (left + 16 + (768 - preview.width) // 2, 116 + (576 - preview.height) // 2))
        draw.text((left + 16, 706), f"{source.name} | original {dimensions[0]} x {dimensions[1]}", font=small, fill="#242f3a")
    draw.text((24, 760), "Synthetic schematic model | B/C geometry and materials unchanged | no cadastral or religious certification", font=small, fill="#242f3a")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG")
    with Image.open(output_path) as verification:
        verification.load()
        if verification.size != sheet.size:
            raise ValueError("בדיקת דף התצוגה נכשלה")
    return {"path": str(output_path.resolve()), "width": sheet.width, "height": sheet.height, "source_files": [str(path.resolve()) for path in sources]}


def _gallery(images, reports):
    cards = []
    for image in images:
        url = _data_url(image["payload"], "image/png")
        title = html.escape(image["title"])
        cards.append(
            f'<section class="card" id="{html.escape(image["name"])}">'
            f'<h2>{title}</h2><p>{html.escape(image["caption"])}</p>'
            f'<a class="download" download="{html.escape(image["name"])}" href="{url}">הורדת התמונה המקורית (PNG)</a>'
            f'<img src="{url}" width="{image["width"]}" height="{image["height"]}" alt="{title}">'
            '</section>'
        )
    report_blocks = []
    for report in reports:
        report_blocks.append(
            f'<details><summary>{html.escape(report["title"])}</summary>'
            '<p>הדוח מתעד את הבדיקות המדווחות בתוכו. בדיקת פתיחת הקבצים אינה אימות קדסטרי.</p>'
            f'<a class="download" download="{html.escape(report["name"])}" href="{_data_url(report["payload"], "application/json")}">הורדת הדוח</a>'
            f'<pre dir="ltr">{html.escape(report["payload"].decode("utf-8"))}</pre></details>'
        )
    return ('''<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Golden City — תמונות ההדמיה</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f2e9;color:#242f3a;font-family:Arial,sans-serif;line-height:1.65}
main{max-width:1400px;margin:auto;padding:24px}h1{margin-top:0}h2{margin:0 0 8px;font-size:1.45rem}
header,.card,.reports{background:white;border:1px solid #e2dac9;border-radius:12px;padding:22px;margin:0 0 24px}
.card img{display:block;width:100%;height:auto;margin-top:18px;border-radius:6px}.download{display:inline-block;color:#244e75;padding:8px 0;font-weight:bold}
.note{border-right:4px solid #b98b29;padding-right:14px}.grid{display:grid;grid-template-columns:1fr;gap:0}
details{border-top:1px solid #e2dac9;padding:15px 0}summary{cursor:pointer;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f6f6;padding:14px;text-align:left;font-size:13px}
@media(min-width:1000px){.grid{grid-template-columns:1fr 1fr;gap:24px}.card:last-child{grid-column:1 / -1}}
</style></head><body><main>
<header><h1>Golden City — תמונות ההדמיה</h1>
<p>תמונות רינדור אמיתיות ודף תצוגה מרכז. הקובץ הזה כולל את התמונות והדוחות בתוכו, ואפשר לפתוח אותו בדפדפן גם ללא חיבור לאינטרנט.</p>
<p>לצפייה: פתחו את הקובץ <b dir="ltr">golden-city-gallery.html</b> בדפדפן. לכל תמונה יש קישור להורדת קובץ PNG המקורי. בחבילת ZIP אפשר לחלץ את הקבצים ולפתוח כל תמונה בנפרד.</p>
<p class="note">זוהי הדמיה סכמטית של מודל סינתטי, ללא אימות קדסטרי או טופוגרפיה מדודה. ניתוח המרפסות אינו אישור כשרות.</p>
</header><div class="grid">''' + "".join(cards) + '''</div>
<section class="reports"><h2>דוחות הבדיקות המצורפים</h2>''' + "".join(report_blocks) + '''</section>
</main></body></html>''').encode("utf-8")


def create_delivery_bundle(output_dir, *, source_root=ROOT, include_blend=False, require_models=True):
    """Create byte-preserving PNG copies, an offline gallery, and a stable ZIP.

    Four acceptance PNGs are required. A fifth image is an actual contact sheet.
    Fresh baseline and edited OBJ/MTL/GLB exports are required by default.
    require_models=False is an explicit image-only bundle, not model validation.
    Reports and project snapshots are included when present. Blender scenes are optional because they require
    Blender; they are never needed to view the images. The return value contains
    absolute output paths and public file metadata, without embedded image data.
    """
    source_root = Path(source_root).resolve()
    output_dir = Path(output_dir).resolve()
    payloads, images, reports, files = {}, [], [], []

    def add_source(relative, *, mime="application/json", title=None, required=False):
        source = source_root / relative
        if not source.is_file():
            if required:
                raise FileNotFoundError(f"חסר קובץ לתיק המסירה: {relative}")
            return None
        payload = source.read_bytes()
        name = source.name
        if name in payloads:
            raise ValueError(f"שם קובץ כפול בחבילת המסירה: {name}")
        payloads[name] = payload
        item = {"name": name, "source": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "mime": mime}
        if source.suffix in {".obj", ".glb", ".blend"}:
            metadata_path = source.with_suffix(".metadata.json")
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                model_expected = metadata.get("model_expectations", {}).get(name)
                if model_expected:
                    # The independent reader and every extracted ZIP member
                    # must match the source scene, beyond merely being parsable.
                    item = {**model_expected, **item}
                elif require_models:
                    raise ValueError(f"חסרות ציפיות אימות למודל: {name}")
            elif require_models:
                raise FileNotFoundError(f"חסר תיעוד אימות למודל: {metadata_path.name}")
        files.append(item)
        return {**item, "payload": payload, "title": title}

    for name, title, caption in IMAGE_SPECS:
        image = add_source(f"examples/renders/{name}", mime="image/png", title=title, required=True)
        image["width"], image["height"] = _png_dimensions(image["payload"])
        image["caption"] = caption
        images.append(image)
    # Prepare the new contact sheet outside the destination so a corrupt source
    # cannot leave a partly published delivery directory.
    with tempfile.TemporaryDirectory(prefix="golden-contact-") as temporary:
        contact_path = Path(temporary) / "contact-sheet.png"
        create_contact_sheet([source_root / "examples/renders" / name for name, *_ in IMAGE_SPECS[:3]], contact_path)
        contact_payload = contact_path.read_bytes()
    contact_item = {"name": "contact-sheet.png", "source": "generated from three actual render PNGs", "bytes": len(contact_payload), "sha256": hashlib.sha256(contact_payload).hexdigest(), "mime": "image/png"}
    payloads[contact_item["name"]] = contact_payload
    files.append(contact_item)
    contact_width, contact_height = _png_dimensions(contact_payload)
    images.append({**contact_item, "payload": contact_payload, "width": contact_width, "height": contact_height, "title": "שלוש תמונות הרינדור יחד", "caption": "דף תצוגה מרכז שהורכב מהתמונות המקוריות: מבט אווירי, מבט רחוב ומבנה A לאחר העריכה."})
    for relative, title in REPORT_SPECS:
        report = add_source(relative, title=title)
        if report:
            # Reject misleading HTML reports rather than labeling them as JSON.
            json.loads(report["payload"])
            reports.append(report)
    for relative in SOURCE_FILES:
        mime = "model/gltf-binary" if relative.endswith(".glb") else "text/plain" if relative.endswith((".obj", ".mtl")) else "application/json"
        add_source(relative, mime=mime, required=require_models and relative.endswith((".obj", ".mtl", ".glb")))
    if include_blend:
        for name in ("aerial.blend", "street.blend", "edited-aerial.blend"):
            add_source(f"examples/renders/{name}", mime="application/octet-stream")

    payloads["golden-city-gallery.html"] = _gallery(images, reports)
    readme = """Golden City — הוראות פתיחת החומרים

1. חלצו את חבילת ה־ZIP לתיקייה רגילה.
2. פתחו golden-city-gallery.html בדפדפן. אין צורך בשרת או באינטרנט.
3. אפשר לפתוח aerial.png, street.png, edited-aerial.png ו־comparison.png
   ישירות בתוכנת התמונות במחשב או בטלפון.
4. קובצי JSON הם דוחות ותיעוד המודל. קובצי GLB ו־OBJ הם מודלים
   לתוכנת תלת־ממד. השאירו OBJ ואת קובץ MTL שלו יחד באותה תיקייה.
   קובצי BLEND, אם צורפו, דורשים Blender. אין צורך בהם לצפייה בתמונות.

המודל סינתטי וההדמיה סכמטית. אין אימות קדסטרי או אישור כשרות.
התמונות המקוריות נשמרו ללא שינוי; זו אריזה מחדש של תוצאות קיימות.
"""
    payloads["OPEN-ME.txt"] = readme.encode("utf-8")
    for name in ("golden-city-gallery.html", "OPEN-ME.txt"):
        payload = payloads[name]
        files.append({"name": name, "source": "generated delivery instructions", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "mime": "text/html" if name.endswith(".html") else "text/plain"})
    manifest = {"format_version": 1, "synthetic": True, "existing_renders_reused": True, "source_images_copied_verbatim": True, "models_required": require_models, "user_access": "not_verified", "manifest_hash_excluded": True, "validation_report_in_archive": False, "files": sorted(files, key=lambda item: item["name"])}
    payloads["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    archive_path = output_dir / "golden-city-materials.zip"
    from delivery_validation import validate_archive, validate_file, validate_links

    # Stage and independently open every output, then extract and reopen the
    # completed ZIP. Do not replace an existing delivery after a failed check.
    with tempfile.TemporaryDirectory(prefix="golden-delivery-check-") as temporary:
        stage = Path(temporary)
        for name, payload in sorted(payloads.items()):
            (stage / name).write_bytes(payload)
        expected_files = {item["name"]: item for item in manifest["files"]}
        validations = {}
        for name in sorted(payloads):
            validations[name] = validate_file(stage / name, expected=expected_files.get(name), artifact_role="delivery_output")
            if not _validation_passed(validations[name]):
                raise ValueError(f"בדיקת פתיחת הקובץ נכשלה: {name}: {validations[name].get('details', {})}")
        links = validate_links(stage / "golden-city-gallery.html", root=stage)
        if not _validation_passed(links):
            raise ValueError(f"בדיקת קישורי הגלריה נכשלה: {links.get('details', {})}")
        staged_archive = stage / archive_path.name
        with zipfile.ZipFile(staged_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, payload in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        archive_validation = validate_archive(staged_archive, expected_manifest=manifest)
        if not _validation_passed(archive_validation):
            raise ValueError(f"בדיקת חילוץ החבילה נכשלה: {archive_validation.get('details', {})}")
        for name, evidence in validations.items():
            evidence["path"] = str(output_dir / name)
            evidence["validated_path_role"] = "staged byte-identical copy of delivered file"
        archive_validation["path"] = str(archive_path)
        archive_validation["validated_path_role"] = "staged byte-identical copy of delivered archive"
        validation_report = {"status": "passed", "passed": True, "user_accessibility": "not_verified", "files": validations, "links": links, "archive": archive_validation, "evidence_note": "Artifact opening and archive extraction were verified in the cloud environment. User download and local file navigation were not verified."}
        payloads["delivery-validation.json"] = (json.dumps(validation_report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        staged_report = stage / "delivery-validation.json"
        staged_report.write_bytes(payloads["delivery-validation.json"])
        validation_report_evidence = validate_file(staged_report, artifact_role="external_validation_report")
        if not _validation_passed(validation_report_evidence):
            raise ValueError("בדיקת דוח האימות נכשלה")
        validation_report_evidence["path"] = str(output_dir / staged_report.name)
        validation_report_evidence["validated_path_role"] = "staged byte-identical copy of delivered report"
        payloads[archive_path.name] = staged_archive.read_bytes()
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in sorted(payloads.items()):
            _write_if_changed(output_dir / name, payload)
        final_integrity = []
        for name, payload in sorted(payloads.items()):
            final_bytes = (output_dir / name).read_bytes()
            expected_sha = hashlib.sha256(payload).hexdigest()
            if len(final_bytes) != len(payload) or hashlib.sha256(final_bytes).hexdigest() != expected_sha:
                raise ValueError(f"הקובץ הסופי שונה מהקובץ המאומת: {name}")
            final_integrity.append({"name": name, "bytes": len(final_bytes), "sha256": expected_sha, "delivered_bytes_match_validation": True})
    return {
        "gallery_path": str(output_dir / "golden-city-gallery.html"),
        "archive_path": str(archive_path),
        "manifest_path": str(output_dir / "manifest.json"),
        "contact_sheet_path": str(output_dir / "contact-sheet.png"),
        "validation_report_path": str(output_dir / "delivery-validation.json"),
        "validation_passed": True,
        "validation_report_evidence": validation_report_evidence,
        "final_integrity": final_integrity,
        "user_access": "not_verified",
        "images": [{key: value for key, value in image.items() if key != "payload"} for image in images],
        "files": manifest["files"],
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validate and package the existing Golden City render results.")
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables")
    parser.add_argument("--include-blend", action="store_true", help="Also independently reopen and include Blender scene files.")
    arguments = parser.parse_args()
    print(json.dumps(create_delivery_bundle(arguments.output, include_blend=arguments.include_blend), ensure_ascii=False, indent=2))
