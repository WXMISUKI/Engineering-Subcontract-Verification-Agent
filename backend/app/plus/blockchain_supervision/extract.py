"""合同字段统一抽取层（纯函数、零 LLM、只读 material.pages_json）。

用途（FRS v2：五步审查需要的合同要素；坐标＝《沪城公路〔2025〕118号》附件1第1项（拟分包工程
内容和范围）、第5项（拟签订的分包合同及设备清单）+《沪交行规〔2026〕8号》附件3）：把合同文本层
抽成**跨合同比对**的标准信息——分包合同甲方 vs 施工总承包合同乙方、分包范围 vs 总包范围
（后者在总包合同在场时才有）；并支撑去重键 `(项目名称, 合同段, 合同编号)`、无编号时
`(甲乙方名称, 签订日期, 金额)` 兜底。总包合同是**可选输入**：不在场时只在「范围说明」里声明
哪两类情形未纳入，不因此判缺件。

纪律（硬性）：
- **零 LLM**：全部确定性正则/词表（红线 RD-6）；正则/词表外置 `data/extract_matchers.json`
  （规则=数据，`rules.load_extract_matchers`），本文件不内联规则常量。
- **三态齐全**：每字段返回 `{value, unit, status, missing, quote, page}`；抽不到 →
  `undecidable` 且 `missing` 必写明缺哪类字段，**绝不留空、绝不猜测、绝不静默归未命中**
  （AGENTS 纪律 4）。
- **数值必带 unit**（金额统一万元 `万元`；管理费比例统一 `%`），不得裸值。
- 金额**复用** `a1_check.extract_contract_amount`（不重写）；公司名归一复用
  `a2_check.norm_company`（同模块公开名，遵守「不引用他模块 `_` 私有符号」纪律）。
- **不改现役 `startup_conditions_check/metadata.py`**（红线 RD-2；其主体抽取依赖 LLM + OC 模型）。

字段编号 F1-F9 为**本层内部编号**（提取用，不占用业主编号，也不对外出现）：
F1 合同类型 / F2 金额+税额口径 / F3 工程范围 / F4 甲乙方名称+USCC / F5 签订(生效)日期 /
F6 管理费约定 / F7 材料款承担方 / F8 施工方案编制主体 / F9 声明性条款。

护栏消费坐标（AGENTS 纪律 5）：本层产出用于报告封面事实认定、去重键兜底、跨合同比对；
缺少合同签订日期时保持不可判定，不以核查日期冒充合同事实。
"""
import re

from . import a1_check
from .a2_check import norm_company
from .models import STATUS_HIT, STATUS_UNDECIDABLE
from .rules import load_extract_matchers

_WS_RE = re.compile(r"\s+")
_PROJECT_NAME_RE = re.compile(
    r"(?:工程名称|项目名称|工程全称|工程标段名称)\s*[:：]\s*([^\r\n]{4,160})"
)

# 字段键 → 展示名（供报告/前端事实认定；F1-F9 为本层内部字段编号）
FIELD_LABELS = {
    "contract_type": "合同类型",
    "amount": "合同金额",
    "scope": "工程范围/内容",
    "parties": "甲乙方名称",
    "sign_date": "签订日期",
    "mgmt_fee": "管理费约定",
    "material_fee": "材料款承担方",
    "plan_author": "施工方案编制主体",
    "declarations": "声明性条款",
}


def extract_project_name(material) -> str | None:
    """从合同正文的显式工程名称标签提取施工任务，不使用文件名或模糊猜测。"""
    # `_pages` 为确定性字段抽取服务会去掉全部空白；抬头需要保留工程名称的自然分隔，
    # 因此这里读取同一材料的原始页文本，但仍只接受显式标签。
    for page in getattr(material, "pages_json", None) or []:
        text = page.get("text", "") if isinstance(page, dict) else ""
        match = _PROJECT_NAME_RE.search(text or "")
        if not match:
            continue
        value = _WS_RE.sub(" ", match.group(1)).strip(" ：:;；，,。")
        # 表头常把合同号/编号接在同一行；只保留工程名称本身。
        for marker in ("合同号", "合同编号", "编号", "监理单位", "承包单位"):
            value = value.split(marker, 1)[0].strip(" ：:;；，,。")
        if value:
            return value
    return None


def _norm_text(text: str) -> str:
    """去空白归一化（与 a1_check._norm_pages 同口径）：正则锚定连续中文关键词。"""
    return _WS_RE.sub("", text or "")


def _pages(material) -> list:
    return [(p.get("page"), _norm_text(p.get("text", "")))
            for p in (material.pages_json or [])]


def _flat(material) -> str:
    return "\n".join(_norm_text(p.get("text", "")) for p in (material.pages_json or []))


def _hit(value, unit=None, quote=None, page=None, **extra) -> dict:
    return {"value": value, "unit": unit, "status": STATUS_HIT,
            "missing": [], "quote": quote, "page": page, **extra}


def _undecidable(*missing: str) -> dict:
    """不可判定：必须写明缺哪类字段（missing 非空）。"""
    return {"value": None, "unit": None, "status": STATUS_UNDECIDABLE,
            "missing": [m for m in missing if m], "quote": None, "page": None}


# ==================== F1 合同类型 ====================

def _f_contract_type(material, flat: str, matchers: dict) -> dict:
    """文件名优先、文本关键词兜底；返回中文类型标签（总包/专业分包/劳务合作）。"""
    name = material.file_name or ""
    types = matchers["types"]
    labels = matchers["type_labels"]
    for key, kws in types.items():
        for kw in kws:
            if kw in name:
                return _hit(labels.get(key, key), quote=name)
    for key, kws in types.items():
        for kw in kws:
            if kw in flat:
                return _hit(labels.get(key, key), quote=kw)
    return _undecidable("合同类型（总包/专业分包/劳务合作）关键词")


# ==================== F2 合同金额 + 税额口径 ====================

def _f_amount(material) -> dict:
    """复用 a1_check.extract_contract_amount；口径缺失即不可判定（不默认含税）。"""
    res = a1_check.extract_contract_amount(material)
    if not res.get("found"):
        return _undecidable("合同金额")
    tax = res.get("tax")
    if tax == "unknown":
        return _undecidable("税额口径（含税/不含税）")
    return _hit(res["value_wan"], unit="万元",
                quote=res.get("quote"), page=res.get("page"), tax=tax)


# ==================== F3 工程范围/内容描述 ====================

def _f_scope(pages: list, matchers: dict) -> dict:
    """关键词窗口：命中「工程范围/承包范围/工程内容…」后取一段描述。"""
    for page_no, text in pages:
        for kw in matchers["scope_keywords"]:
            i = text.find(kw)
            if i >= 0:
                seg = text[i:i + 60]
                return _hit(seg, quote=seg, page=page_no)
    return _undecidable("工程范围/内容描述（工程范围/承包范围/工程内容…关键词）")


# ==================== F4 甲乙方名称（含统一社会信用代码） ====================

def _f_parties(flat: str, matchers: dict) -> dict:
    """公司后缀锚定抽主体（防抽到「乙方（盖章）」碎词）；USCC 走 18 位字符集正则。"""
    party_re = re.compile(matchers["party_name_regex"])
    parties = {}
    for m in party_re.finditer(flat):
        role, name = m.group(1), norm_company(m.group(2))
        if name and role not in parties:
            parties[role] = name
    uscc = re.findall(matchers["uscc_regex"], flat)
    if not parties and not uscc:
        return _undecidable("甲乙方名称（含统一社会信用代码）")
    return _hit(parties or None, quote=None, page=None, uscc=uscc)


def extract_subcontractor_name(material) -> str | None:
    """从正文明确标注的分包方角色中提取唯一单位名；文件名与歧义结果不作判断。"""
    matchers = load_extract_matchers()
    party_re = re.compile(matchers["party_name_regex"])
    subcontractor_roles = {"乙方", "分包人", "分包单位"}
    names = {
        norm_company(match.group(2))
        for _page, text in _pages(material)
        for match in party_re.finditer(text)
        if match.group(1) in subcontractor_roles and norm_company(match.group(2))
    }
    return next(iter(names)) if len(names) == 1 else None


def extract_material_subject_name(material) -> str | None:
    """提取可用于主体一致性校验的唯一单位名。

    合同优先使用明确的乙方/分包方角色；营业执照、资质证书等证照没有
    甲乙方角色时，只在文件名明确属于主体证照且正文能唯一定位公司名时
    返回结果。无法唯一定位时返回 ``None``，由上层按不可判定处理，避免
    用文件名或模糊相似度误判主体。
    """
    party_name = extract_subcontractor_name(material)
    if party_name:
        return party_name

    file_name = str(getattr(material, "file_name", "") or "")
    flat = _flat(material)
    if not any(keyword in file_name or keyword in flat for keyword in ("营业执照", "资质证书", "安全生产许可证")):
        return None

    # 营业执照 OCR 偶尔把截断的“有限公司”和相邻“类型：有限责任公司”
    # 拼成“有限公型有限责任公司”。不得把拼接结果当成完整企业名称；
    # 也不猜补缺失字，交给主体待确认流程。
    if re.search(r"(?:有限公|有限公司)(?:类型|型)有限责任公司", flat):
        return None

    company_re = re.compile(
        r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,60}?(?:有限责任公司|有限公司|集团有限公司|工程公司|公司))"
    )
    # 证照 OCR 常把“名称”识别成“称”，因此同时兼容这两种稳定标签。
    labelled_re = re.compile(
        r"(?:企业名称|单位名称|公司名称|名称|称)\s*[:：]?\s*"
        r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,60}?(?:有限责任公司|有限公司|集团有限公司|工程公司|公司))"
    )
    labelled = {norm_company(match.group(1)) for match in labelled_re.finditer(flat)}
    labelled.discard("")
    # 优先使用明确的证照企业字段；弱“名称”字段在 OCR 顺序错乱时可能
    # 混入其他标签，仅允许其以该明确名称结尾，不能忽略真正不同的主体。
    explicit_re = re.compile(
        r"(?:企业名称|单位名称|公司名称)\s*[:：]?\s*"
        r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,60}?(?:有限责任公司|有限公司|集团有限公司|工程公司|公司))"
    )
    explicit = {norm_company(match.group(1)) for match in explicit_re.finditer(flat)}
    explicit.discard("")
    if len(explicit) > 1:
        return None
    if len(explicit) == 1:
        name = next(iter(explicit))
        if all(value.endswith(name) for value in labelled):
            return name
        return None
    if len(labelled) == 1:
        return next(iter(labelled))

    all_names = {norm_company(match.group(1)) for match in company_re.finditer(flat)}
    all_names.discard("")
    return next(iter(all_names)) if len(all_names) == 1 else None


# ==================== F5 签订日期 / 生效日期 ====================

def _iso_from_match(m) -> str:
    """日期捕获组 → ISO YYYY-MM-DD（两组格式：年月日 / YYYY-MM-DD）。"""
    if m.group(1):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
    return "%04d-%02d-%02d" % (y, mo, d)


def _f_sign_date(pages: list, matchers: dict, baseline_date) -> tuple:
    """返回 (字段 dict, 降级记录 or None)。

    只使用合同中明确的日期；核查基准日不代表签订事实。
    """
    date_re = re.compile(matchers["date_regex"])
    for page_no, text in pages:
        for m in date_re.finditer(text):
            window = text[max(0, m.start() - 12):m.end()]
            if any(k in window for k in matchers["date_keywords"]):
                return _hit(_iso_from_match(m), quote=window, page=page_no), None
    return _undecidable("签订日期"), None


# ==================== F6 管理费约定（有/无、比例） ====================

def _f_mgmt_fee(flat: str, matchers: dict) -> dict:
    kw = matchers["mgmt_fee_keyword"]
    if kw not in flat:
        return _undecidable("管理费约定（有/无、比例）")
    for w in matchers["mgmt_fee_absent_words"]:
        if w in flat:
            return _hit("无", quote=w)
    m = re.search(matchers["mgmt_fee_ratio_regex"], flat)
    if m:
        return _hit(float(m.group(1)), unit="%", quote=m.group(0))
    return _undecidable("管理费比例")


# ==================== F7 材料款/周转材料款承担方 ====================

def _f_material_fee(flat: str, matchers: dict) -> dict:
    for kw in matchers["material_fee_keywords"]:
        i = flat.find(kw)
        if i < 0:
            continue
        window = flat[max(0, i - 30):i + 30]
        for p in matchers["party_words"]:
            if p in window:
                return _hit(p, quote=window)
    return _undecidable("材料款/周转材料款承担方")


# ==================== F8 施工方案编制主体 ====================

def _f_plan_author(flat: str, matchers: dict) -> dict:
    for kw in matchers["plan_keywords"]:
        i = flat.find(kw)
        if i < 0:
            continue
        window = flat[max(0, i - 20):i + 40]
        if any(v in window for v in matchers["plan_verb_keywords"]):
            for p in matchers["party_words"]:
                if p in window:
                    return _hit(p, quote=window)
    return _undecidable("施工方案编制主体")


# ==================== F9 声明性条款 ====================

def _f_declarations(flat: str, matchers: dict) -> dict:
    found = [p for p in matchers["declaration_patterns"] if p in flat]
    if found:
        return _hit(found, quote="；".join(found))
    return _undecidable("声明性条款（不得分包/不得转包/指定分包）")


def extract_contract_fields(material, *, baseline_date=None) -> dict:
    """一份合同材料 → 9 类字段的三态抽取结果（纯函数，不写库、不重算判定）。

    `baseline_date` 为核查基准日，保留参数兼容调用方，不作为合同签订日期。

    返回 `{material_id, file_name, fields: {F1..F9 key: {value,unit,status,missing,quote,page}},
    degradations: [...]}`。
    """
    matchers = load_extract_matchers()
    pages = _pages(material)
    flat = _flat(material)

    degradations = []
    sign_date, sign_deg = _f_sign_date(pages, matchers, baseline_date)
    if sign_deg:
        degradations.append(sign_deg)

    fields = {
        "contract_type": _f_contract_type(material, flat, matchers),
        "amount": _f_amount(material),
        "scope": _f_scope(pages, matchers),
        "parties": _f_parties(flat, matchers),
        "sign_date": sign_date,
        "mgmt_fee": _f_mgmt_fee(flat, matchers),
        "material_fee": _f_material_fee(flat, matchers),
        "plan_author": _f_plan_author(flat, matchers),
        "declarations": _f_declarations(flat, matchers),
    }
    return {
        "material_id": getattr(material, "id", None),
        "file_name": getattr(material, "file_name", None),
        "fields": fields,
        "degradations": degradations,
    }
