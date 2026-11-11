"""计费 schema 的启动幂等迁移（设计 §12）。

``create_all`` 只建缺失的表，**不会给已存在的 token_usage 补列**；老库因此
必须用显式 ALTER 补齐。新表由 create_all 负责，本函数只处理旧表补列/补索引。
SQL 文件 ``backend/sql/011_billing_model_catalog.sql`` 与
``backend/sql/012_billing_attribution_ui.sql`` 是同一份 DDL 的留档。
"""

import logging
from functools import lru_cache

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


@lru_cache(maxsize=64)
def _billing_tables_available_cached(engine) -> bool:
    try:
        names = set(inspect(engine).get_table_names())
        return {"billing_outbox", "billing_settings", "usage_charges", "credit_ledger"} <= names
    except Exception:  # noqa: BLE001
        return False


def billing_tables_available(bind) -> bool:
    """该连接对应的库是否有计费表。

    旧库/未跑 create_all 的测试库可能只有 token_usage；此时台账仍可写
    （新列已由迁移补上），但**不登记出账**，避免每次 flush 报 no such table。
    """
    if bind is None:
        return False
    engine = getattr(bind, "engine", bind)
    try:
        return _billing_tables_available_cached(engine)
    except Exception:  # noqa: BLE001
        return False

NEW_COLUMNS = (
    ("unit_type", "VARCHAR(16) NOT NULL DEFAULT 'token' COMMENT 'token/page/image/char/second'"),
    ("unit_scale", "INT NOT NULL DEFAULT 1 COMMENT '计价倍率:token/char=1000,其余=1'"),
    ("billable_quantity", "DECIMAL(18,3) NULL COMMENT '非 token 数量;token 行为 NULL'"),
    ("billable", "TINYINT(1) NOT NULL DEFAULT 1 COMMENT '本地模型=false,只统计不扣费'"),
    ("status", "VARCHAR(16) NOT NULL DEFAULT 'completed' COMMENT 'started/completed/failed/interrupted'"),
    ("started_at", "DATETIME NULL"),
    ("finished_at", "DATETIME NULL"),
    ("model_code", "VARCHAR(64) NULL COMMENT '实际使用的模型编码'"),
    ("provider_code", "VARCHAR(32) NULL"),
    ("cache_hit_input_count", "INT NULL"),
    ("cache_miss_input_count", "INT NULL"),
    ("cache_write_input_count", "INT NULL"),
    ("cache_reported", "TINYINT(1) NULL COMMENT '是否上报过缓存字段'"),
    ("usage_status", "VARCHAR(16) NULL COMMENT 'complete/partial/unknown/legacy'"),
    ("raw_usage_json", "JSON NULL COMMENT '上游原始 usage'"),
    ("request_id", "VARCHAR(64) NULL"),
    ("billing_status", "VARCHAR(16) NULL COMMENT 'pending/rated/unknown/legacy/price_missing/exempt'"),
    # 计费归因（设计 §18 第 4 条）：历史行保持 NULL，不补数据。
    ("billing_scene", "VARCHAR(32) NULL COMMENT 'scenes.py 场景编码'"),
    ("agent_id", "INT NULL COMMENT '智能体归属;NULL=解析不到'"),
    ("billing_scope_type", "VARCHAR(32) NULL COMMENT 'as_session/oc_task/sr_conversation/bs_task/sc_task'"),
    ("billing_scope_id", "VARCHAR(64) NULL"),
)

NEW_INDEXES = (
    ("idx_token_usage_status", "CREATE INDEX idx_token_usage_status ON token_usage (status)"),
    ("idx_token_usage_billing_status", "CREATE INDEX idx_token_usage_billing_status ON token_usage (billing_status)"),
    ("idx_token_usage_unit_type", "CREATE INDEX idx_token_usage_unit_type ON token_usage (unit_type)"),
    ("idx_token_usage_scene", "CREATE INDEX idx_token_usage_scene ON token_usage (billing_scene)"),
    ("idx_token_usage_agent", "CREATE INDEX idx_token_usage_agent ON token_usage (agent_id)"),
)

# 其它已存在的计费表补列（create_all 不会给已存在的表补列）。
TABLE_COLUMNS = {
    "billing_settings": (
        ("credits_per_yuan", "INT NOT NULL DEFAULT 100 COMMENT '每元多少积分;默认100'"),
        ("min_balance_micro", "BIGINT NOT NULL DEFAULT 100000000 COMMENT 'Plus 无法预估任务的启动最低余额;新建默认100积分'"),
        # 014 一次性迁移标记：旧表补列默认 0（待回填），新表由模型 server_default=1。
        ("min_balance_default_migrated", "TINYINT(1) NOT NULL DEFAULT 0 COMMENT '014 旧默认0一次性回填标记'"),
        ("default_output_tokens", "INT NOT NULL DEFAULT 1024 COMMENT '平台预期输出上限'"),
        ("scene_default_input_tokens", "INT NOT NULL DEFAULT 2000 COMMENT 'Plus 场景默认输入 token'"),
        ("scene_default_output_tokens", "INT NOT NULL DEFAULT 1024 COMMENT 'Plus 场景默认输出 token'"),
        ("model_key_mode", "VARCHAR(16) NOT NULL DEFAULT 'shared' COMMENT 'shared / per_model'"),
        # 2026-11-05：默认价基线迁移标记；一次性应用新价并重算历史 token 账单。
        ("price_baseline_key", "VARCHAR(64) NULL COMMENT '默认价基线迁移标记；匹配则跳过'"),
    ),
    "llm_models": (
        ("api_key_ref", "VARCHAR(128) NULL COMMENT '模型级 Key 的 env/secret 引用名；不存明文'"),
        # 2026-11-02：必须在计费种子 bootstrap_billing 之前补列；种子会查询该字段。
        ("context_size_limit", "INT NULL COMMENT '模型官方最大输入上下文；生效 context_size 不得超过它'"),
    ),
    "model_price_versions": (
        # 2026-11-05：百炼输入长度阶梯价。NULL/空=单档价。
        ("tiers_json", "JSON NULL COMMENT '阶梯价:[{up_to_input_tokens,四桶权重}]；按单次输入token选档'"),
    ),
    "billing_estimates": (
        ("user_type", "VARCHAR(20) NOT NULL DEFAULT '' COMMENT '预估归属用户类型'"),
        ("user_id", "INT NOT NULL DEFAULT 0 COMMENT '预估归属用户 id'"),
        ("input_basis", "VARCHAR(24) NULL COMMENT 'request_text/scene_default/missing'"),
        ("output_basis", "VARCHAR(24) NULL COMMENT 'request/model_max_output/scene_default/platform_default/missing'"),
    ),
    "usage_charges": (
        ("billing_scene", "VARCHAR(32) NULL"),
        ("agent_id", "INT NULL"),
        ("billing_scope_type", "VARCHAR(32) NULL"),
        ("billing_scope_id", "VARCHAR(64) NULL"),
    ),
    "credit_ledger": (
        ("billing_scene", "VARCHAR(32) NULL"),
        ("agent_id", "INT NULL"),
        ("billing_scope_type", "VARCHAR(32) NULL"),
        ("billing_scope_id", "VARCHAR(64) NULL"),
    ),
}

TABLE_INDEXES = (
    ("idx_usage_charges_scope", "CREATE INDEX idx_usage_charges_scope ON usage_charges (billing_scope_type, billing_scope_id)"),
    ("idx_credit_ledger_scene", "CREATE INDEX idx_credit_ledger_scene ON credit_ledger (billing_scene)"),
    ("idx_credit_ledger_agent", "CREATE INDEX idx_credit_ledger_agent ON credit_ledger (agent_id)"),
)


def ensure_model_bindings_schema(engine) -> dict:
    """会话/任务级模型绑定表（设计 §4.11、§8.1）。

    新库由 ``create_all`` 建表；本函数只对「表缺失」的旧库补建（create_all 已覆盖，
    这里显式留一步便于启动日志复核）。幂等：存在即空报告。
    """
    report = {"created": False}
    try:
        insp = inspect(engine)
        if "model_bindings" in insp.get_table_names():
            return report
        from ...models.billing_models import ModelBinding

        ModelBinding.__table__.create(bind=engine, checkfirst=True)
        report["created"] = True
        logger.info("model_bindings 表已补建")
    except Exception:  # noqa: BLE001 —— 建表失败不阻断启动，create_all 通常会先建好
        logger.warning("model_bindings 表补建失败（不影响启动）", exc_info=True)
    return report


def ensure_billing_schema(engine) -> dict:
    report = {"added_columns": [], "added_indexes": []}
    insp = inspect(engine)
    if "token_usage" not in insp.get_table_names():
        return report
    existing_cols = {c["name"] for c in insp.get_columns("token_usage")}
    existing_idx = {i["name"] for i in inspect(engine).get_indexes("token_usage")}

    all_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for name, ddl in NEW_COLUMNS:
            if name in existing_cols:
                continue
            conn.execute(text(f"ALTER TABLE token_usage ADD COLUMN {name} {ddl}"))
            report["added_columns"].append(f"token_usage.{name}")
        for name, ddl in NEW_INDEXES:
            if name in existing_idx:
                continue
            try:
                conn.execute(text(ddl))
                report["added_indexes"].append(name)
            except Exception:  # noqa: BLE001 —— 索引已存在/权限不足不阻断启动
                logger.warning("创建计费索引 %s 失败（不影响启动）", name, exc_info=True)

        # 其它计费表：create_all 只建缺失表，不会给已存在的旧表补列。
        for table, columns in TABLE_COLUMNS.items():
            if table not in all_tables:
                continue
            table_cols = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns:
                if name in table_cols:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                report["added_columns"].append(f"{table}.{name}")

        # 014 的一次性数据自愈（靠标记幂等）：旧表补列默认 0，ALTER 只改默认值不动存量
        # 行，导致「enforce 下 Plus 无预估任务实际不设门槛」。对所有
        # min_balance_default_migrated=0 的旧行做一次判定：值为 0 → 回填 100 积分，
        # 非 0 保留原值；随后置标记 1。管理员经 API 显式保存 min_balance（含 0）会
        # 直接置标记 1，之后启动不再覆盖；直接改库设 0 会在下次启动被回填，
        # 如需保留 0 请在管理端保存一次。
        if "billing_settings" in all_tables:
            result = conn.execute(text(
                "UPDATE billing_settings "
                "SET min_balance_micro = CASE WHEN min_balance_micro = 0 THEN :v "
                "ELSE min_balance_micro END, "
                "min_balance_default_migrated = 1 "
                "WHERE min_balance_default_migrated = 0"
            ), {"v": 100_000_000})
            if getattr(result, "rowcount", 0):
                report.setdefault("backfilled", []).append("billing_settings.min_balance_micro")

        # 分包审查的 OCR 在 2026-11-02 前只带 scope=bs_task、不带 conversation_id，
        # 导致用量已出账但会话气泡（只按 conversation_id 汇总）看不到。按任务回填
        # 会话归属；条件带 conversation_id IS NULL，重复启动不会改写已归好的行。
        if {"token_usage", "bs_task"} <= all_tables:
            result = conn.execute(text(
                "UPDATE token_usage SET conversation_id = ("
                " SELECT t.conversation_id FROM bs_task t"
                " WHERE CAST(t.id AS CHAR) = token_usage.billing_scope_id"
                ") WHERE conversation_id IS NULL AND billing_scope_type = 'bs_task'"
                " AND EXISTS (SELECT 1 FROM bs_task t"
                " WHERE CAST(t.id AS CHAR) = token_usage.billing_scope_id)"
            ))
            if getattr(result, "rowcount", 0):
                report.setdefault("backfilled", []).append(
                    "token_usage.conversation_id(bs_task)"
                )

        # billing_estimates 唯一键从 (scope_type, scope_id) 换成带归属的四元组。
        # MySQL 支持 DROP INDEX；SQLite 老库不做表重建（新库由 create_all 建新键）。
        if "billing_estimates" in all_tables and engine.dialect.name == "sqlite":
            unique_names = {
                u.get("name") for u in insp.get_unique_constraints("billing_estimates")
            }
            if "uq_billing_estimate_scope" in unique_names:
                # SQLite 不能 DROP 约束，重建表风险高；开发库直接删库重建。
                logger.warning(
                    "SQLite 旧开发库的 billing_estimates 仍使用旧唯一键 "
                    "uq_billing_estimate_scope；请删除开发库重建（或手工重建该表），"
                    "否则不同用户同一 scope 仍会互相覆盖。"
                )
        if "billing_estimates" in all_tables and engine.dialect.name == "mysql":
            unique_names = {
                u.get("name") for u in insp.get_unique_constraints("billing_estimates")
            }
            if "uq_billing_estimate_scope" in unique_names:
                conn.execute(text("ALTER TABLE billing_estimates DROP INDEX uq_billing_estimate_scope"))
                report["added_indexes"].append("billing_estimates.uq_billing_estimate_scope(dropped)")
            if "uq_billing_estimate_owner_scope" not in unique_names:
                conn.execute(text(
                    "ALTER TABLE billing_estimates ADD UNIQUE KEY "
                    "uq_billing_estimate_owner_scope (user_type, user_id, scope_type, scope_id)"
                ))
                report["added_indexes"].append("billing_estimates.uq_billing_estimate_owner_scope")
        for name, ddl in TABLE_INDEXES:
            table = ddl.split(" ON ", 1)[1].split(" ", 1)[0]
            if table not in all_tables:
                continue
            existing_table_idx = {i["name"] for i in insp.get_indexes(table)}
            if name in existing_table_idx:
                continue
            try:
                conn.execute(text(ddl))
                report["added_indexes"].append(name)
            except Exception:  # noqa: BLE001
                logger.warning("创建计费索引 %s 失败（不影响启动）", name, exc_info=True)
    if report["added_columns"]:
        logger.info("计费 schema 迁移完成：补列 %s", report["added_columns"])
    return report