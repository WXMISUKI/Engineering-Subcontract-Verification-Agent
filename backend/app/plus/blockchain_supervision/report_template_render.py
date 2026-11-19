"""Render the frozen V1.3 block tree without reading current business data."""
from __future__ import annotations

import io
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import fitz
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from .report_template_v13 import _dimension_summary, _item_text


def _footer(payload, digest, exported_at):
    if payload.get('review_mode') == 'contract_retrospective':
        return ''
    return "内容指纹：%s　导出时间：%s。校验码对应系统导出时内容，人工填写后的文件请另行留档。" % (
        digest[:16], exported_at or datetime.now().isoformat(timespec="seconds"))


def _paragraph_runs(element):
    return list(element.findall(qn("w:r")))


def _set_run_text(run, text):
    for child in list(run):
        if child.tag != qn("w:rPr"):
            run.remove(child)
    lines = str(text or "").split("\n")
    for index, line in enumerate(lines):
        if index:
            run.append(OxmlElement("w:br"))
        node = OxmlElement("w:t")
        if line.startswith(" ") or line.endswith(" "):
            node.set(qn("xml:space"), "preserve")
        node.text = line
        run.append(node)


def _set_paragraph_runs(element, segments):
    """Replace text while retaining the paragraph and run properties in place."""
    ppr = element.find(qn("w:pPr"))
    prototypes = _paragraph_runs(element)
    for child in list(element):
        if child is not ppr:
            element.remove(child)
    for index, text in enumerate(segments):
        prototype = prototypes[min(index, len(prototypes) - 1)] if prototypes else OxmlElement("w:r")
        run = deepcopy(prototype)
        _set_run_text(run, text)
        element.append(run)


def _set_cell_text(cell, text):
    tc = cell._tc
    paragraphs = tc.findall(qn("w:p"))
    if not paragraphs:
        tc.append(OxmlElement("w:p"))
        paragraphs = tc.findall(qn("w:p"))
    _set_paragraph_runs(paragraphs[0], [str(text or "")])
    for paragraph in paragraphs[1:]:
        tc.remove(paragraph)


def _clone_table_row(table, source_row):
    table._tbl.append(deepcopy(source_row._tr))


def _fill_table(table, headers, rows):
    """Fill a table while retaining its original grid, borders and cell styles."""
    for cell, value in zip(table.rows[0].cells, headers):
        _set_cell_text(cell, value)
    target_rows = len(rows) + 1
    while len(table.rows) < target_rows:
        _clone_table_row(table, table.rows[1] if len(table.rows) > 1 else table.rows[0])
    while len(table.rows) > target_rows:
        table._tbl.remove(table.rows[-1]._tr)
    for row, values in zip(table.rows[1:], rows):
        for cell, value in zip(row.cells, values):
            _set_cell_text(cell, value)


def _format_issue_rows(document):
    return [(row.get("theme") or "审查问题", row.get("fact") or "")
            for row in document.get("issue_groups") or []
            if row.get("category") in {"缺项", "不符合项"}]


def _format_overview(document):
    info = document["basic_information"]
    keys = ("工程名称", "标段", "建设单位（业主）", "施工总承包单位", "分包单位",
            "分包事项", "拟分包金额", "材料页数", "核查基准日")
    return "；".join("%s：%s" % (key, str(info.get(key) or "材料未载明")) for key in keys) + "。"


def word_bytes(payload, digest, exported_at=None):
    """Dispatch by frozen template identity; V1.4 is block-rendered."""
    document = payload["template_document"]
    if document.get("template_id") in {"v1.4", "contract-retrospective.v1", "contract-retrospective.v2", "contract-retrospective.v3", "contract-retrospective.v4"}:
        return word_bytes_v14(payload, digest, exported_at)
    doc = Document(Path(__file__).with_name("templates") / "review_opinion_v13.docx")
    body = doc.element.body
    paragraphs = [element for element in body if element.tag == qn("w:p")]
    tables = list(doc.tables)
    info = document["basic_information"]
    project = info.get("工程名称") or info.get("标段") or "本项目"
    page_text = "（共%s页）" % document["material_page_count"] if document.get("material_page_count") is not None else ""
    submitted = str(info.get("报送日期") or "待核实")
    if len(submitted) >= 10 and submitted[4] == "-":
        submitted = "%s月%s日" % (submitted[5:7].lstrip("0"), submitted[8:10].lstrip("0"))
    count_text = "%s项" % document["checklist_count"] if document.get("checklist_count") is not None else "待核实"
    intro = ("根据相关法律法规和文件，对你部【%s】报送的%s%s报审材料%s实施合规性智能审查。"
             "共核查事项%s。审查情况如下：" %
             (submitted, project, _item_text(info), page_text, count_text))
    title = document["title"]
    prefix, marker, suffix = title.partition("分包审查意见")
    _set_paragraph_runs(paragraphs[0], [prefix, marker + suffix, ""])
    _set_paragraph_runs(paragraphs[1], ["致：", document.get("recipient") or "施工总承包单位／项目部"])
    _set_paragraph_runs(paragraphs[2], [
        "  ", "根据", "相关法律法规和文件", "，对", "你部【", submitted, "】报送的",
        project + _item_text(info),
        "报审材料%s实施合规性智能审查。共核查事项%s。审查情况如下：" % (page_text, count_text),
    ])
    # The template paragraph already has automatic list numbering for section I.
    _set_paragraph_runs(paragraphs[3], ["报审基本情况"])
    _set_paragraph_runs(paragraphs[4], ["分包单位：", str(info.get("分包单位") or "")])
    _set_paragraph_runs(paragraphs[5], ["分包事项：", str(info.get("分包事项") or "")])
    _set_paragraph_runs(paragraphs[6], ["二、审查依据"])
    _set_paragraph_runs(paragraphs[7], ["《中华人民共和国建筑法》、《公路工程施工分包管理办法》、《公路工程施工分包负面清单》、《上海市公路工程施工分包管理实施细则》等，详见附件。"])
    _set_paragraph_runs(paragraphs[8], ["三、审查情况"])
    _set_paragraph_runs(paragraphs[9], [
        "本次按8个审查维度对报审材料核查：", "其中", _dimension_summary(document["dimensions"]), "。"
    ])
    _fill_table(tables[0], ["序号", "审查维度", "主要依据", "初审意见"],
                [[str(index), row["title"], "；".join(row["basis"]) or (row.get("limitation") if row["verdict"] == "—" else "本次未形成专门核查依据"), row["verdict"]]
                 for index, row in enumerate(document["dimensions"], 1)])
    for row in tables[0].rows[1:]:
        row_properties = row._tr.get_or_add_trPr()
        if row_properties.find(qn("w:cantSplit")) is None:
            row_properties.append(OxmlElement("w:cantSplit"))

    issue_heading = paragraphs[10]
    issue_prototypes = paragraphs[11:16]
    heading_five = paragraphs[16]
    for paragraph in issue_prototypes:
        body.remove(paragraph)
    insert_at = list(body).index(heading_five)
    prototype = issue_prototypes[0]
    for index, (label, fact) in enumerate(_format_issue_rows(document)):
        paragraph = deepcopy(prototype)
        body.insert(insert_at + index, paragraph)
        _set_paragraph_runs(paragraph, [label + "。", " " + fact])
    _set_paragraph_runs(issue_heading, ["四、审查发现问题"])
    issue_heading.get_or_add_pPr().get_or_add_keepNext()
    _set_paragraph_runs(heading_five, ["五、总体审查意见"])
    status_names = {status: "、".join(row["title"] for row in document["dimensions"] if row["verdict"] == status)
                    for status in ("符合", "不符合", "缺项")}
    for paragraph, status, label in zip((paragraphs[17], paragraphs[18], paragraphs[19]),
                                        ("符合", "不符合", "缺项"),
                                        ("符合项：", "不符合项：", "缺项：")):
        if status_names[status]:
            _set_paragraph_runs(paragraph, [label + status_names[status]])
        else:
            body.remove(paragraph)
    # Omit the legacy status row entirely, including its reserved line height.
    body.remove(paragraphs[20])
    _set_paragraph_runs(paragraphs[21], ["审查结论："])
    _set_paragraph_runs(paragraphs[22], ["六、补充说明"])
    _set_paragraph_runs(paragraphs[23], ["本报告由智能体基于报审材料及预设规则生成，仅起辅助审查作用，最终以实际审查人结论为准。"])
    _set_paragraph_runs(paragraphs[24], ["建议进一步对申报材料的原件核实、验证。"])
    _set_paragraph_runs(paragraphs[27], ["附件："])
    _set_paragraph_runs(paragraphs[28], ["审查概况与审查依据"])
    _set_paragraph_runs(paragraphs[29], ["问题清单与补正要求"])
    _set_paragraph_runs(paragraphs[30], ["审查人（签字）： ________\n复核人（签字）： ________\n签发人（签字）： ________"])
    _set_paragraph_runs(paragraphs[31], ["工程分包审查智能体（系统自动生成）\n" + str(document.get("issued_date") or "")])
    signature_properties = paragraphs[30].find(qn("w:pPr"))
    if signature_properties is None:
        signature_properties = OxmlElement("w:pPr")
        paragraphs[30].insert(0, signature_properties)
    signature_properties.append(OxmlElement("w:keepLines"))
    _set_paragraph_runs(paragraphs[34], ["附件一、审查概况与审查依据"])
    _set_paragraph_runs(paragraphs[35], ["一、审查概况"])
    _set_paragraph_runs(paragraphs[36], [_format_overview(document)])
    _set_paragraph_runs(paragraphs[37], ["审查边界说明："])
    limit_prototypes = paragraphs[38:43]
    basis_heading = paragraphs[43]
    basis_body = paragraphs[44]
    for paragraph in limit_prototypes:
        body.remove(paragraph)
    limit_insert = list(body).index(basis_heading)
    for index, limit in enumerate(document.get("limitations") or []):
        paragraph = deepcopy(limit_prototypes[0])
        body.insert(limit_insert + index, paragraph)
        # The cloned template paragraph already carries Word automatic numbering.
        _set_paragraph_runs(paragraph, [limit])
    _set_paragraph_runs(basis_heading, ["二、审查依据"])
    _set_paragraph_runs(basis_body, ["\n".join("%s. %s。" % (index, value) for index, value in enumerate(document.get("basis") or [], 1))])
    _set_paragraph_runs(paragraphs[47], ["附件二、问题清单与补正要求"])
    grouped = [row for row in document.get("issue_groups") or [] if row["category"] != "符合项"]
    _fill_table(tables[1], ["序号", "问题类别", "问题事实", "补正要求", "责任单位"],
                [[str(index), row["theme"], row["fact"],
                  row["recommendation"], row["responsible_party"]]
                 for index, row in enumerate(grouped, 1)])
    _set_paragraph_runs(paragraphs[48], [
        "注：正文列示证据明确的问题；本清单另列待核实及人工复核事项，均由同一核查记录分组生成。补正材料经复核后逐项销号。"
    ])
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def _set_run_font(run, *, size=None, bold=False):
    run.font.name = "宋体"
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "宋体")
    if size is not None:
        run.font.size = Pt(size)
    run.bold = bold


def _set_style_font(style):
    style.font.name = "宋体"
    style.font.size = Pt(11)
    style.paragraph_format.line_spacing = 1.5
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "宋体")


def _set_light_table_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement("w:" + edge)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "BFBFBF")
        borders.append(element)
    tbl_pr.append(borders)


def _shade_cell(cell, color="F2F2F2"):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(shd)


def _add_text(doc, text, *, size=11, bold=False, align=None, space_before=0,
              space_after=4, indent=False, line_spacing=1.5):
    paragraph = doc.add_paragraph()
    if align == "center":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if indent:
        paragraph.paragraph_format.left_indent = Cm(0.8)
    paragraph.paragraph_format.space_before = Pt(space_before)
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.line_spacing = line_spacing
    run = paragraph.add_run(str(text))
    _set_run_font(run, size=size, bold=bold)
    return paragraph


def _add_table(doc, headers, rows, *, widths=None, font_size=10.5):
    table = doc.add_table(rows=1, cols=len(headers))
    document = doc.sections[0]
    # 仅合同倒查模板指定列宽；准入旧模板仍沿用自动布局。
    table.autofit = widths is None
    if widths and len(widths) == len(headers):
        table.autofit = False
        available = document.page_width - document.left_margin - document.right_margin
        for column, ratio in zip(table.columns, widths):
            column.width = int(available * ratio)
        for row in table.rows:
            for cell, ratio in zip(row.cells, widths):
                cell.width = int(available * ratio)
    _set_light_table_borders(table)
    for index, value in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = ""
        _shade_cell(cell)
        run = cell.paragraphs[0].add_run(str(value))
        _set_run_font(run, size=font_size, bold=True)
    repeated = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(repeated)
    for row in rows:
        cells = table.add_row().cells
        if widths:
            for cell, ratio in zip(cells, widths):
                cell.width = int(available * ratio)
        for index, value in enumerate(row):
            cells[index].text = ""
            run = cells[index].paragraphs[0].add_run(str(value))
            _set_run_font(run, size=font_size)
    return table


def word_bytes_v14(payload, digest, exported_at=None):
    """Render the frozen V1.4 block tree to DOCX without a source template."""
    document = payload["template_document"]
    doc = Document()
    section = doc.sections[0]
    landscape = document.get("page_layout") == "landscape"
    section.page_width = Cm(29.7 if landscape else 21.0)
    section.page_height = Cm(21.0 if landscape else 29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(3.2)
    section.right_margin = Cm(3.2)
    retrospective = document.get("template_id") in {"contract-retrospective.v1", "contract-retrospective.v2", "contract-retrospective.v3", "contract-retrospective.v4"}
    if retrospective:
        section.left_margin = section.right_margin = Cm(1.25)
        section.top_margin = section.bottom_margin = Cm(1.5)
    _set_style_font(doc.styles["Normal"])
    _add_text(doc, document["title"], size=16, bold=True, align="center", space_after=8)
    for block in document.get("blocks") or []:
        block_type = block.get("type")
        if block_type == "page_break":
            doc.add_page_break()
        elif block_type == "heading":
            _add_text(doc, block.get("text") or "", size=12, bold=True,
                      space_before=6, space_after=6)
        elif block_type == "table":
            _add_table(doc, block.get("headers") or [], block.get("rows") or [],
                       widths=block.get("widths") if retrospective else None,
                       font_size=9 if retrospective else 10.5)
            _add_text(doc, "", size=4, space_after=2)
        elif block_type == "conclusion":
            for index, line in enumerate(str(block.get("text") or "").splitlines()):
                _add_text(doc, line, size=11, bold=index == 0, space_after=3)
        else:
            _add_text(doc, block.get("text") or "", bold=bool(block.get("bold")),
                      align=block.get("align"), space_after=4,
                      indent=bool(block.get("indent")))
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def word_bytes_worksheet_v14(payload, digest, exported_at=None):
    """Render the 8-sheet working paper separately from the formal opinion."""
    document = payload["template_document"]
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)
    _set_style_font(doc.styles["Normal"])
    _add_text(doc, "八张检查表工作底稿", size=16, bold=True, align="center", space_after=8)
    _add_text(doc, "本底稿为系统核查记录，供审查人核对和留档，不构成正式审查意见；"
                   "正式意见以正文第 7 章结论为准。", size=9, space_after=8)
    for index, sheet in enumerate(document.get("attachments") or []):
        if index:
            doc.add_page_break()
        _add_text(doc, sheet.get("title") or "检查表", size=13, bold=True, space_after=4)
        if sheet.get("note"):
            _add_text(doc, sheet["note"], size=9, space_after=4)
        if sheet.get("auto_headers") and sheet.get("auto_rows"):
            _add_table(doc, sheet["auto_headers"], sheet["auto_rows"])
            _add_text(doc, "", size=4, space_after=2)
        if sheet.get("manual_headers") and sheet.get("manual_rows"):
            _add_text(doc, "需人工核查事项", size=11, bold=True, space_after=3)
            _add_table(doc, sheet["manual_headers"], sheet["manual_rows"])
            _add_text(doc, "", size=4, space_after=2)
        if sheet.get("rows") and sheet.get("kind") == "key_value":
            _add_table(doc, ["项目", "内容"], [list(row) for row in sheet["rows"]])
    if footer := _footer(payload, digest, exported_at):
        _add_text(doc, footer, size=8, space_after=0)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def pdf_bytes(payload, digest, exported_at=None):
    from . import export
    document = payload["template_document"]
    doc = fitz.open()
    landscape = document.get("page_layout") == "landscape"
    margin, width, height = (36.0, 842.0, 595.0) if landscape else (48.0, 595.0, 842.0)
    page, y = doc.new_page(width=width, height=height), margin
    size, line_height, padding = 9.0, 13.5, 4.0

    def new_page():
        nonlocal page, y
        page, y = doc.new_page(width=width, height=height), margin

    def paragraph(text, font_size=10.5, heading=False):
        nonlocal y
        if heading and y + 100 > height - margin:
            new_page()
        for raw in str(text).splitlines() or [""]:
            for line in export._wrap(raw, font_size, width - margin * 2):
                if y + font_size * 1.5 > height - margin:
                    new_page()
                export._pdf_insert_text(page, (margin, y + font_size), line, fontsize=font_size)
                y += font_size * 1.5
        y += 6

    def table(block):
        nonlocal y
        edges = [margin]
        for ratio in block["widths"]:
            edges.append(edges[-1] + ratio * (width - 2 * margin))
        def wrap_row(values):
            return [[line for raw in str(value).splitlines() or [""]
                     for line in export._wrap(raw, size, right - left - 2 * padding)]
                    for value, left, right in zip(values, edges, edges[1:])]
        headers = wrap_row(block["headers"])
        header_height = max(map(len, headers)) * line_height + 2 * padding
        def cells(columns, header=False):
            nonlocal y
            row_height = max(map(len, columns)) * line_height + 2 * padding
            for left, right, lines in zip(edges, edges[1:], columns):
                page.draw_rect(fitz.Rect(left, y, right, y + row_height), color=(.4, .4, .4),
                               width=.4, fill=(.94, .94, .94) if header else None)
                for index, line in enumerate(lines):
                    export._pdf_insert_text(page, (left + padding, y + padding + size + index * line_height),
                                            line, fontsize=size)
            y += row_height
        if y + header_height + 2 * line_height > height - margin:
            new_page()
        cells(headers, True)
        for row in block["rows"]:
            columns = wrap_row(row)
            total = max(map(len, columns))
            row_height = total * line_height + 2 * padding
            if (payload.get('review_mode') != 'contract_retrospective'
                    and row_height <= height - 2 * margin - header_height and y + row_height > height - margin):
                new_page()
                cells(headers, True)
            offset = 0
            while offset < total:
                available = int((height - margin - y - 2 * padding) // line_height)
                if available < 1:
                    new_page()
                    cells(headers, True)
                    continue
                end = min(total, offset + available)
                cells([column[offset:end] for column in columns])
                offset = end
        y += 8

    paragraph(document["title"], 16, True)
    if payload.get("draft_notice") and document.get("template_id") != "v1.4":
        paragraph(payload["draft_notice"], 10)
    for block in document["blocks"]:
        if block["type"] == "page_break":
            new_page()
        elif block["type"] == "table":
            table(block)
        else:
            paragraph(block["text"], 12 if block["type"] == "heading" else 10.5, block["type"] == "heading")
    if document.get("template_id") != "v1.4":
        if footer := _footer(payload, digest, exported_at):
            paragraph(footer, 8)
    output = doc.tobytes()
    doc.close()
    return output


def summary_lines(payload):
    document = payload["template_document"]
    yield "title", document["title"]
    if payload.get("draft_notice") and document.get("template_id") != "v1.4":
        yield "text", payload["draft_notice"]
    for block in document["blocks"]:
        if block["type"] == "table":
            yield "text", " | ".join(block["headers"])
            for row in block["rows"]:
                yield "text", " | ".join(str(value) for value in row)
        elif block["type"] != "page_break":
            yield "heading" if block["type"] == "heading" else "text", block["text"]
