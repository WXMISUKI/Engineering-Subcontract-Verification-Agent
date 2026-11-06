"""工程分包审查智能体表结构（bs_ 前缀，SQLite/MySQL 双兼容）。

现有持久化设计（目标范围以 FRS v2.2 为准，新增能力另行迁移）：
- BsTask：任务 + 启动选项快照（尺度开局锁定，任务内不可改）
- BsMaterial：材料 + 四类归属（自动归类只是初始值，可人工更正；总包合同可选，0 或 1 份）
- BsTaskEvent：事件流（SSE 取数源，取号走加锁读，见 service.add_event）
"""
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.sql import func, text

from ...database import Base


class BsTask(Base):
    __tablename__ = "bs_task"
    __table_args__ = (Index("ix_bs_task_conversation", "conversation_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversation_mappings.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    # setup（两段式第一段：上传与选项）/ running / review / done
    state = Column(String(40), nullable=False, default="setup")
    scope_a1 = Column(Boolean, nullable=False, default=False)
    scope_a3 = Column(Boolean, nullable=False, default=False)
    # 尺度开局锁定（07 §4.3 标准档唯一实装）；任务内改尺度会让历史结论不可复现
    scale = Column(String(20), nullable=False, default="standard")
    # 两段式确认弹窗的五项快照（任务对象/标段/范围/尺度/材料构成/解析状态）
    options_snapshot_json = Column(JSON, nullable=True, default=dict)
    # A-03（B1）标段锚点：三列一律 nullable、不回填（历史任务无此概念、无数据源）。
    # 报告封面/合同字段抽取层的锚点信息；未填写即 None（呈现层按「未填写」占位）。
    project_name = Column(String(200), nullable=True)      # 标段项目名称
    contract_section = Column(String(120), nullable=True)  # 合同段
    baseline_date = Column(Date, nullable=True)            # 核查基准日（规则版本取值锚点）
    # BS-P4-05：任务辖域与规则 profile。历史任务保持 NULL，不回填或猜造归属。
    jurisdiction = Column(String(40), nullable=True)       # 当前支持 road
    profile_code = Column(String(80), nullable=True)       # road_default / road_group
    # BS-P2-04（117-B §3.1）：启动/重跑时冻结的规则数据版本；历史任务保持 NULL。
    # NULL=历史任务（本列上线前收口），读取侧按「未记录」呈现，不补造。
    ruleset_version = Column(String(40), nullable=True)
    created_by = Column(Integer, nullable=False)
    created_by_type = Column(String(20), nullable=False, default="web")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BsSubject(Base):
    """任务内分包主体（材料归集锚点）。上传阶段只记录归属，不做规则完整性判断。"""
    __tablename__ = "bs_subject"
    __table_args__ = (
        Index("ix_bs_subject_task", "task_id"),
        Index("ix_bs_subject_task_code", "task_id", "stable_code", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    stable_code = Column(String(40), nullable=False)
    name = Column(String(200), nullable=True)
    subject_type = Column(String(20), nullable=True)
    type_confirmed = Column(Boolean, nullable=False, default=False)
    type_locked = Column(Boolean, nullable=False, default=False)
    # T1-v2 契约（附录 §2.1）：AI 类型识别与冲突字段。
    # ai_subject_type=NULL=未能识别（不等于缺件，只影响启动确认与可判定性）；
    # type_conflict 仅在 AI 与人工类型同时存在且不一致时为 True，人工确认/覆盖后
    # 以人工为准置 False、reason 清空，完整冲突历史由 BsTaskEvent 事件流承载。
    ai_subject_type = Column(String(20), nullable=True)
    type_conflict = Column(Boolean, nullable=False, default=False)
    type_conflict_reason = Column(String(200), nullable=True)
    # 主体来源与生命周期：显式创建的主体即使尚未绑定材料也可继续配置；
    # 材料自动创建的临时主体在最后一份材料删除后退休，保留记录供审计。
    origin = Column(String(20), nullable=False, default="explicit")
    retired = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())


class BsPacket(Base):
    """主体下的一批报审资料；批次是归集元数据，不代表规则项数量。"""
    __tablename__ = "bs_packet"
    __table_args__ = (Index("ix_bs_packet_subject", "subject_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("bs_subject.id", ondelete="CASCADE"), nullable=False)
    batch_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# 材料解析态取值（契约 v3 §7.2.4 冻结）：普通材料走 uploaded/parsing/done/failed；
# `stored` 是 **ZIP 容器专用**取值——原包已保存、待准备阶段展开。它既不触发
# `parsing_pending` 409 闸（该闸只扫 uploading/parsing），也不代表内容已读取、
# 更不代表子材料已展开。列宽 String(20)，取值须 ≤ 20 字符。
PARSE_STATE_STORED = "stored"


class BsMaterial(Base):
    __tablename__ = "bs_material"
    __table_args__ = (
        Index("ix_bs_material_task", "task_id"),
        Index("ix_bs_material_subject", "subject_id"),
        Index("ix_bs_material_packet", "packet_id"),
        Index("ix_bs_material_replacement_target", "replacement_target_id"),
        Index("ix_bs_material_superseded_by", "superseded_by_id"),
        # T4.1-T2 §2.1 幂等键：同一容器内「规范化相对路径」唯一。MySQL 与 SQLite
        # 的唯一索引都允许多行 NULL，故普通 PDF 与历史材料（两列均 NULL）互不冲突，
        # 派生材料两列均有值。容器级「重试/重复启动不重复建行」由它兜底。
        Index("uq_bs_material_container_entry", "container_material_id", "container_entry_path",
              unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    # 主体中心归集：历史材料可为空；新上传的分包/报审材料由用户选择主体。
    subject_id = Column(Integer, ForeignKey("bs_subject.id", ondelete="SET NULL"), nullable=True)
    packet_id = Column(Integer, ForeignKey("bs_packet.id", ondelete="SET NULL"), nullable=True)
    material_category = Column(String(30), nullable=True)  # general/sub/labor/packet；NULL=历史材料未标注
    file_name = Column(String(255), nullable=False)
    # classify.py 的五类；自动归类只是初始值，人工更正后 role_confirmed=True
    role = Column(String(20), nullable=False, default="unknown")
    role_confirmed = Column(Boolean, nullable=False, default=False)
    # upload=用户上传 / ledger=本项目已入库合同带入（复用免重解析）
    via = Column(String(20), nullable=False, default="upload")
    # app/uploads 下相对路径（与 structural_check 同约定）
    path = Column(String(500), nullable=True)
    size_kb = Column(Integer, nullable=False, default=0)
    pages = Column(Integer, nullable=True)
    # 文本层逐页提取产物 [{"page": 1, "text": "..."}]；扫描件为空列表（见 degradations）
    pages_json = Column(JSON, nullable=True, default=list)
    # uploaded / parsing / done / failed
    parse_state = Column(String(20), nullable=False, default="uploaded")
    # 降级发生时就记录（对齐 structural_check 降级契约口径）
    degradations_json = Column(JSON, nullable=True, default=list)
    # 图像判章结果（W4 复用 OC _local_seal_detect）：[{page_idx(1-based), bbox, ...}]
    # 空列表 = 未检出信号（不得据此判缺章，OC 硬护栏语义）；None = 未跑判章
    seal_json = Column(JSON, nullable=True, default=list)
    # T4.1-T2 §2.1 / 契约 v3 §7.4 来源可追溯：容器派生子材料的自引用与 ZIP 内
    # 规范化相对路径（`\`→`/`，无 `..`、无空段、无 `.` 段）。两列**可空**以兼容
    # 全部既有 PDF 与历史行（一律 NULL、不回填伪来源）。
    # 刻意**不加数据库外键**：项目既有纪律是「删除记录显式删、不依赖 FK
    # ondelete CASCADE」（sqlite 需 PRAGMA foreign_keys=ON 才生效，见
    # service.delete_task 说明），且 main.py 的幂等迁移无法给存量 MySQL 表补外键——
    # 加了会变成「新装库有 FK、迁移库没有」的两套 schema。父删级联由
    # service.delete_material 显式完成。
    container_material_id = Column(Integer, nullable=True)
    container_entry_path = Column(String(500), nullable=True)
    # D9/F2（设计 114/116）：材料替代链字段一律 nullable、不猜测回填历史版本；
    # revision_no 只在明确的新链中递增，历史行 NULL 表示 legacy_unknown。
    revision_no = Column(Integer, nullable=True)
    # D9/T2-A2：上传候选在解析成功前暂存的替代目标。它不是生效关系；解析失败
    # 或 CAS 冲突时旧材料仍保持当前，成功提交后由服务层清空并写旧节点替代指针。
    replacement_target_id = Column(Integer, nullable=True)
    # 显式替换生效后，旧行记录「被哪份新材料替代」。
    # 刻意**不加数据库外键**（与 container_material_id 同纪律：存量表补 FK 会变成
    # 「新装库有、迁移库没有」的两套 schema；父删级联由 service 显式完成）。
    superseded_by_id = Column(Integer, nullable=True)
    superseded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# 事实表 schema 版本：写行时固化，读取侧据此判断字段语义；不就地升级历史行。
FACT_SCHEMA_VERSION = "material-facts.v1"


class BsMaterialFact(Base):
    """已落库的材料字段事实；append-only，不等同于业务结论。

    写入即不可变：同一材料重新解析产生新的 ``parse_batch_id``，旧批次事实保持
    可读，用于回答“当时依据的是哪一版文本”。因此本表不做业务更新；任务删除时
    的级联清除（D-3）属于授权清除，不是对结论的改写。
    同一批次重放按 ``(material_id, parse_batch_id, field_path, page)`` 幂等：
    写入前查重覆盖常规路径，``uq_bs_material_fact_batch_field_page`` 唯一索引
    兜住并发写入。``page`` 因此收紧为 NOT NULL DEFAULT 0——MySQL/SQLite 的唯一
    索引允许多行 NULL，保留可空列会得到“部分生效”的假约束；「无页码」统一记 0。
    """

    __tablename__ = "bs_material_fact"
    __table_args__ = (
        Index("ix_bs_material_fact_material", "material_id"),
        Index("ix_bs_material_fact_task_field", "task_id", "field_path"),
        Index("ix_bs_material_fact_batch", "material_id", "parse_batch_id"),
        # 并发幂等兜底：同一批次内同字段同页只能有一行。索引列宽按 utf8mb4 计
        # 约 752 字节（64+120 字符），低于 MySQL 8 InnoDB 3072 字节上限。
        Index(
            "uq_bs_material_fact_batch_field_page",
            "material_id", "parse_batch_id", "field_path", "page",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    material_id = Column(Integer, ForeignKey("bs_material.id", ondelete="CASCADE"), nullable=False)
    generation_id = Column(String(100), nullable=True)
    field_path = Column(String(120), nullable=False)
    raw_value = Column(Text, nullable=False)
    normalized_value = Column(Text, nullable=True)
    # 页号 1 起；0 表示该字段无法定位到具体页（无页码/整册级证据）。
    page = Column(Integer, nullable=False, default=0, server_default=text("0"))
    quote = Column(Text, nullable=True)
    quality = Column(String(30), nullable=False, default="readable")
    confidence = Column(Float, nullable=True)
    # online_ocr_text / local_ocr_text / embedded_text_layer；由页面实际来源推导，
    # 不统一写成 online_ocr_text。历史行若未知保持 "unknown"。
    source_kind = Column(String(40), nullable=False, default="unknown")
    # 解析批次身份：同一内容 + 同一抽取器版本视为同一批次，重放幂等；
    # 内容或抽取器变化则新增批次，历史批次不覆盖。
    parse_batch_id = Column(String(64), nullable=False, default="legacy")
    schema_version = Column(String(20), nullable=False, default=FACT_SCHEMA_VERSION)
    extractor_version = Column(String(80), nullable=False)
    # 解析输入的内容指纹；NULL 表示历史/未知，不回填伪造值。
    content_sha256 = Column(String(64), nullable=True)
    # 抽取时材料的版本号（BsMaterial.revision_no）；NULL 表示未进入版本链。
    material_version = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# 关系 schema 版本：三态关系落行时固化，读取侧据此判断维度语义；不就地升级历史行。
RELATION_SCHEMA_VERSION = "evidence-relation.v1"


class BsEvidenceRelation(Base):
    """人员名单与证书的跨材料三态关系；append-only 候选判定，不是业务结论。

    同一人员重新解析会产生新的关系行（指纹含事实行与解析器版本），旧行保留，
    用于回答“当时依据的是哪一版文本”。``status`` 只有 matched / conflict /
    unresolved 三值：字段缺失、遮挡或不可读一律 unresolved，不因同名推断同人。
    任务删除时的级联清除属于授权清除，不是对结论的改写。
    """

    __tablename__ = "bs_evidence_relation"
    __table_args__ = (
        Index("ix_bs_evidence_relation_task_person", "task_id", "person_key"),
        Index("ix_bs_evidence_relation_task_status", "task_id", "status"),
        # 并发幂等兜底：同一任务同一关系指纹只能有一行。指纹 = sha256(任务 + 人员 +
        # 两侧定位 + 事实行 + 状态 + 解析器版本)，因此重算不覆盖、不重复。
        Index("uq_bs_evidence_relation_key", "task_id", "relation_key", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("bs_subject.id", ondelete="SET NULL"), nullable=True)
    # 补件代次：取自证书材料的 material.supplement_linked；取不到留空，不猜到具体代。
    generation_id = Column(String(100), nullable=True)
    relation_type = Column(String(40), nullable=False, default="person_certificate")
    # 归一化姓名；同一任务同一人员共用一个键，不随岗位变化重命名。
    person_key = Column(String(120), nullable=False)
    person_name = Column(String(60), nullable=True)
    post = Column(String(40), nullable=True)
    # matched / conflict / unresolved
    status = Column(String(20), nullable=False)
    reason = Column(Text, nullable=True)
    # 逐维比较：[{field, expected, actual, result, reason, expected_ref, actual_ref}]
    dimensions_json = Column(JSON, nullable=True, default=list)
    # 名单侧与证书侧的原文定位（material_id / page / quote / ...）
    left_json = Column(JSON, nullable=True, default=dict)
    right_json = Column(JSON, nullable=True, default=dict)
    fact_ids_json = Column(JSON, nullable=True, default=list)
    relation_key = Column(String(64), nullable=False)
    resolver_version = Column(String(80), nullable=False)
    schema_version = Column(String(20), nullable=False, default=RELATION_SCHEMA_VERSION)
    created_at = Column(DateTime, server_default=func.now())


class BsTaskEvent(Base):
    __tablename__ = "bs_task_event"
    __table_args__ = (Index("ix_bs_event_task_seq", "task_id", "sequence"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(60), nullable=False)
    stage = Column(String(40), nullable=True)
    payload_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, server_default=func.now())


class BsMaterialConfirmation(Base):
    """材料要求的人工确认历史，独立于问题采纳回执。"""
    __tablename__ = "bs_material_confirmation"
    __table_args__ = (Index("ix_bs_material_confirmation_task_finding", "task_id", "finding_id", "id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    finding_id = Column(Integer, nullable=False)
    subject_id = Column(Integer, nullable=False)
    material_id = Column(Integer, nullable=False)
    item_id = Column(String(40), nullable=False)
    run_id = Column(String(100), nullable=False)
    token = Column(String(64), nullable=False)
    decision = Column(String(20), nullable=False)
    operator_id = Column(Integer, nullable=False)
    operator_type = Column(String(20), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class BsReviewVersion(Base):
    """审查结果版本快照；重核查/补充材料不得覆盖历史结论和人工回执。"""
    __tablename__ = "bs_review_version"
    __table_args__ = (
        Index("ix_bs_review_version_task", "task_id"),
        Index("uq_bs_review_version_task_no", "task_id", "version_no", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    version_no = Column(Integer, nullable=False)
    trigger = Column(String(30), nullable=False)  # completed / rerun / supplement / material_edit
    snapshot_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now())


# 三态词全站统一（BS-P1-15 / 设计 100 §2）：状态码的唯一来源。
STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE = "hit", "miss", "undecidable"
STATUS_CODES = (STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE)
STATUS_LABELS = {STATUS_HIT: "不符合", STATUS_MISS: "符合", STATUS_UNDECIDABLE: "无法核实"}


class BsFinding(Base):
    """核查结论（A3-01 完整性等）。命中=缺陷成立；不可判定必须带缺什么（AGENTS 纪律 4）。"""
    __tablename__ = "bs_finding"
    __table_args__ = (
        Index("ix_bs_finding_task", "task_id"),
        Index("ix_bs_finding_task_kind_subject_item",
              "task_id", "kind", "subject_id", "item_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("bs_task.id", ondelete="CASCADE"), nullable=False)
    # 逻辑主体引用：NULL 兼容本列上线前的历史结论；不设物理外键，避免 create_all
    # 新表与 ensure_bs_schema 存量补列形成约束漂移。同任务归属由写入服务校验。
    subject_id = Column(Integer, nullable=True)
    material_id = Column(Integer, ForeignKey("bs_material.id", ondelete="SET NULL"), nullable=True)
    item_id = Column(String(40), nullable=False)       # 规则条目 id（report_checklist / negative list）
    kind = Column(String(40), nullable=False)           # completeness / negative_list / ...
    status = Column(String(20), nullable=False)         # hit / miss / undecidable
    message = Column(String(500), nullable=False)
    # 证据：命中/未命中时的原文引文与页码（不可判定时为空，reason 落 message）
    quote = Column(Text().with_variant(Text(), "mysql"), nullable=True)
    page = Column(Integer, nullable=True)
    # 三档定位（pinning.py 落库前钉扎）：precise=引文页内验中 / page=页码保留但引文验不中 /
    # unresolved=无定位（宁缺勿错清空页码）；None=结论无引文（计算类），无原文可点
    location_mode = Column(String(20), nullable=True)
    evidence_json = Column(JSON, nullable=True)  # 多侧原文证据；旧数据保留NULL
    source = Column(String(200), nullable=True)         # 条目出处（回原文口径）
    # 人工复核回执（P1-3 最小闭环）：NULL=未复核；单列单对象，再复核覆盖（最后回执为准）
    review_json = Column(JSON, nullable=True)
    # A-13（B2）严重度/置信档位：severity/confidence 由 severity.py 确定性矩阵判定
    # （落库前经 _replace_findings 单点赋值）；discrepancy_json 语义=规则值 vs 人工复核
    # 回执的冲突留痕（本仓零 LLM，不照搬 OC「LLM 值 vs 规则值」）。均 nullable、无回填。
    severity = Column(String(10), nullable=True)        # high / medium / low
    confidence = Column(String(10), nullable=True)      # high / medium / low
    discrepancy_json = Column(JSON, nullable=True)      # {rule:{severity,confidence}, review:{...}}
    # BS-P1-12 结构化缺件：存 **key**（如 `general_scope`），不存中文标签。
    # 只落库 key 的原因：缺件文案已改过一次（P1-02），把中文存进库等于让历史结论
    # 跟着文案版本走；而 key 稳定、可归并、可统计（P1-06 面板要按材料类汇总）。
    # 中文标签与 key→材料类归并都在 `data/missing_material_map.json`，现读现算。
    # 不可判定时非空；命中/未命中为 NULL。历史数据一律 NULL ⇒ 面板按文案回落。
    missing_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
