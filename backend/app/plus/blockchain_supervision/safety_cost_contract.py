"""分包安全生产费的合同单独约定；工程层计提及实际履约另核。"""
import re
from decimal import Decimal

from .finding_text import bounded_finding_message
from .models import BsFinding, STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE
from .parsing import material_reading_gap

ITEM_ID = 'RC-A10-cost'
SOURCE = '财资〔2022〕136号第十七、十八条，PDF物理第12—13页；仅合同约定维度'
LIMIT = ('仅核合同单独约定；公路工程1.5%为第十七条工程层建筑安装工程造价提取标准，'
         '实施前已完成招投标且签约项目按原标准；分包不再重复提取，实际支付及专款专用另核')
_FEE = r'安全生产费用?'
_NUMBER = r'\d[\d,，]*(?:\.\d+)?'
_BASE = r'建筑安装工程造价|建筑安装工程费|建安工程造价|建安工程费|不含税合同价|含税合同价|分包合同额|合同金额'
_AMOUNT = re.compile(_FEE + r'(?:单列(?:金额)?|单独约定|单独列明|金额)?(?:为|计|[:：])?'
                     r'(?P<value>' + _NUMBER + r')(?P<unit>万元|元)')
_RATE = re.compile(_FEE + r'按(?P<base>' + _BASE + r')?的?(?P<rate>\d+(?:\.\d+)?)[%％]')
_NEGATIVE = re.compile(_FEE + r'(?:不予|不|无需|无须|不再|不得)(?:另行|单独)?(?:单列|列明|约定)'
                       r'|(?:不予|不|无需|无须|不再|不得)(?:另行|单独)?(?:单列|列明|约定)(?:企业)?' + _FEE)
_POSITIVE = re.compile(_FEE + r'(?:应当?|须|需|必须)?(?:予以)?(?:单列|单独约定|单独列明)'
                       r'|(?:单列|单独约定|单独列明)(?:企业)?' + _FEE)


def _repeat_states(text):
    states = set()
    for match in re.finditer(r'(?:分包单位|分包人)(.{0,30}?)提取', text):
        prefix = text[:match.start()]
        target = (re.search(_FEE, match[0]) or re.match(r'(?:企业)?' + _FEE, text[match.end():])
                  or re.search(_FEE + r'(?:应当?|须|需)?由$', prefix))
        if not target:
            continue
        if re.search(r'不再|无需|无须|不得|不应|不必|不需|禁止', match[1]) or re.search(
                r'不得|不应|禁止|无需|无须|不需要', prefix[-6:]):
            states.add('not_required')
        elif re.search(r'应当?|须|需|必须|自行|负责', match[1]):
            states.add('required')
    return sorted(states)


def extract_cost_terms(text):
    """数值只归属紧邻安全费标签的支持版式；其他百分比仍保留在原始责任记录。"""
    terms = []
    for raw in re.split(r'[；;。！？\n]', text):
        compact = re.sub(r'\s+', '', raw)
        if not re.search(_FEE, compact):
            continue
        amounts = [str(Decimal(m['value'].replace(',', '').replace('，', '')) *
                       (10000 if m['unit'] == '万元' else 1)) for m in _AMOUNT.finditer(compact)]
        rates = [{'rate_percent': m['rate'], 'base': m['base']} for m in _RATE.finditer(compact)]
        negative = bool(_NEGATIVE.search(compact))
        positive = any(not re.search(r'不|无需|无须|不得|禁止', compact[max(0, m.start()-4):m.start()])
            for m in _POSITIVE.finditer(compact)) or any(
            re.match(r'(?:计取|计列|计算)?[，,]?(?:并)?单列', compact[m.end():])
            for m in _RATE.finditer(compact))
        repeat_states = _repeat_states(compact)
        uncertain = bool(re.search(r'暂定|另议|约为|左右|不低于|不少于|不超过|不大于|最多|至少|[<>≤≥~～]', compact))
        terms.append({'quote': raw.strip(), 'separate': 'conflict' if negative and positive else
            'no' if negative else 'yes' if positive else 'unknown', 'amounts_yuan': amounts,
            'rates': rates, 'repeat_extraction_states': repeat_states,
            'repeat_extraction_required': 'required' in repeat_states, 'uncertain': uncertain,
            'performance_verified': False})
    return terms


def assess_cost_terms(rows, *, unread=False):
    terms = [term for row in rows for term in row.get('safety_cost_terms', [])]
    separate = {term['separate'] for term in terms} - {'unknown'}
    valid = [term for term in terms if term['separate'] == 'yes']
    amounts = {Decimal(value) for term in valid for value in term['amounts_yuan']}
    rates = {(Decimal(rate['rate_percent']), rate['base']) for term in valid for rate in term['rates']}
    repeat_states = {state for term in terms for state in term['repeat_extraction_states']}
    result = {'amounts_yuan': sorted(str(value) for value in amounts),
              'rates': [{'rate_percent': str(rate), 'base': base} for rate, base in sorted(rates, key=str)],
              'performance_verified': False, 'statutory_rate_verified': False}
    if unread:
        status, reason = STATUS_UNDECIDABLE, '合同存在未确认页，需补读并核对完整费用约定'
    elif ('conflict' in separate or len(separate) > 1 or len(amounts) > 1 or len(rates) > 1
          or len(repeat_states) > 1):
        status, reason = STATUS_UNDECIDABLE, '安全费单独约定或金额/比例候选冲突，需核对各原页及有效合同版本'
    elif any(term['uncertain'] for term in terms):
        status, reason = STATUS_UNDECIDABLE, '安全费存在暂定、范围或待议表述，需核对最终合同约定'
    elif 'no' in separate or any(term['repeat_extraction_required'] for term in terms):
        status, reason = STATUS_HIT, ('已提交合同明确不单独约定安全生产费，需核对修订'
            if 'no' in separate else '已提交合同要求分包单位提取安全生产费，需按第十八条核对分包不再重复提取的责任边界')
    elif valid and ((amounts and min(amounts) > 0) or
                    (rates and all(rate > 0 and base for rate, base in rates))):
        status, reason = STATUS_MISS, '已见安全生产费单独约定及明确金额/计算方式，仅该合同约定维度符合'
    else:
        status, reason = STATUS_UNDECIDABLE, '安全生产费单独约定、正数金额或明确计算基数尚未唯一识别，需核对合同原页'
    return {**result, 'status': status, 'reason': reason}


def run_cost_contract_check(task, materials, rows):
    if not rows:
        return []
    contracts = [m for m in materials if getattr(m, 'role', None) in {'sub', 'labor'}]
    result = assess_cost_terms(rows, unread=any(material_reading_gap(m) is not None for m in contracts))
    refs = [{**row, 'safety_cost_assessment': result} for row in rows]
    anchor = refs[0]
    return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind='completeness', status=result['status'],
        finding_semantics='machine_conclusion', source=SOURCE,
        message=bounded_finding_message('安全生产费合同约定：' + result['reason'] + '；' + LIMIT),
        material_id=anchor['material_id'], page=anchor['page'], quote=anchor['quote'],
        location_mode='page', evidence_json=refs)]
