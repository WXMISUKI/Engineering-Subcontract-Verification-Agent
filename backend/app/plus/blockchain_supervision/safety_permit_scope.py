"""安许行业对应不替代专业资质、证照有效性及实际安全条件核查。"""
import json
import re
from datetime import date
from pathlib import Path

from .certificate_facts import extract_safety_permit_page
from .parsing import material_page_readable, material_reading_gap

ITEM_ID = "A2-09"
SOURCE = ("《安全生产许可证条例》（2014年修正）第二条、"
          "《建筑施工企业安全生产许可证管理规定》（2015年修正）第二条；"
          "上海市公路分包细则附件3/4提交要求；仅核许可行业，资质承包范围另行核查")
# 分段边界比证照认证宽：带电子/国名等前缀的其他证照也必须截断；
# 只用于限制字段窗口，绝不据此认定该行就是已提供的真实证照。
_NEXT_DOCUMENT_TITLE = re.compile(
    r"(?m)^[ \t]*[^:：\n]*(?:执照|许可证|证书)"
    r"(?:[ \t]*(?:[（(](?:正本|副本|电子版|复印件)[）)]|正本|副本|电子版|复印件))?[ \t]*$")
_SCOPE_LABEL = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[.、．）)]?\s*)?"
    r"(?:分包工程承包范围和内容|分包工程范围和内容|分包工程内容|劳务施工范围|分包范围|工程范围|承包范围|工程内容|工作内容)"
    r"(?:\s*[:：]\s*(.*)|\s*)$")
_NEXT_FIELD = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[.、．）)]\s*)?"
    r"(?:合同价款|合同金额|合同工期|施工工期|计划开工|计划竣工|质量标准|承包方式|工程地点|工程名称|"
    r"分包人|承包人|发包人|甲方|乙方)\s*(?:[:：]|$)")
_RESTRICTED_PERMIT = re.compile(r"不含|不包括|不得从事|仅限|限制|吊销|暂扣|注销|撤销|无效|不再有效|不适用")


def _field_count(fragment, label):
    return len(re.findall(r"(?<![\u4e00-\u9fff])(?:" + label + r")\s*[\"']?\s*[:：]", fragment))


def extract_contract_scope_records(materials):
    """保留完整范围段；旧报告60字预览不能作为行业通过的全部依据。"""
    from .contract_clause_facts import iter_contract_sections

    records = []
    for material, page, section in iter_contract_sections(materials):
        lines = section.splitlines()
        final_page = getattr(material, "pages", None) or max(
            (p.get("page", 0) for p in material.pages_json or [] if isinstance(p, dict)), default=0)
        for index, line in enumerate(lines):
            label = _SCOPE_LABEL.fullmatch(line)
            if not label:
                continue
            values = [label.group(1) or ""]
            stop = index + 1
            while stop < len(lines) and not (
                    _SCOPE_LABEL.fullmatch(lines[stop]) or _NEXT_FIELD.match(lines[stop])):
                values.append(lines[stop])
                stop += 1
            value = "\n".join(values).strip()
            records.append({"material_id": material.id, "page": page["page"],
                            "scope": value, "quote": "\n".join(lines[index:stop]),
                            "complete": stop < len(lines) or page["page"] == final_page,
                            "evidence_kind": "subcontract_scope"})
    return records


def construction_activity_state(scopes, policy):
    """每个完整范围段均能解释才确认行业，不能以单个正向词覆盖整段含义。"""
    terms = "|".join(re.escape(term) for term in sorted(policy["construction_activity_terms"], key=len, reverse=True))
    activity = re.compile(r"(?:(?:本项目|本标段|负责|承担|实施|进行)\s*)?(?:" + terms + r")(?:作业|劳务作业|劳务分包)?")
    for row in scopes:
        text = re.sub(r"\s+", "", str(row.get("scope") or "")).strip("。；;，,")
        if (row.get("complete") is not True or not text
                or any(term in text for term in policy["ambiguous_boundary_terms"])):
            return False
        parts = re.split(r"以及|及|和|与|[，,、；;]", text)
        if not all(part and activity.fullmatch(part) for part in parts):
            return False
    return bool(scopes)


def extract_safety_permit_records(materials, certificate_text, certificate_titles):
    records = []
    for material in materials:
        if material.parse_state != "done":
            continue
        for page in material.pages_json or []:
            number, text = page.get("page"), page.get("text") or ""
            if not material_page_readable(material, number) or re.search(r"模板|填写示例|样表", text):
                continue
            headings = list(certificate_titles.finditer(text))
            for index, heading in enumerate(headings):
                if "安全生产许可证" not in re.sub(r"\s+", "", heading.group()):
                    continue
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                boundary = _NEXT_DOCUMENT_TITLE.search(text, heading.end(), end)
                if boundary:
                    end = boundary.start()
                fragment = certificate_text(material, text[heading.start():end])
                if not fragment:
                    continue
                # 同页多证也按独立标题分段，旧报告跨页归并不作为本规则事实。
                facts = extract_safety_permit_page(fragment, number, material.file_name)
                if facts:
                    records.append({**facts, "material_id": material.id,
                                    "subject_id": getattr(material, "subject_id", None),
                                    "industry_restriction": bool(_RESTRICTED_PERMIT.search(fragment)),
                                    "industry_identity_ambiguous": (
                                        _field_count(fragment, "许可范围") > 1
                                        or _field_count(fragment, "企业名称|营业名称|名称") > 1),
                                    "quote": fragment, "evidence_kind": "safety_permit_scope"})
    return records


def evaluate_scope(records, *, company, normalize_company, reading_complete, contract_scopes, baseline_date=None):
    if not records:
        return ("not_included" if reading_complete else "undecidable",
                "未取得可核对的安全生产许可证原页，请补充证照" if reading_complete else
                "相关材料读取不完整，安全生产许可证及许可范围需核对")
    if not company:
        return "undecidable", "安全生产许可证已见，但分包单位基准不唯一或未确认，许可范围归属需核对"
    if any(row.get("industry_restriction") or row.get("industry_identity_ambiguous") for row in records):
        return "undecidable", "安全生产许可证存在限制/状态说明或重复身份、范围字段，需核对完整原页，不能只取首个字段确认许可行业"
    names = {normalize_company(row.get("name") or "") for row in records}
    if "" in names or names != {normalize_company(company)}:
        return "undecidable", "安全生产许可证企业名称缺失或与分包单位存在差异，需先核对证照归属及许可范围"
    scopes = {row.get("scope") or "" for row in records}
    if len(scopes) > 1:
        return "undecidable", "安全生产许可证存在多份范围候选或范围字段不一致，需逐份核对，不能拼接证照字段"
    if scopes == {""}:
        return ("not_included" if reading_complete else "undecidable",
                "安全生产许可证已见，许可范围字段未提取，请核对证照原页" if reading_complete else
                "安全生产许可证已见，但相关材料读取不完整，许可范围需确认")
    scope = next(iter(scopes))
    if not contract_scopes:
        return "undecidable", "安全生产许可证许可范围为%s；本主体分包范围未明确，需核对对应关系" % scope
    if not reading_complete:
        return "undecidable", "安全生产许可证许可范围为%s；相关材料读取不完整，许可行业与施工作业对应关系需核对" % scope
    policy = json.loads(Path(__file__).with_name("data").joinpath("safety_permit_scope.json").read_text(encoding="utf-8"))
    try:
        baseline = date.fromisoformat(str(baseline_date))
    except (TypeError, ValueError):
        baseline = None
    if baseline is None or baseline < date.fromisoformat(policy["effective_from"]):
        return "undecidable", "核查基准日未明确或早于当前许可行业依据版本，需按对应期间核对许可行业"
    if (re.sub(r"\s+", "", scope) in policy["accepted_industry_scopes"]
            and construction_activity_state(contract_scopes, policy)):
        return "miss", ("安全生产许可证所载行业为建筑施工，与合同明示施工作业的行业类别对应；"
                        "仅确认许可行业，资质专业、等级及承包规模由资质匹配条目另行核查；"
                        "证照有效性另行核查，不证明证照真实性或实际安全生产条件")
    return "undecidable", ("安全生产许可证许可范围为%s；合同作业类别、许可行业或限制条件尚不能唯一对应，"
                            "需核对证照及实际作业性质；不据其他行业证照认定未取得建筑施工许可，"
                            "资质承包范围及证照有效性另行核查" % scope)


def reading_complete(materials):
    for material in materials:
        if material.role not in {"sub", "labor", "packet"}:
            continue
        if material_reading_gap(material) is not None:
            return False
        pages = material.pages_json or []
        numbers = [page.get("page") for page in pages if isinstance(page, dict)]
        if (len(numbers) != len(pages) or not numbers
                or any(type(number) is not int or number < 1 for number in numbers)):
            return False
        total = getattr(material, "pages", None) or max(numbers)
        if type(total) is not int or len(set(numbers)) != len(numbers) or len(numbers) != total:
            return False
        if min(numbers) != 1 or max(numbers) != total:
            return False
    return True
