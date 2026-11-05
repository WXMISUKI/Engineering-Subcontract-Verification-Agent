import json, logging
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class Line:
    id: str
    page: int          # 1-based
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class ExtractDoc:
    pages: int
    lines: list[Line] = field(default_factory=list)

    def to_json(self) -> str:
        # 本地 OCR 可能产出 numpy 标量，统一转原生类型保证 JSON 可序列化
        def _sanitize(v):
            if hasattr(v, "item"):
                try:
                    return v.item()
                except Exception:
                    return float(v) if not isinstance(v, bool) else v
            return v

        payload = json.loads(json.dumps(asdict(self), default=_sanitize))
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "ExtractDoc":
        d = json.loads(s)
        return ExtractDoc(pages=d["pages"], lines=[_line_from_entry(l) for l in d["lines"]])


def _line_from_entry(l: dict) -> Line:
    """由 doc_json 的行对象构造 Line,并把历史脏数据归一成契约形态。

    契约:返回的 Line.text 恒为 str、words 恒为 Word 列表(无词的行 words 为空,
    由 mapper 按其"匹配失败返回 []"契约跳过)。

    脏数据来源:主文档 OCR 行曾把词表错传成 text 参数(见 ocr_worker._append_ocr_lines),
    落库后 text 是 [{x0,y0,x1,y1,text}] 而 words 为空 —— mapper._norm 的 re.sub 收到 list
    抛 TypeError,整条审查流死掉。归一放在 from_json(所有 doc_json 读取路径的唯一入口),
    存量脏 doc_json 不必数据迁移即自愈,后续消费方(context/mapper/ask/query_lines)拿到的
    都是干净形态。
    """
    words = [Word(**w) for w in (l.get("words") or [])]
    text = l.get("text")
    if isinstance(text, (list, tuple)):
        entries = [e for e in text if isinstance(e, dict)]
        if entries:
            if not words:
                words = [Word(e["x0"], e["y0"], e["x1"], e["y1"], str(e.get("text") or ""))
                         for e in entries
                         if all(k in e for k in ("x0", "y0", "x1", "y1"))]
            text = " ".join(str(e.get("text") or "") for e in entries)
        else:
            text = " ".join(str(e) for e in text)
    elif text is None:
        text = ""
    elif not isinstance(text, str):
        text = str(text)
    return Line(id=l["id"], page=l["page"], text=text, words=words)


def _make_line(lid: str, page: int, words: list[Word]) -> Line:
    text = " ".join(w.text for w in words)
    return Line(id=lid, page=page, text=text, words=words)


import re as _re

# 行号规格:支持 [L100] / (L1-L10) / L100 / L1,L3,L5 / L1-L10;剥括号+空白后按 , 分
_LINE_TOKEN = _re.compile(r"^L(\d+)$")
_LINE_RANGE = _re.compile(r"^L(\d+)-L(\d+)$")


def parse_line_spec(spec: str) -> list[str]:
    """解析行号规格字符串 → 行 ID 列表。兼容带 []() 的写法。非法段静默跳过。"""
    if not spec:
        return []
    cleaned = spec.strip().strip("[]() ").replace("(", "").replace(")", "").replace("[", "").replace("]", "")
    ids: list[str] = []
    for seg in cleaned.split(","):
        seg = seg.strip()
        m = _LINE_TOKEN.match(seg)
        if m:
            ids.append(f"L{m.group(1)}")
            continue
        m = _LINE_RANGE.match(seg)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b:
                ids.extend(f"L{n}" for n in range(a, b + 1))
            continue
        # 非法段跳过
    return ids


def query_lines(doc: "ExtractDoc", spec: str) -> list[dict]:
    """按行号规格查行,保持文档顺序。返回 [{id, page, text, rects}]。
    rects=该行 word 的 PDF 点 bbox(供前端跳页后高亮)。读 live doc_json(含 OCR 追加行)。"""
    want = set(parse_line_spec(spec))
    if not want:
        return []
    return [{"id": ln.id, "page": ln.page, "text": ln.text,
             "rects": [{"x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1} for w in ln.words]}
            for ln in doc.lines if ln.id in want]



def _text_layer_lines(pno: int, raw_words: list, idx: int) -> tuple[list[Line], int]:
    """按 (block_no, line_no, word_no) 排序分组行,返回 (lines, next_idx)。
    raw_words: PyMuPDF get_text('words') 每词 (x0,y0,x1,y1, text, line_no, block_no, word_no)。"""
    raw_words.sort(key=lambda w: (w[5], w[6], w[7]))
    out: list[Line] = []
    cur_key = None
    cur_words: list[Word] = []
    for w in raw_words:
        x0, y0, x1, y1, text, line_no, block_no, word_no = w
        key = (block_no, line_no)
        if cur_key is not None and key != cur_key:
            out.append(_make_line(f"L{idx}", pno + 1, cur_words))
            idx += 1
            cur_words = []
        cur_key = key
        cur_words.append(Word(float(x0), float(y0), float(x1), float(y1), text))
    if cur_words:
        out.append(_make_line(f"L{idx}", pno + 1, cur_words))
        idx += 1
    return out, idx


# ---- 上标恢复（2026-09-23，SC20260923120354808「15² 读成 152」根因修复）----
# get_text('words') 把上标 glyph（字号小、基线抬高的 '2'/'3'/'6'…）当普通词拍平进正文：
# W＝bh²/6 → 'bh2/6'、10⁶ → '106'、600×15³/12 → '600×153/12'，LLM 据此抽取/复算必然错值。
# 修复：对含上标 span 的行改用 get_text('dict') 的 span 级重建文本。MuPDF 的 flags&1
# 上标位是不可靠启发（实测同一页 bh²/mm³ 不打标、10⁶ 打标、还有 size=正文 '= 0.54' 误打标），
# 故用纯几何判据：字号 < 0.95×行主体 且 基线抬高 > 0.15×行主体。
_SUP_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _sup_text(t: str) -> str:
    """上标段文本转义：纯数字转 Unicode 上标字符；其余（如 1/2）用 ^(…) 记法。
    注意不能把 2^1/2 转成 '2½'——那会被读成 2.5，宁笨勿歧义。"""
    t = t.strip()
    if not t:
        return ""
    if t.isdigit():
        return t.translate(_SUP_DIGITS)
    return "^(" + t + ")"


def _superscript_plan(spans: list[dict]) -> list[tuple[str, bool, tuple]] | None:
    """dict 行 spans → [(text, is_sup, bbox)]；行内无上标返回 None。"""
    cand = [sp for sp in spans if (sp.get("text") or "").strip()]
    if not cand:
        return None
    dom = max(sp["size"] for sp in cand)
    normals = [sp for sp in cand if sp["size"] >= dom * 0.95]
    baseline = max(sp["origin"][1] for sp in normals) if normals else None
    if baseline is None:
        return None
    plan: list[tuple[str, bool, tuple]] = []
    has_sup = False
    for sp in cand:
        is_sup = sp["size"] < dom * 0.95 and (baseline - sp["origin"][1]) > dom * 0.15
        has_sup = has_sup or is_sup
        plan.append((sp["text"], is_sup, tuple(sp["bbox"])))
    return plan if has_sup else None


def _rebuild_text_from_plan(plan: list[tuple[str, bool, tuple]]) -> str:
    """span 级重建行文本：上标段紧贴前词（不插空格），普通段按视觉间隙 >1pt 补空格。"""
    parts: list[str] = []
    prev_x1: float | None = None
    for text, is_sup, bbox in plan:
        if is_sup:
            s = _sup_text(text)
            if s:
                if parts:
                    parts[-1] = parts[-1].rstrip() + s
                else:
                    parts.append(s)
            prev_x1 = bbox[2]
            continue
        if prev_x1 is not None and bbox[0] - prev_x1 > 1.0:
            parts.append(" ")
        parts.append(text)
        prev_x1 = bbox[2]
    return "".join(parts).strip()


def _restore_superscript_lines(page, lines: list["Line"]) -> list["Line"]:
    """对含上标 span 的视觉行做 span 级文本重建。只触 text，不动 words 几何
    （证据高亮 rect、行定位完全不受影响）；dict 行与 words 行按纵向重叠配对，
    配不上的行保持拍平原样（安全降级）。"""
    if not lines:
        return lines
    try:
        d = page.get_text("dict")
    except Exception:
        return lines
    plans: list[tuple[list, list[tuple[str, bool, tuple]]]] = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for dln in blk.get("lines", []):
            spans = [sp for sp in dln.get("spans", []) if (sp.get("text") or "").strip()]
            if not spans:
                continue
            plan = _superscript_plan(spans)
            if plan:
                plans.append((dln, plan))
    if not plans:
        return lines
    # dict 行 → words 行配对用 IoU（交并比）而非交集：修改版计算书常见双层内容
    # （可见层 + 隐藏旧修订层），旧层行框被分数线条形 glyph 拉高后与可见层交集并列最大，
    # 纯交集 + 先到先得会把重建投给旧层乱码行；IoU 对行框「胖瘦」敏感，能正确分离两层。
    best_for_line: dict[int, tuple[float, str]] = {}
    for _dln, plan in plans:
        y0 = min(b[1] for _, _, b in plan)
        y1 = max(b[3] for _, _, b in plan)
        best_i, best_iou = None, 0.0
        for i, ln in enumerate(lines):
            if not ln.words:
                continue
            wy0 = min(w.y0 for w in ln.words)
            wy1 = max(w.y1 for w in ln.words)
            inter = min(y1, wy1) - max(y0, wy0)
            if inter <= 0:
                continue
            union = (y1 - y0) + (wy1 - wy0) - inter
            iou = inter / union if union > 0 else 0.0
            if iou > best_iou:
                best_i, best_iou = i, iou
        if best_i is None:
            continue
        rebuilt = _rebuild_text_from_plan(plan)
        if not rebuilt:
            continue
        prev = best_for_line.get(best_i)
        if prev is None or best_iou > prev[0]:
            best_for_line[best_i] = (best_iou, rebuilt)
    for i, (_iou, rebuilt) in best_for_line.items():
        lines[i].text = rebuilt
    # 跨行归并（第二遍）：部分 PDF 生成器把上标 glyph 拆成独立视觉行（MuPDF 行分组
    # 随生成器而异；真实 Word 计算书同行、合成/部分导出器分行），行级重建只能得到
    # 'W=bh' + '²/6=22500mm' + '3' 三段 —— 归并回前一行才还原 'W=bh²/6=22500mm³'。
    # ① 重建行以上标字符开头 → 原样并入前行；② 未重建的孤儿上标行（几何上比前行
    # 矮且抬高、文本是 ≤5 字符纯数字/分数）→ 转义后并入前行。清空的行保留 id/几何，
    # 不破坏下游行号稳定性。
    sup_start_re = _re.compile(r"^[⁰¹²³⁴⁵⁶⁷⁸⁹]|^\^\(")
    orphan_re = _re.compile(r"^\d{1,3}$|^\d{1,3}/\d{1,3}$")
    rebuilt_idx = set(best_for_line.keys())
    last = None
    for idx in range(len(lines)):
        cur = lines[idx]
        t = (cur.text or "").strip()
        if not t:
            continue
        if last is not None and last.words and cur.words:
            pt = (last.text or "").rstrip()
            prev_h = max(w.y1 - w.y0 for w in last.words)
            cur_h = max(w.y1 - w.y0 for w in cur.words)
            raised = max(w.y1 for w in cur.words) < min(w.y1 for w in last.words) + 0.5
            sentence_end = pt[-1:] in "。；;：:）)】》"
            if pt and not sentence_end and raised:
                if idx in rebuilt_idx and sup_start_re.match(t):
                    last.text = pt + t
                    cur.text = ""
                    continue
                if (
                    idx not in rebuilt_idx
                    and len(t) <= 5
                    and orphan_re.match(t)
                    and cur_h < prev_h * 0.85
                ):
                    last.text = pt + _sup_text(t)
                    cur.text = ""
                    continue
        last = cur
    return lines


def extract(path: str) -> ExtractDoc:
    """PDF -> ExtractDoc:每页词级 bbox + 视觉行分组,行 id 全局唯一 L0..L{n-1}。
    仅取文本层 get_text('words');图内文字/扫描页由 ocr_worker 异步补(见 ocr_worker.run_ocr)。

    坐标系: PDF 点空间,原点左上、y 向下。前端 rect * scale 直接用。
    """
    import pymupdf as fitz

    doc = fitz.open(path)
    lines: list[Line] = []
    idx = 0
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        raw = page.get_text("words")
        out, idx = _text_layer_lines(pno, raw, idx)
        out = _restore_superscript_lines(page, out)
        lines.extend(out)
    return ExtractDoc(pages=doc.page_count, lines=lines)


def image_page_count(path: str) -> int:
    """统计含栅格图的页数(供 upload 判定是否需异步 OCR + ocr_status.total)。"""
    import pymupdf as fitz

    doc = fitz.open(path)
    n = 0
    for pno in range(doc.page_count):
        if doc.load_page(pno).get_images():
            n += 1
    return n


if __name__ == "__main__":
    import sys

    d = extract(sys.argv[1] if len(sys.argv) > 1 else "tests/fixture.pdf")
    print("pages", d.pages, "lines", len(d.lines))
    if d.lines:
        print("L0", d.lines[0].text[:60])
