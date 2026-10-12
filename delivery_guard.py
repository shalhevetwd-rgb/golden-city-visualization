"""A shared delivery gate for every project's generated files.

Evidence is cached by bytes, role and expectations. Changed bytes are checked
again; successful server reads do not establish recipient access through chat.
"""
import copy
import hashlib
import json
from pathlib import Path
from threading import RLock

_LOCK=RLock()
_CACHE={}

def verify_for_delivery(path, *, expected=None, artifact_role='output'):
    from delivery_validation import ValidationError, validate_file
    path=Path(path).resolve()
    if not path.is_file():raise ValidationError('הקובץ המבוקש אינו קיים')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if expected and expected.get('sha256',digest)!=digest:
        raise ValidationError('תוכן הקובץ שונה מהתוצר שנרשם למסירה')
    expectation_key=json.dumps(expected or {},sort_keys=True,ensure_ascii=False)
    key=(str(path),digest,artifact_role,expectation_key)
    with _LOCK:
        # HTML references can change without changing the document's own bytes.
        # Reopen linked targets every time instead of reusing stale evidence.
        if key not in _CACHE or path.suffix.lower() in {'.html','.htm'}:
            evidence=validate_file(path,expected={**(expected or {}),'sha256':(expected or {}).get('sha256',digest)},artifact_role=artifact_role)
            if evidence.get('status')!='passed':raise ValidationError('בדיקת המסירה לא הושלמה')
            _CACHE[key]=copy.deepcopy(evidence)
        evidence=copy.deepcopy(_CACHE[key])
    evidence['user_accessibility']='not_verified'
    evidence['delivery_gate']='independent_open_and_byte_hash'
    return evidence
