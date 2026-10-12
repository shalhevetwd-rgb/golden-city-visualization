"""Source-bound project workflow. Human review records are not certification.

This module does not infer cadastral boundaries, dimensions or heights from a
picture. Geometry remains the supplied, validated project geometry.
"""
import base64
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

from state import commit, save_atomic

REQUIREMENTS = {
    'parcel_geometry_verified': 'גבולות חלקות ממקור מוסמך ובדיקת שיוך לכתובת ולגוש',
    'alignment_approved': 'תוכנית העמדה, נקודות בקרה, קנה מידה וכיוון צפון',
    'building_positions_verified': 'מיקום המבנים ותחום ההתערבות ביחס לגבולות',
    'floors_verified': 'מספר קומות מתוך תוכנית מקור',
    'heights_verified': 'גבהים ומפלסי בסיס עם מערכת גובה מוגדרת',
    'terrain_verified': 'טופוגרפיה ומקור נתוני גובה',
}


def dossier(request):
    name = str(request.get('name', '')).strip()[:120]
    address = str(request.get('address', '')).strip()[:240]
    block = str(request.get('block', '')).strip()
    if not name or not address or not re.fullmatch(r'[1-9][0-9]{0,8}', block):
        raise ValueError('נדרשים שם מתחם, כתובת ומספר גוש חיובי')
    return {'id': uuid.uuid4().hex, 'name': name, 'identity': {
        'address': address, 'block': block,
        'parcels': str(request.get('parcels', '')).strip()[:240],
        'status': 'user_provided_not_verified'},
        'synthetic': False, 'crs': None, 'boundary': None, 'buildings': [],
        'errors': ['לא נקלטו גבולות ולא נקלטה תוכנית העמדה'],
        'approved': False, 'version': 1, 'topography': 'unknown',
        'cameras': {}, 'assets': [], 'renders': [], 'inputs': {}, 'evidence': {}}


def geometry_payload(project):
    """Review binding excludes appearance and cameras, includes physical edits."""
    fields = ('id', 'height', 'floors', 'base_elevation', 'balconies', 'slabs')
    return {'crs': project.get('crs'), 'boundary': project.get('boundary'),
            'buildings': [{'geometry': item.get('geometry'),
                           'properties': {k: item.get('properties', {}).get(k)
                                          for k in fields}}
                          for item in project.get('buildings', [])],
            'overhead_obstructions': project.get('overhead_obstructions', []),
            'topography': project.get('topography', 'unknown'),
            'alignment': project.get('alignment')}


def ensure_geometry_record(project, base_dir):
    identifier = project['id']
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('מזהה פרויקט לא תקין')
    raw = json.dumps(geometry_payload(project), sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    relative = f'geometry/{identifier}-{digest}.json'
    path = Path(base_dir) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError('קובץ גיאומטריה שמור נפגם')
    if not path.exists():
        path.write_bytes(raw)
    # Independent reopen, not merely a hash of the in-memory JSON.
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('בדיקת גרסת הגיאומטריה נכשלה')
    project['geometry_version_sha256'] = digest
    project['geometry_manifest_path'] = relative
    return project


def intake(project, request, base_dir):
    from engine.intake import inspect
    name = str(request.get('name', '')).strip()
    if not name or len(name) > 200 or Path(name).name != name or '\\' in name:
        raise ValueError('נדרש שם קובץ ללא נתיב')
    suffix = Path(name).suffix.lower()
    if suffix not in {'.pdf', '.dxf', '.dwg', '.png', '.jpg', '.jpeg',
                      '.geojson', '.obj', '.glb'}:
        raise ValueError('סיומת קובץ אינה מיועדת לקליטת מקור')
    content = base64.b64decode(request.get('content_base64', ''), validate=True)
    if not content or len(content) > 50 * 1024 * 1024:
        raise ValueError('נדרש קובץ מקור לא ריק עד 50 MiB')
    identifier = uuid.uuid4().hex
    relative = f'assets/{identifier}{suffix}'
    path = Path(base_dir) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    try:
        result = inspect(path)
        if not result.get('supported', False):
            raise ValueError(result.get('meaning', 'תוכן הקובץ אינו נתמך לחילוץ'))
        if hashlib.sha256(path.read_bytes()).hexdigest() != result['sha256']:
            raise ValueError('קובץ המקור השתנה בזמן הקליטה')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    result = {**result, 'name': name, 'source_id': identifier,
              'georeferencing_verified': False}
    # Keep large extracted meshes out of every history snapshot and HTTP reply.
    # The complete inspection and source bytes remain separately available.
    inspection_raw = json.dumps(result, ensure_ascii=False, allow_nan=False,
                                separators=(',', ':')).encode()
    if len(inspection_raw) > 262_144:
        report_name = identifier + '.inspection.json'
        report_path = path.parent / report_name
        report_path.write_bytes(inspection_raw)
        reopened = report_path.read_bytes()
        if reopened != inspection_raw or not isinstance(json.loads(reopened), dict):
            raise ValueError('בדיקת דוח הקליטה נכשלה')
        def compact(value):
            if isinstance(value, list):
                if len(value) > 1000:
                    return {'stored_in_full_inspection': True, 'count': len(value)}
                return [compact(item) for item in value]
            if isinstance(value, dict):
                return {key: compact(item) for key, item in value.items()}
            if isinstance(value, str) and value.startswith('data:') and len(value) > 131_072:
                return 'stored_in_full_inspection'
            return value
        result = compact(result)
        result['full_inspection'] = {'url': '/assets/' + report_name,
            'bytes': len(reopened), 'sha256': hashlib.sha256(reopened).hexdigest(),
            'notice': 'מערכי גיאומטריה גדולים שמורים בדוח הנפרד; המקור לא שונה'}
    updated = copy.deepcopy(project)
    updated.setdefault('inputs', {})[identifier] = {
        'path': relative, 'sha256': result['sha256'], 'name': name,
        'status': 'user_provided_not_verified'}
    updated.setdefault('intake', []).append(result)
    updated = commit(project, updated, 'source:' + suffix)
    ensure_geometry_record(updated, base_dir)
    return updated, result


def readiness(project, base_dir):
    from engine.constraints import site_gate
    ensure_geometry_record(project, base_dir)
    gate = site_gate(project, base_dir=Path(base_dir))
    synthetic = project.get('synthetic') is True
    missing = [REQUIREMENTS.get(k, k) for k in gate.get('missing', [])]
    geometry_valid = bool(project.get('buildings')) and not project.get('errors')
    if not geometry_valid:
        missing.insert(0, 'גיאומטריה תקינה של גבולות ומבנים במערכת קואורדינטות מטרית')
    if not project.get('approved'):
        missing.append('אישור משתמש להעמדה לאחר בדיקת השגיאות')
    if not synthetic:
        # The current reusable renderer does not yet consume measured terrain
        # or reproduce the complete measured surrounding environment.
        missing.append('מנוע מצב קיים עם טופוגרפיה מדודה ושימור סביבת האתר טרם הושלם')
    ready = synthetic and geometry_valid and project.get('approved') is True
    return {**gate, 'ready_for_final_render': ready,
            'final_render_allowed': ready, 'missing': missing,
            'classification': 'synthetic_experiment' if synthetic else 'unverified_real_site',
            'certification': False, 'accurate_real_site': False,
            'geometry_version_sha256': project['geometry_version_sha256'],
            'workflow': [
                {'stage': 'זיהוי המתחם', 'status': 'synthetic' if synthetic else 'user_provided'},
                {'stage': 'אימות מידע גיאוגרפי ותכנון',
                 'status': 'not_applicable_synthetic' if synthetic else 'review_recorded' if gate.get('evidence_complete') else 'missing'},
                {'stage': 'בדיקת גיאומטריה', 'status': 'passed' if geometry_valid else 'missing'},
                {'stage': 'אישור העמדה', 'status': 'user_confirmed' if project.get('approved') else 'missing'},
                {'stage': 'רינדור ניסוי', 'status': 'available' if ready else 'blocked'},
                {'stage': 'רינדור מדויק של אתר אמיתי', 'status': 'not_implemented'},
            ]}


def record_review(project, request, base_dir):
    supplied = request.get('evidence')
    if not isinstance(supplied, dict) or not supplied or set(supplied) - set(REQUIREMENTS):
        raise ValueError('נדרשת רשומת סקירה עבור שדות האימות המוגדרים')
    updated = copy.deepcopy(project)
    ensure_geometry_record(updated, base_dir)
    for field, record in supplied.items():
        if not isinstance(record, dict):
            raise ValueError('רשומת סקירה חייבת להיות אובייקט')
        updated.setdefault('evidence', {})[field] = copy.deepcopy(record)
        updated[field] = True
    from engine.constraints import site_gate
    gate = site_gate(updated, base_dir=Path(base_dir))
    bad = set(supplied) & set(gate.get('missing', []))
    if bad:
        raise ValueError('סקירה אינה קשורה למקור ולגרסת הגיאומטריה הנוכחיים: ' + ', '.join(sorted(bad)))
    updated = commit(project, updated, 'review_recorded')
    updated['review_notice'] = 'נרשמה סקירה אנושית; זהות הסוקר וסמכותו אינן מאומתות אוטומטית. אין אישור דיוק או כשרות.'
    return updated


def capabilities():
    """Capabilities are backed by the repository's executed intake fixtures."""
    root = Path(__file__).parent
    receipt_path = root / 'reports' / 'intake-v3-checks.json'
    try:
        receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    except (ValueError, OSError):
        receipt = {}
    receipt_valid = (receipt.get('tests_run', 0) > 0 and receipt.get('failures') == 0
                     and receipt.get('errors') == 0 and receipt.get('skipped') == 0
                     and receipt.get('inspection_module_sha256') ==
                     hashlib.sha256((root/'engine'/'intake.py').read_bytes()).hexdigest())
    tested = receipt.get('formats', {}) if receipt_valid else {}
    descriptions = [
        ('.png/.jpg', 'בדיקת תמונה וממדים; אין חילוץ אוטומטי של קנה מידה או מיקום'),
        ('.pdf', 'עמודים, טקסט ותצוגה; אין הסקת גיאומטריית בניין מסריקה'),
        ('.dxf', 'חילוץ ישויות ASCII שנבדקו; יחידות ומיקום מחייבים מקור ובקרה'),
        ('.geojson', 'קריאת גיאומטריה; יבוא גבולות מטריים ואימות מקור הם פעולות נפרדות'),
        ('.obj', 'גיאומטריה ואובייקטים; הפורמט אינו שומר מצלמות או CRS'),
        ('.glb', 'גיאומטריה, אובייקטים ומצלמות אם קיימות; אין הסקת קומות או CRS'),
    ]
    def tested_extension(ext):
        alternatives = ['.' + v.lstrip('.') for v in ext.split('/')]
        for extension in alternatives:
            record = tested.get(extension, {})
            fixture = root / str(record.get('fixture', ''))
            if record.get('passed') is not True or not fixture.is_file() or not fixture.resolve().is_relative_to((root/'tests'/'fixtures').resolve()):
                return False
            if hashlib.sha256(fixture.read_bytes()).hexdigest() != record.get('sha256'):
                return False
        return True
    return {'formats': [{'extension': ext, 'status': 'tested_extraction' if tested_extension(ext) else 'implemented_not_verified',
                         'label': ext, 'details': detail} for ext, detail in descriptions] +
                       [{'extension': '.dwg', 'status': 'unsupported', 'label': 'DWG',
                         'details': 'לא נמצא ממיר מורשה שנבדק; אפשר לייצא DXF מתוכנת המקור'}],
            'rendering': {'synthetic_experiment': True, 'accurate_real_site_final': False,
                          'professional_architectural_quality_certified': False},
            'accuracy': 'מקורות, העמדה, גבהים וטופוגרפיה אינם מוסקים מתמונות',
            'sukkah': {'geometry_screen': True, 'automatic_halachic_approval': False}}
