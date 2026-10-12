"""Project-defined sukkah screening; never an automatic halachic decision."""
import math
from shapely.geometry import shape
from balconies import analyze_balcony
from spatial import polygon_geometry


def number(value, name, positive=True):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('מידה סופית וחיובית נדרשת: ' + name)
    return float(value)


def screen(project, building_id, balcony_id, policy=None, details=None, overhead_complete=False):
    analysis = analyze_balcony(project, building_id, balcony_id, overhead_complete=overhead_complete)
    policy, details = policy or {}, details or {}
    if not isinstance(policy, dict) or not isinstance(details, dict):
        raise ValueError('מדיניות ונתוני הסוכה חייבים להיות אובייקטים')
    missing, checks = [], {}
    provenance = policy.get('provenance', {})
    if not isinstance(provenance, dict) or any(not isinstance(provenance.get(k), str) or not provenance[k].strip()
                                              for k in ('authority', 'reference', 'version')):
        missing.append('מדיניות פרויקט: פוסק או גורם מוסמך, מקור וגרסה')
    required = ('min_width_m', 'min_length_m', 'min_height_m')
    dimensions = {}
    for key in required:
        if key not in policy:
            missing.append('דרישת מדיניות: ' + key)
        else:
            dimensions[key] = number(policy[key], key)
    candidate = None
    if details.get('sukkah_footprint') is None:
        missing.append('פוליגון מלבני של שטח הסוכה המוצע בתוך המרפסת')
    else:
        candidate = polygon_geometry(details['sukkah_footprint'], 'sukkah footprint')
        if candidate.interiors or candidate.geom_type != 'Polygon':
            raise ValueError('נדרש שטח סוכה מלבני ללא חורים')
        rectangle = candidate.minimum_rotated_rectangle
        if len(list(candidate.exterior.coords)) != 5 or candidate.symmetric_difference(rectangle).area > 1e-7:
            raise ValueError('בדיקת המידות דורשת מלבן מפורש; אין להסיק מידות מפוליגון לא סדיר')
        points = list(candidate.exterior.coords)[:-1]
        edge_lengths = [math.dist(points[i], points[(i + 1) % 4]) for i in range(4)]
        width, length = sorted(edge_lengths)[:1][0], max(edge_lengths)
        checks['candidate_inside_balcony'] = shape(analysis['top_view']['balcony']).covers(candidate)
        checks['candidate_clear_projection'] = shape(analysis['top_view']['open']).covers(candidate)
        if len(dimensions) == 3:
            minimums = sorted((dimensions['min_width_m'], dimensions['min_length_m']))
            checks['plan_dimensions'] = width >= minimums[0] and length >= minimums[1]
        measured_dimensions = {'width_m': width, 'length_m': length, 'area_m2': candidate.area}
    if 'clear_height_m' not in details:
        missing.append('גובה פנוי מדוד או מתוכנן מעל שטח הסוכה')
    else:
        height = number(details['clear_height_m'], 'clear_height_m')
        if 'min_height_m' in dimensions:
            checks['clear_height'] = height >= dimensions['min_height_m']
    for key, label in (('measurements_source', 'מקור למידות ולגובה'),
                       ('vertical_datum', 'מערכת גובה משותפת למרפסת ולבליטות')):
        if not isinstance(details.get(key), str) or not details[key].strip():
            missing.append(label)
    if not overhead_complete:
        missing.append('רשימת בליטות וקירויים מלאה; ההשלמה אינה מאומתת אוטומטית')
    walls = details.get('walls')
    if 'min_wall_count' in policy:
        if type(policy['min_wall_count']) is not int or policy['min_wall_count'] <= 0:
            raise ValueError('מספר הדפנות במדיניות חייב להיות מספר שלם חיובי')
        if not isinstance(walls, dict) or type(walls.get('count')) is not int or walls['count'] < 0 or not walls.get('source'):
            missing.append('מספר דפנות ומקור תכנוני')
        else:
            checks['wall_count_user_record'] = walls['count'] >= policy['min_wall_count']
    else:
        missing.append('דרישות דפנות לפרויקט')
    if 'min_wall_height_m' in policy:
        threshold = number(policy['min_wall_height_m'], 'min_wall_height_m')
        if not isinstance(walls, dict) or 'height_m' not in walls:
            missing.append('גובה דפנות מתוך מקור')
        else:
            checks['wall_height_user_record'] = number(walls['height_m'], 'wall height') >= threshold
    schach = details.get('schach')
    if not isinstance(schach, dict) or not schach.get('material') or not schach.get('source'):
        missing.append('תיאור סכך ומקור; התאמתו מחייבת בדיקה מקצועית')
    if any(value is False for value in checks.values()):
        status, passed = 'geometry_or_policy_failed', False
    elif missing:
        status, passed = 'incomplete', None
    else:
        status, passed = 'geometric_candidate_for_professional_review', True
    return {'building_id': building_id, 'balcony_id': balcony_id,
            'status': status, 'geometric_screen_passed': passed, 'checks': checks,
            'missing': missing, 'policy_provenance': provenance,
            'measured_candidate': measured_dimensions if candidate is not None else None,
            'source_classification': {'geometry': 'project_model', 'policy': 'user_supplied',
                                      'walls_and_schach': 'user_supplied_not_verified'},
            'analysis': analysis, 'professional_review_record': details.get('professional_review'),
            'professional_review_authenticated': False, 'halachic_approval': False,
            'notice': 'זהו סינון גיאומטרי לפי מדיניות שהוזנה. אין קביעה הלכתית או אישור כשרות אוטומטי.'}
