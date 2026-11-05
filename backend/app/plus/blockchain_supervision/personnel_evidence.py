"""名单事实的共同抽取层；不做合规判定或关系推断。"""
import re
from html import unescape
from .attachment_source import has_embedded_submission, is_attachment_source


# 人员姓名（社保记录侧 / 名单侧显式标注）
_NAME_IN_SOCIAL_RE = re.compile(r"姓名[:：]\s*([\u4e00-\u9fa5]{2,4})")
# 名单行锚点：手机号（「名单及联系方式」里的联系方式）
_ROSTER_PHONE_RE = re.compile(r"1[3-9]\d{9}")
# 名单行内中文串（按最大连续段切，再按长度+停用词过滤——避免长串被切碎误当人名）
_ROSTER_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fa5]+")
# 名单抽取停用词：岗位/字段/单位通用词，命中即弃（防把「项目经理」当人名）
_ROSTER_STOPWORDS = frozenset({
    "姓名", "名称", "岗位", "职务", "职位", "职责", "序号", "编号", "备注", "备注栏",
    "联系电话", "联系方式", "手机", "手机号", "手机号码", "电话", "电话号码", "邮箱",
    "项目经理", "项目总工", "总工程师", "技术负责人", "安全员", "施工员", "质量员",
    "材料员", "资料员", "预算员", "测量员", "试验员", "机械员", "标准员", "劳务员",
    "项目负责人", "项目副经理", "管理人员", "主要人员", "其他人员", "人员名单",
    "专业分包", "劳务分包", "分包单位", "施工单位", "建设单位", "监理单位",
    "有限公司", "有限责任公司", "集团", "公司", "项目部", "工程", "施工",
    "身份证", "证书", "职称", "学历", "部门", "工作",
})


def extract_social_names(text: str) -> list:
    """社保记录文本 → 人员姓名列表（去重保序）。"""
    out, seen = [], set()
    for m in _NAME_IN_SOCIAL_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out

def extract_roster_names(text: str) -> list:
    """「主要管理人员名单及联系方式」材料文本 → 姓名列表（去重保序，宁缺勿错）。

    名单材料为表格/列表，结构不定，故只取两条**确定性通道**（R-1 决议）：
    ① 显式标注「姓名：张三」；
    ② 含手机号（联系方式）的名单行内、长度 2–4 且非岗位/字段停用词的**最大连续中文段**
       （先按最大段切分再过滤，故「上海城建隧道建设工程有限公司」这类长串不会被切碎
       误当人名，而「王建国」这类 2–4 字段得以保留）。
    两条通道均落空 → 返回 []，由调用方判**不可判定**——绝不臆断名单为空。
    """
    out, seen = [], set()

    def _add(name: str) -> None:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    for m in _NAME_IN_SOCIAL_RE.finditer(text):
        _add(m.group(1))
    for line in text.splitlines():
        if not _ROSTER_PHONE_RE.search(line):
            continue
        for run in _ROSTER_CJK_RUN_RE.findall(line):
            if 2 <= len(run) <= 4 and run not in _ROSTER_STOPWORDS:
                _add(run)
    return out


# 仅RC-A09补读合同中的实填人员信息；不得把整份合同放进所有报审证明候选。
_ROSTER_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:乙方)?"
    r"(?:项目主要管理人员|主要项目管理人员|主要管理人员)"
    r"(?:名单(?:及联系方式)?|表)?[:：]?$"
)
_CONTACT_FIELD_RE = re.compile(r"联系方式|联络方式|联系电话|电话|手机(?:号码)?")
_ACTUAL_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ROSTER_INSTRUCTION_RE = re.compile(
    r"示例|样例|填表说明|填写示范|(?:应当|必须|应|须|需).{0,8}(?:提交|提供|配备|填写|报送)"
)
_ROSTER_TABLE_BOUNDARY = "__contract_roster_table_boundary__"
_ROSTER_CONTACT_BOUNDARY_RE = re.compile(r"委托代理人|法定代表人|(?:甲方|乙方)?联系人")


_DEDICATED_ROSTER_RE = re.compile(r"管理人员名单|名单及联系方式")
_SOCIAL_TITLE_RE = re.compile(r"(?:社会保险|社保).{0,6}(?:缴费|参保|证明|权益记录)|参保人员|参保证明")
_LABOR_TITLE_RE = re.compile(r"^劳动合同(?:书)?[：:]?$")
_RELATION_OBLIGATION_RE = re.compile(r"示例|样例|填表说明|填写示范|(?:应当|必须|应|须|需).{0,14}(?:缴纳|签订|提交|提供|购买)")
_INDIRECT_RELATION_RE = re.compile(r"承诺|声明|台账|清单|模板|示例|样例|应提交|应提供")
_SOCIAL_ATTACHMENT_TITLE_RE = re.compile(r"(?:社会保险|社保|养老保险)(?:个人|单位)?(?:缴费|参保|权益)?(?:证明|记录|明细)(?:表)?[：:]?$")
_FIELD_BOUNDARY = r"(?=\s*(?:姓名|劳动者姓名|参保单位|缴费单位|用人单位|单位名称|缴费所属期|缴费期间|缴费月份|缴费年月|费款所属期|参保月份|所属期|合同期限)\s*[:：]|\n|[;；]|$)"
_EMPLOYER_RE = re.compile(r"(?:参保单位|缴费单位|用人单位|单位名称)\s*[:：]\s*([^\n;；]+?)" + _FIELD_BOUNDARY)
_PERIOD_RE = re.compile(r"(?:缴费所属期|缴费期间|缴费月份|缴费年月|费款所属期|参保月份|所属期|合同期限)\s*[:：]\s*([^\n;；]+?)" + _FIELD_BOUNDARY)
_POST_FIELD_RE = re.compile(r"(?:岗位|职务)\s*[:：]\s*([^\n;；]+)")
_POST_PLACEHOLDERS = frozenset({"岗位", "职务", "待填", "待定", "未填", "空白", "/", "-", "—"})


def _page_lines(raw: str, *, preserve_roster_table=False, preserve_social_table=False) -> list[str]:
    """保留段、表和行的边界，所有人员抽取共用此归一方式。"""
    # OCR的pretty HTML会在td之间换行；只有同一tr内部的排版换行可合并。
    raw = re.sub(r"<tr\b[^>]*>.*?</tr\s*>",
                 lambda match: re.sub(r"[\r\n]+", " ", match.group()), raw, flags=re.I | re.S)
    plain = re.sub(r"</(?:tr|p|div|h[1-6])\s*>|<br\s*/?>", "\n", raw, flags=re.I)
    plain = re.sub(r"</?table\b[^>]*>", "\n" + _ROSTER_TABLE_BOUNDARY + "\n", plain, flags=re.I)
    plain = unescape(re.sub(r"<[^>]*>", " ", plain))
    plain = re.sub(r"姓\s+名", "姓名", plain)
    # 仅在显式姓名栏且值以单元格/字段边界结束时合并字间空格，绝不全页拼中文。
    plain = re.sub(r"(姓名\s*[:：][ \t]*)([\u4e00-\u9fa5](?:[ \t]+[\u4e00-\u9fa5]){1,3})"
                   r"(?=[ \t]*(?:[|;；]|$))",
                   lambda match: match.group(1) + re.sub(r"[ \t]+", "", match.group(2)),
                   plain, flags=re.M)
    lines = [re.sub(r"[ \t]+", " ", line).strip(" #\t") for line in plain.splitlines()]
    has_markdown_roster = preserve_roster_table and any(
        "|" in line and "姓名" in line and ("岗位" in line or "职务" in line)
        and _CONTACT_FIELD_RE.search(line) for line in lines)
    has_social_table = preserve_social_table and any(
        "|" in line and "姓名" in line and ("缴费" in line or "参保" in line) for line in lines)
    return [line if (has_markdown_roster or has_social_table) and "|" in line else line.strip(" |#\t") for line in lines]


def _table_person(header: str, row: str):
    """仅从表头对应姓名/岗位/联系方式列取值，不扫描职责、备注中的中文串。"""
    if "|" in header:
        # Markdown 单元格本身可能包含空格，也可能为空；必须按列而非空白切分。
        def table_cells(line):
            line = line.strip()
            if line.startswith("|"):
                line = line[1:]
            if line.endswith("|"):
                line = line[:-1]
            return [cell.strip() for cell in line.split("|")]
        columns = table_cells(header)
        cells = table_cells(row)
        if len(cells) != len(columns):
            return None
    else:
        columns = re.split(r"\s+", header.strip())
        cells = re.split(r"\s+", row.strip())
    name_index = next((i for i, column in enumerate(columns) if column == "姓名"), None)
    post_index = next((i for i, column in enumerate(columns) if column in {"岗位", "职务"}), None)
    phone_index = next((i for i, column in enumerate(columns) if _CONTACT_FIELD_RE.fullmatch(column)), None)
    if columns and columns[0] == "序号" and (not cells or not re.fullmatch(r"\d+", cells[0])):
        return None  # 续行首列为空，不能向左错列借用姓名或岗位。
    if any(index is None or index >= len(cells) for index in (name_index, post_index, phone_index)):
        return None
    name, post, phone = cells[name_index], cells[post_index], cells[phone_index]
    if (not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name) or name in _ROSTER_STOPWORDS
            or not post or post in _POST_PLACEHOLDERS or not _ACTUAL_PHONE_RE.search(phone)):
        return None
    return [name], post


def _layout_management_roster(page, material):
    """仅在名单标题、三列及同行字段均可唯一确认时提取布局线索。"""
    text = page.get("text") or ""
    meta = page.get("ocr_meta") or {}
    if not re.search(r"管理人员.{0,8}名单", text) or not isinstance(meta, dict) or meta.get("quality") != "ocr_ok":
        return []
    blocks = []
    for entry in meta.get("layout") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        box = entry.get("bbox")
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(value) not in (int, float) for value in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            continue
        blocks.append((entry["text"].strip(), box))
    name_head = [box for value, box in blocks if value == "姓名"]
    post_head = [box for value, box in blocks if value in {"职务", "岗位"}]
    phone_head = [box for value, box in blocks if _CONTACT_FIELD_RE.fullmatch(value)]
    if not (len(name_head) == len(post_head) == len(phone_head) == 1):
        return []
    name_head, post_head, phone_head = name_head[0], post_head[0], phone_head[0]
    if not (name_head[2] < post_head[0] and post_head[2] < phone_head[0]):
        return []
    bottom = max(name_head[3], post_head[3], phone_head[3])
    def xcenter(box):
        return (box[0] + box[2]) / 2
    def overlap(a, b):
        height = min(a[3], b[3]) - max(a[1], b[1])
        return height > 0 and height >= 0.6 * min(a[3] - a[1], b[3] - b[1])
    # 一些名单在职务和电话之间还有“持证以及证号”列；该列正文即使与
    # 姓名同行，也不能作为职务候选。只采用与职务表头同一行的明确列头。
    between_headers = [box for value, box in blocks
                       if re.search(r"持证|证书|证号|证件", value)
                       and overlap(box, post_head) and post_head[2] < box[0] < phone_head[0]]
    post_end = min((box[0] for box in between_headers), default=phone_head[0])
    names = [(value, box) for value, box in blocks if box[1] > bottom
             and name_head[0] - (name_head[2] - name_head[0]) <= xcenter(box) < post_head[0]
             and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", value) and value not in _ROSTER_STOPWORDS]
    posts = [(value, box) for value, box in blocks if box[1] > bottom
             and name_head[2] < xcenter(box) < post_end
             and re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", value) and value not in _POST_PLACEHOLDERS]
    phones = [(value, box) for value, box in blocks if box[1] > bottom
              and xcenter(box) > post_head[2] and _ACTUAL_PHONE_RE.fullmatch(value)]
    facts = []
    for name, box in names:
        same_posts = [(value, other) for value, other in posts if overlap(box, other)]
        same_phones = [(value, other) for value, other in phones if overlap(box, other)]
        if len(same_posts) != 1 or len(same_phones) != 1:
            continue
        if sum(overlap(other, same_phones[0][1]) for _, other in names) != 1:
            continue
        if name not in text or same_posts[0][0] not in text or same_phones[0][0] not in text:
            continue
        facts.append({"name": name, "post": same_posts[0][0], "material_id": material.id,
                      "page": page.get("page"), "quote": name, "evidence_kind": "management_roster"})
    return facts


def _roster_records(materials: list, *, contract_only=False):
    """产出全部实填记录及原A3首条投影；不在这里判劳动/参保关系。"""
    for material in materials:
        contract = material.role in ("sub", "labor")
        dedicated = (material.role not in ("general", "sub", "labor")
                     and bool(_DEDICATED_ROSTER_RE.search(material.file_name or "")))
        if material.parse_state != "done" or (contract_only and not contract):
            continue
        if material.role == "general":
            continue
        embedded = contract and has_embedded_submission(material)
        for page in material.pages_json or []:
            layout_facts = _layout_management_roster(page, material) if not contract or embedded else []
            if layout_facts:
                yield from layout_facts
                continue
            lines = _page_lines(page.get("text") or "", preserve_roster_table=True)
            anchors = []
            for index, line in enumerate(lines):
                heading = bool(_ROSTER_HEADING_RE.fullmatch(re.sub(r"\s+", "", line)))
                header = ("姓名" in line and ("岗位" in line or "职务" in line)
                          and bool(_CONTACT_FIELD_RE.search(line)))
                if heading or header:
                    anchors.append((index, header))
            # 专用名单无标题时仍允许显式姓名；不把合同联系人当名单。
            if dedicated and not anchors:
                anchors = [(-1, False)]
            emitted = set()
            for index, header in anchors:
                line = lines[index] if index >= 0 else ""
                block = [line]
                following_lines = lines[index + 1:index + 25] if contract_only else lines[index + 1:]
                for following in following_lines:
                    if (following == _ROSTER_TABLE_BOUNDARY
                            or _ROSTER_CONTACT_BOUNDARY_RE.search(following)
                            or re.match(r"^\d+(?:\.\d+)*[.、]\s*\S", following)):
                        break  # 下一合同条款的联系人不属于本人员段。
                    block.append(following)
                text = "\n".join(block)
                if _ROSTER_INSTRUCTION_RE.search(text):
                    continue
                records = []
                if header:
                    # 名称、主要管理岗位、实填电话必须属于同一表格行，不能跨行拼凑。
                    for row in block[1:]:
                        person = _table_person(line, row)
                        if person:
                            records.append((row, *person))
                else:
                    # 合同段落允许姓名/电话分行，但联系人边界或下一姓名栏会结束本记录。
                    for offset, row in enumerate(block[1:], 1):
                        if not (re.match(r"^姓名\s*[:：]", row) and extract_roster_names(row)):
                            continue
                        record = [row]
                        for following in block[offset + 1:offset + 4]:
                            if re.match(r"^姓名\s*[:：]", following):
                                break
                            record.append(following)
                        if (dedicated or any(_CONTACT_FIELD_RE.search(part) and _ACTUAL_PHONE_RE.search(part)
                                             for part in record)):
                            record_text = "\n".join(record)
                            post = _POST_FIELD_RE.search(record_text)
                            names = [name for name in extract_social_names(row) if name not in _ROSTER_STOPWORDS]
                            records.append((record_text, names, post.group(1).strip() if post else None))
                for record, names, post in records:
                    proof = re.sub(r"\s+", " ", line + "\n" + record).strip()[:240]
                    # 后续姓名的岗位/电话不能借给本记录；首姓名来自显式栏或同表行。
                    for name in names:
                        key = (name, proof)
                        if key in emitted:
                            continue
                        emitted.add(key)
                        yield {"name": name, "post": post,
                               "material_id": material.id, "page": page.get("page"),
                               "quote": proof, "evidence_kind": "contract_roster" if contract else "management_roster"}


def extract_management_roster(materials: list) -> list[dict]:
    """全部实填主要人员事实，保留每一条原文所在材料和物理页。"""
    return list(_roster_records(materials))


def special_trade_layout_rows(page, *, with_parts=False, allow_partial=False):
    """只提取表头、列位置及行高均能唯一对应的单块姓名和工种。"""
    meta = page.get("ocr_meta") or {}
    if not isinstance(meta, dict) or meta.get("quality") != "ocr_ok":
        return []
    blocks = []
    for entry in meta.get("layout") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        box = entry.get("bbox")
        if (not isinstance(box, (tuple, list)) or len(box) != 4
                or any(type(v) not in (int, float) for v in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            continue
        blocks.append((entry["text"].strip(), box))
    names = [b for b in blocks if b[0] == "姓名"]
    roles = [b for b in blocks if b[0] == "工种"]
    if len(names) != 1 or len(roles) != 1:
        return []
    name_header, role_header = names[0][1], roles[0][1]
    if name_header[2] >= role_header[0]:
        return []
    header_bottom = max(name_header[3], role_header[3])
    def in_column(box, header):
        center = (box[0] + box[2]) / 2
        padding = (header[2] - header[0]) * 0.6
        return header[0] - padding <= center <= header[2] + padding and box[1] > header_bottom
    def overlaps(a, b):
        overlap = min(a[3], b[3]) - max(a[1], b[1])
        return overlap > 0 and overlap >= 0.6 * min(a[3] - a[1], b[3] - b[1])
    name_blocks = [(t, b) for t, b in blocks if in_column(b, name_header)
                   and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t) and t not in _ROSTER_STOPWORDS]
    role_blocks = [(t, b) for t, b in blocks if in_column(b, role_header)
                   and re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", t)]
    result = []
    ordered_names = sorted(name_blocks, key=lambda entry: (entry[1][1] + entry[1][3]) / 2)
    centers = [(box[1] + box[3]) / 2 for _, box in ordered_names]
    for index, (name, box) in enumerate(ordered_names):
        lower = (centers[index - 1] + centers[index]) / 2 if index else header_bottom
        upper = ((centers[index] + centers[index + 1]) / 2 if index + 1 < len(centers)
                 else centers[index] + (centers[index] - centers[index - 1]) / 2 if index else box[3])
        row_roles = [(role, role_box) for role, role_box in role_blocks
                     if lower < (role_box[1] + role_box[3]) / 2 < upper]
        if len(row_roles) == 2:
            # 工种单元格可能折成两行：两个片段均须落在唯一姓名的行内，
            # 且每段不能同时与另一姓名相交；只返回完整岗位后缀。
            row_roles.sort(key=lambda entry: (entry[1][1], entry[1][0]))
            first_box, second_box = row_roles[0][1], row_roles[1][1]
            vertical_overlap = min(first_box[3], second_box[3]) - max(first_box[1], second_box[1])
            if vertical_overlap > 0.3 * min(first_box[3] - first_box[1], second_box[3] - second_box[1]):
                continue
            def substantial_overlap(a, b):
                overlap = min(a[3], b[3]) - max(a[1], b[1])
                return overlap > 0 and overlap >= 0.35 * min(a[3] - a[1], b[3] - b[1])
            if not all(substantial_overlap(box, role_box) and
                       sum(substantial_overlap(other, role_box) for _, other in name_blocks) == 1
                       for _, role_box in row_roles):
                continue
            parts = [role for role, _ in row_roles]
            role = "".join(parts)
            if (not re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", role)
                    or not role.endswith(("工", "司机", "指挥", "操作员", "驾驶员"))
                    or name not in (page.get("text") or "")):
                continue
            result.append((name, role, parts) if with_parts else (name, role))
            continue
        if len(row_roles) != 1:
            continue
        matches = [(role, role_box) for role, role_box in row_roles if overlaps(box, role_box)]
        if len(matches) != 1:
            continue
        role, role_box = matches[0]
        # 单块只取有完整岗位后缀的候选；例如 OCR 把“起重机指挥”截为
        # “起重机指”时不能把残片当作工种。其他未知表达保守留待核实。
        if (not role.endswith(("工", "司机", "指挥", "操作员", "驾驶员"))
                and not (allow_partial and len(role) >= 3)):
            continue
        if sum(overlaps(other, role_box) for _, other in name_blocks) != 1:
            continue
        if name not in (page.get("text") or ""):
            continue
        result.append((name, role, [role]) if with_parts else (name, role))
    return result


def extract_special_trade_roster(materials: list) -> list[dict]:
    """提取有明确标题及表头的特种作业名单行；证件是否属于本人须另行核验。"""
    def cells_of(line):
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [cell.strip() for cell in line.split("|")]

    facts = []
    for material in materials:
        if material.parse_state != "done" or not is_attachment_source(material):
            continue
        for page in material.pages_json or []:
            text = page.get("text") or ""
            meta = page.get("ocr_meta") or {}
            clues = meta.get("rotation_roster_clues") if isinstance(meta, dict) else None
            if isinstance(clues, list):
                for clue in clues:
                    if not isinstance(clue, dict):
                        continue
                    name, post = clue.get("name"), clue.get("post")
                    if (isinstance(name, str) and isinstance(post, str) and name in text and post in text
                            and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name)
                            and re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", post)):
                        facts.append({"name": name, "post": post, "material_id": material.id,
                                      "page": page.get("page"), "quote": name,
                                      "evidence_kind": "special_trade_roster"})
                continue
            if not re.search(r"(?:特殊工种|特种作业).{0,16}(?:名单|名册)", text):
                continue
            layout_rows = special_trade_layout_rows(page)
            for name, post in layout_rows:
                facts.append({"name": name, "post": post, "material_id": material.id,
                              "page": page.get("page"), "quote": name,
                              "evidence_kind": "special_trade_roster"})
            if layout_rows:
                continue
            lines = text.splitlines()
            for index, header in enumerate(lines):
                if "|" not in header:
                    continue
                columns = cells_of(header)
                if "序号" not in columns or "姓名" not in columns:
                    continue
                post_index = next((i for i, name in enumerate(columns) if name in {"职务", "岗位", "工种"}), None)
                if post_index is None or not any("证" in name for name in columns):
                    continue
                for row in lines[index + 1:]:
                    if "|" not in row:
                        break
                    cells = cells_of(row)
                    if len(cells) != len(columns):
                        continue
                    serial, name, post = cells[columns.index("序号")], cells[columns.index("姓名")], cells[post_index]
                    if (not re.fullmatch(r"\d+", serial) or not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name)
                            or name in _ROSTER_STOPWORDS or not post or post in _POST_PLACEHOLDERS):
                        continue
                    facts.append({"name": name, "post": post, "material_id": material.id,
                                  "page": page.get("page"), "quote": row.strip(),
                                  "evidence_kind": "special_trade_roster"})
                break
    return facts


def contract_roster_evidence(materials: list):
    """RC-A09兼容投影：仍只取合同中第一条实填记录的原有摘录。"""
    first = next(_roster_records(materials, contract_only=True), None)
    return (first["material_id"], first["quote"], first["page"]) if first else None


def _relation_context(material, lines):
    if any(re.search(r"模板|示例|样例|填表说明|填写示范", line) for line in lines):
        return None
    contract = material.role in ("general", "sub", "labor")
    actual = [line for line in lines if not _RELATION_OBLIGATION_RE.search(line)]
    title = next((line for line in actual if _SOCIAL_TITLE_RE.search(line)
                  and not _INDIRECT_RELATION_RE.search(line)
                  and (not contract or _SOCIAL_ATTACHMENT_TITLE_RE.fullmatch(line))), None)
    labor = next((line for line in actual if _LABOR_TITLE_RE.fullmatch(line)), None)
    filename = material.file_name or ""
    fallback = (not contract and _SOCIAL_TITLE_RE.search(filename)
                and not _INDIRECT_RELATION_RE.search(filename)
                and not any(_RELATION_OBLIGATION_RE.search(line) for line in lines))
    if not title and not labor and not fallback:
        return None
    return actual, title or labor, "labor_contract" if labor and not title else "social_record"


def _relation_page_contexts(material, page):
    lines = _page_lines(page.get("text") or "", preserve_social_table=True)
    starts = [i for i, line in enumerate(lines)
              if _SOCIAL_ATTACHMENT_TITLE_RE.fullmatch(line) or _LABOR_TITLE_RE.fullmatch(line)]
    # 同一页内有多份独立证明时，各段自行提供姓名/单位/期间，不借第一页首字段。
    if len(starts) > 1:
        segments = [lines[start:end] for start, end in zip(starts, starts[1:] + [len(lines)])]
    else:
        segments = [lines]
    for segment in segments:
        context = _relation_context(material, segment)
        if context is not None:
            yield context


def _social_table_rows(lines):
    """仅提取社保证明中按表头对齐且序号、姓名同一行的记录。"""
    def cells(line):
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [cell.strip() for cell in line.split("|")]
    for index, header in enumerate(lines):
        if "|" not in header:
            continue
        columns = cells(header)
        if "序号" not in columns or "姓名" not in columns or not any("缴费" in c or "参保" in c for c in columns):
            continue
        period_index = next((i for i, c in enumerate(columns) if "缴费起止" in c or "缴费期间" in c), None)
        for row in lines[index + 1:]:
            if "|" not in row:
                break
            values = cells(row)
            # 某些社保证明 OCR 把后部日期拆成两格、序号漏识别；前四列
            # 仍须按明确表头验证姓名、完整身份证号和个人编号。此时只能保留
            # 人员线索，不从错列中猜缴费期间或参保单位。
            if (columns[:4] == ["序号", "姓名", "身份证号", "个人编号"]
                    and len(values) == len(columns) + 1 and not values[0]
                    and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", values[1])
                    and values[1] not in _ROSTER_STOPWORDS
                    and re.fullmatch(r"\d{17}[0-9Xx]", values[2])
                    and re.fullmatch(r"\d{6,20}", values[3])):
                yield values[1], None, row
                continue
            if len(values) != len(columns):
                continue
            serial, name = values[columns.index("序号")], values[columns.index("姓名")]
            if (not re.fullmatch(r"\d+", serial) or not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name)
                    or name in _ROSTER_STOPWORDS):
                continue
            period = values[period_index] if period_index is not None and values[period_index] else None
            yield name, period, row
        break


def extract_person_relation_evidence(materials: list) -> list[dict]:
    """抽取实际社保/劳动合同中的姓名、明示单位和期间，不推断同人或关系成立。

    合同附件须在该物理页出现独立证明/劳动合同标题，不能由合同缴费义务触发。
    单位和期间仅取同页同证明段明示字段；不跨段、跨页、跨材料补齐。
    """
    facts = []
    for material in materials:
        if material.parse_state != "done":
            continue
        for page in material.pages_json or []:
            for actual_lines, title, kind in _relation_page_contexts(material, page):
                text = "\n".join(actual_lines)
                employers = list(dict.fromkeys(m.group(1).strip() for m in _EMPLOYER_RE.finditer(text)))
                periods = list(dict.fromkeys(m.group(1).strip() for m in _PERIOD_RE.finditer(text)))
                for line in actual_lines:
                    for name in extract_social_names(line):
                        # 引文来自同段原文；候选全留，冲突时不擅自选择第一条。
                        fields = [m.group() for regex in (_EMPLOYER_RE, _PERIOD_RE) for m in regex.finditer(text)]
                        quote = "\n".join(filter(None, [title, *fields, line]))
                        facts.append({"name": name, "post": None, "material_id": material.id,
                                      "page": page.get("page"), "quote": re.sub(r"\s+", " ", quote)[:240],
                                      "evidence_kind": kind,
                                      "employer": employers[0] if len(employers) == 1 else None,
                                      "period": periods[0] if len(periods) == 1 else None,
                                      "employer_candidates": employers, "period_candidates": periods})
                if kind == "social_record":
                    for name, period, row in _social_table_rows(actual_lines):
                        facts.append({"name": name, "post": None, "material_id": material.id,
                                      "page": page.get("page"), "quote": row.strip(), "evidence_kind": kind,
                                      "employer": None, "period": period, "employer_candidates": [],
                                      "period_candidates": [period] if period else []})
    return facts


def social_evidence_materials(materials: list) -> list:
    """社保证明候选（包括未读专用件），供规则准确说明读取/抽名缺口。"""
    out = []
    for material in materials:
        if any(context[2] == "social_record"
               for page in material.pages_json or [{"text": ""}]
               for context in _relation_page_contexts(material, page)):
            out.append(material)
    return out


def relation_evidence_kinds(material) -> set[str]:
    """返回材料内可识别的关系证明类型，不以另一种证明补齐缺失字段。"""
    return {context[2] for page in material.pages_json or [{"text": ""}]
            for context in _relation_page_contexts(material, page)}


def relation_segments_have_unique_employers(material, required_kinds: set[str]) -> bool:
    """逐个证明段核对姓名与单位字段，不能借另一段填补缺口。"""
    seen = set()
    for page in material.pages_json or []:
        for lines, _title, kind in _relation_page_contexts(material, page):
            if kind not in required_kinds:
                continue
            seen.add(kind)
            names = {name for line in lines for name in extract_social_names(line)}
            employers = {match.group(1).strip() for match in _EMPLOYER_RE.finditer("\n".join(lines))}
            if not names or len(employers) != 1:
                return False
    return seen == required_kinds
