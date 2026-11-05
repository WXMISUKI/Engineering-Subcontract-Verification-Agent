"""工程分包审查前置资料的只读清单生成器。

这份清单只描述来源文件，不复制、移动或删除原件。分类采用显式文件名规则，
未知文件保持为 ``candidate``，避免把未核验的材料误当成规范入库。

★ 派生产物（OCR 中间结果、网页另存的 ``_files`` 资源目录）不是「来源文件」，
   扫描时整体跳过。此前这些目录尚不存在，清单只有 21 条；2026-10-19 生成 OCR
   结果后，若不跳过，重跑会膨胀到 412 条，把 page-0001.json 之类中间文件当成
   资料登记，清单既不可读也不可复现。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "subcontract-source-manifest.v1"
_MANIFEST_PATH = Path(__file__).parent / "data" / "source_manifest.json"

_ROAD_SELECTED: dict[str, dict[str, Any]] = {
    "沪城公路（2025）118号：关于印发《上海城投.pdf": {
        "category": "owner_internal_norm",
        "scope": "road_mvp",
        "document_no": "沪城公路〔2025〕118号",
        "effective_from": "2025-12-10",
        "selection": "selected",
        "source_authority": "上海城投公路投资（集团）有限公司",
        "verification": "ocr_pending",
        "vector_ingest": "planned_after_proofread",
        "notes": "118号文正文扫描件；先登记来源，人工校对前不得作为已校对条款引用。",
    },
    "2025年分包单位报审以及附件要求（6.20）(2).pdf": {
        "category": "owner_internal_requirement",
        "scope": "road_mvp",
        "effective_from": "2025-06-20",
        "selection": "selected",
        "source_authority": "第一现场指挥部",
        "verification": "text_extract_verified",
        "vector_ingest": "planned",
        "notes": "业主报审材料要求；(2) 先作为副本标记，需与正式原件比对后再合并。",
    },
    "上海市交通委员会关于印发《上海市公路工程施工分包管理实施细则》的通知(2).pdf": {
        "category": "shanghai_local_norm",
        "scope": "road_mvp",
        "document_no": "沪交行规〔2026〕8号",
        "effective_from": "2026-08-08",
        "effective_to": "2031-08-07",
        "selection": "selected",
        "source_authority": "上海市交通委员会",
        "verification": "source_metadata_pending",
        "vector_ingest": "planned_after_proofread",
        "notes": "上海公路分包实施细则扫描件；需补发文文号、有效期和官方来源核验。",
    },
    "公路工程施工分包负面清单.docx": {
        "category": "national_transport_norm",
        "scope": "road_mvp",
        "document_no": "交办公路〔2024〕6号（待核）",
        "selection": "candidate",
        "source_authority": "交通运输部（待核）",
        "verification": "source_metadata_pending",
        "vector_ingest": "hold_until_source_verified",
        "notes": "内容标注为2024年版；正式发布机关、版本和可入库原件待核验。",
    },
    "20261014工程分包审查智能体建设工作安排部署_上海城投交通集团（韩鸿猷）.pdf": {
        "category": "owner_project_baseline",
        "scope": "project_baseline",
        "selection": "reference_only",
        "source_authority": "上海城投交通集团",
        "verification": "text_extract_verified",
        "vector_ingest": "excluded",
        "notes": "项目设计与范围基线，不作为法规条款进入规范知识库。",
    },
    # ★ 国家底线本体（设计 80 A-1）：原件一直在，此前误标 candidate 且 review_flow 记「未取得」。
    #   两份副本内容一致（8 页 / 12 页，正文 3,872 字仅 1 处全角/半角冒号差异），以 8 页版为准。
    "《公路工程施工分包管理办法》交公路规〔2024〕2号.pdf": {
        "category": "national_transport_norm",
        "scope": "road_mvp",
        "document_no": "交公路规〔2024〕2号",
        "title": "公路工程施工分包管理办法",
        "issuer": "交通运输部",
        "effective_from": "2024-02-18",
        "effective_to": None,
        "status": "in_force",
        "authority_level": "floor",
        "physical_pages": 8,
        "text_chars": 4058,
        "source_kind": "text_layer",
        "selection": "selected",
        "source_authority": "交通运输部",
        "verification": "text_extract_verified",
        "vector_ingest": "planned_after_proofread",
        "notes": "【首选副本·已提取全文本】8 页、文本层直提 4,058 字，完整到第二十七条（含附则），无 OCR。"
                 "已由 scripts/ingest_basis_clauses.py 摄为 MOT-2024-2 共 33 条，落 basis_clauses.json。"
                 "与 缺失文件/ 下 12 页副本做过逐字比对（归一化空白 + 剔除页眉页脚后 difflib 全量比对）："
                 "正文 3,872 字仅 1 处差异（第十七条引导冒号「：」vs「:」），无实质条款差异。"
                 "text_extract_verified 指文本层直提无 OCR 误差，尚未逐字人工校对，故摄取端 human_verified=false。",
    },
    # ★ 次选副本。注意：_classify 只按 basename 匹配，故此处 key 不含「缺失文件/」前缀。
    "交通-公路工程施工分包管理办法(交公路规〔2024〕2号).pdf": {
        "category": "national_transport_norm",
        "scope": "road_mvp",
        "document_no": "交公路规〔2024〕2号",
        "title": "公路工程施工分包管理办法",
        "issuer": "交通运输部",
        "effective_from": "2024-02-18",
        "effective_to": None,
        "status": "in_force",
        "authority_level": "floor",
        "physical_pages": 12,
        "text_chars": 4359,
        "source_kind": "text_layer",
        "selection": "selected",
        "source_authority": "交通运输部",
        "verification": "text_extract_verified",
        "vector_ingest": "planned_after_proofread",
        "notes": "【次选副本·与首选副本同文】12 页、4,359 字，多出的是官方发文版式页眉页脚"
                 "（交通运输部行政规范性文件／交通运输部发布／-N-）。剔除页眉页脚并归一化空白后"
                 "与 前置背景/《公路工程施工分包管理办法》交公路规〔2024〕2号.pdf 做 difflib 全量比对："
                 "正文 3,872 字、差异块 1 个（第十七条引导冒号「：」vs「:」）。"
                 "原先登记 candidate／source_metadata_pending 是因未与另一副本比对，2026-10-23 已比对完成。"
                 "条款坐标与首选副本共用同一 doc_id（MOT-2024-2），不重复摄取。",
    },
    # ★ 事业部 18 号（设计 80 A-2）：本机一直有原件，但未登记。
    #   ★ 权威层级＝supplement（只补空缺，不得覆盖 floor / override）。
    #   ★ 关键红线：第七条「管理人员每周到岗履职时间不得少于施工时间的 80%」
    #     是**出勤时间占比**，与「低于成本价分包单价线 80%」、「30% 工程量」量纲完全不同，
    #     **禁止任何形式的换算或互相印证**（沪/渝/鄂/粤的 80% 是单价线，上海无该阈值）。
    "沪城公路第一事业部〔2025〕18号：关于印发《上海城投公路投资（集团）有限公司第一事业部建设工程分包管理实施细则》的通知(1).pdf": {
        "category": "owner_internal_norm",
        "scope": "road_mvp",
        "document_no": "沪城公路第一事业部〔2025〕18号",
        "title": "上海城投公路投资（集团）有限公司第一事业部建设工程分包管理实施细则",
        "issuer": "上海城投公路投资（集团）有限公司第一事业部",
        "effective_to": None,
        "status": "in_force",
        "authority_level": "supplement",
        "physical_pages": 8,
        "text_chars": 2595,
        "source_kind": "text_layer",
        "selection": "reference_only",
        "source_authority": "上海城投公路投资（集团）有限公司第一事业部",
        "verification": "text_extract_verified",
        "vector_ingest": "excluded",
        "notes": "【只登记不引用】8 页、2,595 字，文本层可直提（首页封面无文本层，正文第 2 页起）。"
                 "层级＝supplement：只补 floor/override 空缺，不得覆盖。"
                 "本期不进规范知识库、不做 basis 坐标（a1_check 0 命中），仅作事业部口径参照与答疑依据。"
                 "★ 与 floor 不是同一条线：第八条（一）明确转引《公路工程施工分包管理办法》"
                 "（交公路规[2024]2号）第十五/十六/十七条，即事业部口径**以部办法为准**，细则不新增违法分包类型。"
                 "★ 唯一新增实体规则是第七条 2「分包单位管理人员每周到岗履职时间不得少于施工时间的 80%」——"
                 "这是**出勤时间占比**，与「低于成本价分包单价线 80%」「30% 工程量」量纲不同，"
                 "**禁止换算、禁止互相印证**；且该 80% 属履职考核，不构成本期任何审查结论的判定阈值。"
                 "★ 第五条资料要求（专业 4 项／劳务 4 项）与业主 6.20 清单口径需人工比对，未自动下结论。",
    },
}

_CONFIRMED_FUTURE_NORMS: dict[str, dict[str, Any]] = {
    "建筑业企业资质标准.pdf": {
        "category": "national_qualification_norm",
        "scope": "future_qualification",
        "selection": "selected",
        "source_authority": "住房城乡建设主管部门（用户确认采用基准）",
        "verification": "source_metadata_pending",
        "vector_ingest": "planned_after_source_verified",
        "notes": "用户已确认采用；适用于资质映射，正式版本和来源元数据仍需登记。",
    },
    "水利建设市场经营主体信用信息管理办法_官方版.pdf": {
        "category": "water_domain_norm",
        "scope": "future_water",
        "document_no": "水建设〔2024〕201号",
        "effective_from": "2024-11-01",
        "selection": "selected",
        "source_authority": "水利部",
        "verification": "source_metadata_verified",
        "vector_ingest": "deferred_domain_review",
        "notes": "用户指定官方版，水建设〔2024〕201号；保留在资料池，按水利项目适用性单独接入。",
    },
    "水利-水利建设市场监督管理办法.pdf": {
        "category": "water_domain_norm",
        "scope": "future_water",
        "selection": "excluded",
        "source_authority": "待确认",
        "verification": "source_metadata_pending",
        "vector_ingest": "excluded",
        "notes": "旧/错名水利副本，不进入当前依据集；原件保留以便追溯。",
    },
}

_CANDIDATE_FUTURE_NORMS: dict[str, dict[str, Any]] = {
    # ★ 2 号文已上移至 _ROAD_SELECTED（floor 层本体，2026-10-23 设计 80 A-1 改判），此处不再登记 candidate。
    "交通-水运建设市场监督管理办法(交通运输部令第74号).pdf": {
        "category": "waterway_norm",
        "scope": "future_waterway",
        "document_no": "交通运输部令第74号",
        "selection": "candidate",
        "source_authority": "交通运输部",
        "verification": "source_metadata_pending",
        "vector_ingest": "deferred_domain_review",
        "notes": "水运领域候选依据，不进入当前公路主流程。",
    },
    "住建-建筑工程施工发包与承包违法行为认定.pdf": {
        "category": "national_housing_norm",
        "scope": "future_housing",
        "document_no": "建市规〔2019〕1号",
        "selection": "candidate",
        "source_authority": "住房城乡建设部",
        "verification": "source_metadata_pending",
        "vector_ingest": "deferred_domain_review",
        "notes": "房建领域候选依据，不进入当前公路主流程。",
    },
    "住建-房屋建筑和市政基础设施工程施工分包管理办法（建设部令第124号）.pdf": {
        "category": "national_housing_norm",
        "scope": "future_housing",
        "document_no": "建设部令第124号",
        "selection": "candidate",
        "source_authority": "住房城乡建设部",
        "verification": "validity_pending",
        "vector_ingest": "deferred_domain_review",
        "notes": "历史房建/市政分包依据，修订及现行有效性待核验。",
    },
    "建筑业企业资质管理规定.pdf": {
        "category": "national_qualification_norm",
        "scope": "future_qualification",
        "selection": "candidate",
        "source_authority": "住房城乡建设主管部门",
        "verification": "source_metadata_pending",
        "vector_ingest": "deferred_domain_review",
        "notes": "资质管理候选依据，版本和有效性待登记。",
    },
    "水利-水利建设工程施工分包管理规定(水建管〔2005〕304号).docx": {
        "category": "water_domain_norm",
        "scope": "future_water",
        "document_no": "水建管〔2005〕304号",
        "selection": "candidate",
        "source_authority": "水利部",
        "verification": "validity_pending",
        "vector_ingest": "deferred_domain_review",
        "notes": "水利分包候选依据，版本较旧；先核现行有效性。",
    },
    "水利工程施工转包违法分包等违法行为.pdf": {
        "category": "water_domain_norm",
        "scope": "future_water",
        "selection": "candidate",
        "source_authority": "待确认",
        "verification": "ocr_required",
        "vector_ingest": "deferred_domain_review",
        "notes": "扫描件无稳定文本层，须先OCR并人工核验来源。",
    },
}

_PROJECT_SAMPLES = {
    "一工区特殊路基处理预应力管桩、高压旋喷桩.pdf",
    "合同扫描件-吊装.pdf",
    "合同扫描件-预制梁.pdf",
    "预制桥梁下部结构、上部结构梁板及跨线桥叠.pdf",
}

_INVENTORY_ONLY = {"需要补充完善的核查依赖资料.png"}

# 派生产物目录：不是来源资料，扫描时整目录跳过（相对 source_root 的路径前缀）。
_DERIVED_PREFIXES = (
    "OCR转换结果",  # OCR 中间产物：逐页记录/原始图片/可读文本
)

# 派生产物后缀：网页「另存为」产生的资源目录，文件名形如 xxx.html 与 xxx_files/。
_DERIVED_SUFFIXES = (
    "_files",  # 浏览器另存网页时的静态资源目录
)


def _preflight_action(row: dict[str, Any]) -> str:
    """把 manifest 的治理状态翻译成下一步动作；不把任何状态自动升级为可入库。"""
    vector_ingest = row.get("vector_ingest")
    if vector_ingest == "excluded":
        return "excluded"
    if vector_ingest in {"submitted", "processing", "completed"}:
        return "already_ingested"
    if row.get("selection") != "selected":
        return "hold_selection"
    if vector_ingest == "planned_after_proofread":
        return "proofread_required"
    if vector_ingest == "planned_after_source_verified":
        return "source_verification_required"
    if vector_ingest == "planned":
        return "source_confirmation_required"
    if vector_ingest == "hold_until_source_verified":
        return "source_verification_required"
    if vector_ingest == "deferred_domain_review":
        return "deferred_domain_review"
    return "governance_required"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _classify(relative_path: Path) -> dict[str, Any]:
    name = relative_path.name
    if name in _ROAD_SELECTED:
        return dict(_ROAD_SELECTED[name])
    if name in _CONFIRMED_FUTURE_NORMS:
        return dict(_CONFIRMED_FUTURE_NORMS[name])
    if name in _CANDIDATE_FUTURE_NORMS:
        return dict(_CANDIDATE_FUTURE_NORMS[name])
    if name in _PROJECT_SAMPLES:
        return {
            "category": "project_sample",
            "scope": "project_sample",
            "selection": "sample_only",
            "source_authority": "项目申报/施工样本（待确认）",
            "verification": "ocr_required",
            "vector_ingest": "excluded",
            "notes": "只用于实体、工程范围和规则回放测试，不进入规范知识库。",
        }
    if name in _INVENTORY_ONLY:
        return {
            "category": "inventory_screenshot",
            "scope": "inventory_only",
            "selection": "reference_only",
            "source_authority": "项目资料盘点",
            "verification": "not_applicable",
            "vector_ingest": "excluded",
            "notes": "资料盘点截图，仅保留来源凭据，不做OCR或向量入库。",
        }
    if relative_path.parts and relative_path.parts[0] == "缺失文件":
        return {
            "category": "future_domain_norm",
            "scope": "future_domain",
            "selection": "candidate",
            "source_authority": "待按正式来源核验",
            "verification": "source_metadata_pending",
            "vector_ingest": "deferred_domain_review",
            "notes": "原目录名‘缺失文件’仅保留来源路径，不代表当前待补件；按项目类型和正式版本核验后再入库。",
        }
    return {
        "category": "unclassified",
        "scope": "unclassified",
        "selection": "candidate",
        "source_authority": "待确认",
        "verification": "source_metadata_pending",
        "vector_ingest": "hold_until_classified",
        "notes": "未命中显式分类规则，不得自动进入规范知识库。",
    }


def _is_derived(relative: Path) -> bool:
    """判断是否为派生产物（OCR 中间结果、网页另存资源目录），不是来源资料。"""

    parts = relative.parts
    if parts and parts[0].startswith(_DERIVED_PREFIXES):
        return True
    for part in parts[:-1]:
        if part.endswith(_DERIVED_SUFFIXES):
            return True
    return False


def build_manifest(source_dir: str | Path, *, generated_at: str | None = None) -> dict[str, Any]:
    """扫描来源目录并返回清单；此函数只读文件系统。"""

    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError("资料目录不存在：%s" % root)
    files = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda p: p.as_posix()):
        relative = path.relative_to(root)
        if _is_derived(relative):
            continue
        row = {
            "relative_path": relative.as_posix(),
            "file_name": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        row.update(_classify(relative))
        files.append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or date.today().isoformat(),
        "source_root": root.as_posix(),
        "policy": {
            "destructive_actions": "disabled",
            "raw_files": "preserve",
            "unknown_files": "hold",
            "normative_vector_ingest": "requires_selection_and_source_verification",
        },
        "files": files,
    }


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """返回结构问题；空列表表示清单可作为资料治理快照保存。"""

    problems: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version 不匹配")
    if manifest.get("policy", {}).get("destructive_actions") != "disabled":
        problems.append("destructive_actions 必须为 disabled")
    seen: set[str] = set()
    for index, row in enumerate(manifest.get("files") or []):
        path = row.get("relative_path")
        if not path or path in seen:
            problems.append("files[%d] relative_path 缺失或重复" % index)
        seen.add(path)
        digest = row.get("sha256") or ""
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            problems.append("files[%d] sha256 无效" % index)
        for field in ("category", "selection", "verification", "vector_ingest"):
            if not row.get(field):
                problems.append("files[%d] 缺少 %s" % (index, field))
    return problems


def verify_manifest_sources(manifest: dict[str, Any]) -> dict[str, Any]:
    """只读核对清单结构与来源文件完整性，返回可归档的预检报告。

    预检不会复制、移动、删除或上传文件，也不会把任何治理状态改成“可入库”。
    ``source_ok`` 同时要求路径在 ``source_root`` 内、文件存在、大小和 SHA-256
    与清单一致；治理动作由 ``action`` 明确表达，供人工确认后再进入灌库。
    """
    problems = validate_manifest(manifest)
    root_text = str(manifest.get("source_root") or "").strip()
    root = Path(root_text).expanduser().resolve() if root_text else None
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(manifest.get("files") or []):
        row = raw if isinstance(raw, dict) else {}
        relative_text = str(row.get("relative_path") or "")
        candidate = (root / relative_text).resolve() if root and relative_text else root
        inside_root = bool(root and candidate and (candidate == root or root in candidate.parents))
        exists = bool(inside_root and candidate and candidate.is_file())
        size_ok = exists and candidate.stat().st_size == row.get("size_bytes")
        sha_ok = exists and _sha256(candidate) == str(row.get("sha256") or "").lower()
        source_ok = bool(inside_root and exists and size_ok and sha_ok)
        if not inside_root:
            integrity = "path_outside_source_root"
        elif not exists:
            integrity = "missing"
        elif not size_ok:
            integrity = "size_mismatch"
        elif not sha_ok:
            integrity = "sha256_mismatch"
        else:
            integrity = "ok"
        rows.append({
            "index": index,
            "relative_path": relative_text,
            "file_name": row.get("file_name", ""),
            "selection": row.get("selection", ""),
            "vector_ingest": row.get("vector_ingest", ""),
            "action": _preflight_action(row),
            "source_ok": source_ok,
            "integrity": integrity,
        })

    integrity_failures = [row for row in rows if not row["source_ok"]]
    return {
        "status": "pass" if not problems and not integrity_failures else "fail",
        "source_root": str(root) if root else "",
        "manifest_problems": problems,
        "integrity_failures": [row["relative_path"] for row in integrity_failures],
        "files": rows,
        "summary": {
            "total": len(rows),
            "source_ok": sum(row["source_ok"] for row in rows),
            "source_failed": len(integrity_failures),
            "ingested": sum(row["action"] == "already_ingested" for row in rows),
            "upload_ready": 0,
        },
    }


def write_manifest(manifest: dict[str, Any], output: str | Path) -> None:
    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("资料清单校验失败：%s" % "；".join(problems))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_registered_manifest() -> dict[str, Any]:
    """读取仓库登记的来源清单；只读，不校验外部原件、不改变准入状态。"""
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

