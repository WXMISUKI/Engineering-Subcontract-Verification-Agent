"""独立版数据库的幂等结构补齐与旧用量台账迁移。"""

import json

from sqlalchemy import inspect, text


def _add_columns(engine, table: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table)}
    with engine.begin() as connection:
        for name, ddl in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _normalise_legacy_usage(raw_usage):
    """提取旧台账能够确认的 token 三元组，缺失值保持 NULL。"""
    if isinstance(raw_usage, dict):
        payload = raw_usage
    elif raw_usage:
        try:
            payload = json.loads(raw_usage)
        except (TypeError, ValueError):
            payload = {}
    else:
        payload = {}

    def as_int(value):
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None

    input_count = as_int(payload.get("prompt_tokens", payload.get("input_tokens")))
    output_count = as_int(payload.get("completion_tokens", payload.get("output_tokens")))
    total_count = as_int(payload.get("total_tokens", payload.get("token_count")))
    if total_count is None and input_count is not None and output_count is not None:
        total_count = input_count + output_count
    return input_count, output_count, total_count, payload


def _migrate_legacy_usage(engine) -> int:
    """把旧 standalone_token_usage 幂等复制到统一 token_usage。

    旧表保留一段时间用于回滚和审计；统一表中的行标记为 legacy，绝不补扣历史费用。
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "standalone_token_usage" not in tables or "token_usage" not in tables:
        return 0

    inserted = 0
    with engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT id, user_id, user_type, conversation_id, model_code, source, stage, "
            "scope_type, scope_id, raw_usage, created_at FROM standalone_token_usage"
        )).mappings()
        for row in rows:
            dedupe_key = f"legacy:standalone_token_usage:{row['id']}"
            exists = connection.execute(
                text("SELECT 1 FROM token_usage WHERE dedupe_key = :dedupe_key LIMIT 1"),
                {"dedupe_key": dedupe_key},
            ).first()
            if exists:
                continue
            input_count, output_count, total_count, payload = _normalise_legacy_usage(row["raw_usage"])
            connection.execute(text(
                "INSERT INTO token_usage (user_id, user_type, conversation_id, dedupe_key, "
                "input_count, output_count, token_count, source, stage, model_code, unit_type, "
                "unit_scale, billable, status, usage_status, raw_usage_json, billing_status, "
                "billing_scene, billing_scope_type, billing_scope_id, created_at) VALUES ("
                ":user_id, :user_type, :conversation_id, :dedupe_key, :input_count, :output_count, "
                ":token_count, :source, :stage, :model_code, 'token', 1, 1, 'completed', 'legacy', "
                ":raw_usage_json, 'legacy', :billing_scene, :scope_type, :scope_id, :created_at)"
            ), {
                "user_id": row["user_id"],
                "user_type": row["user_type"],
                "conversation_id": row["conversation_id"],
                "dedupe_key": dedupe_key,
                "input_count": input_count,
                "output_count": output_count,
                "token_count": total_count,
                "source": row["source"],
                "stage": row["stage"],
                "model_code": row["model_code"],
                "raw_usage_json": json.dumps(payload, ensure_ascii=False),
                "billing_scene": row["scope_type"] if row["scope_type"] == "bs_task" else None,
                "scope_type": row["scope_type"],
                "scope_id": row["scope_id"],
                "created_at": row["created_at"],
            })
            inserted += 1
    return inserted


def _backfill_task_timing(engine) -> int:
    """为上线计时字段前已启动的任务建立保守起点。"""
    inspector = inspect(engine)
    if "bs_task" not in inspector.get_table_names():
        return 0
    changed = 0
    with engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT id, state, created_at, updated_at, started_at, completed_at "
            "FROM bs_task WHERE state <> 'setup'"
        )).mappings()
        for row in rows:
            started_at = row["started_at"] or row["created_at"]
            completed_at = row["completed_at"]
            if row["state"] == "done" and completed_at is None:
                completed_at = row["updated_at"] or started_at
            elapsed_seconds = None
            if row["state"] == "done" and started_at and completed_at:
                elapsed_seconds = max(0.0, (completed_at - started_at).total_seconds())
            if started_at != row["started_at"] or completed_at != row["completed_at"]:
                connection.execute(text(
                    "UPDATE bs_task SET started_at = :started_at, completed_at = :completed_at, "
                    "elapsed_seconds = COALESCE(elapsed_seconds, :elapsed_seconds) WHERE id = :id"
                ), {"started_at": started_at, "completed_at": completed_at,
                    "elapsed_seconds": elapsed_seconds, "id": row["id"]})
                changed += 1
    return changed


def ensure_standalone_schema(engine) -> dict:
    """补齐计费/任务计时列；重复执行安全。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    renamed_usage = False
    if "token_usage" not in tables and "standalone_token_usage" in tables:
        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.execute(text("ALTER TABLE standalone_token_usage RENAME TO token_usage"))
            else:
                connection.execute(text("RENAME TABLE standalone_token_usage TO token_usage"))
        renamed_usage = True

    _add_columns(engine, "bs_finding", {"ai_adjudication_json": "JSON NULL"})
    _add_columns(engine, "bs_task", {
        "started_at": "DATETIME NULL",
        "completed_at": "DATETIME NULL",
        "elapsed_seconds": "DOUBLE NULL",
    })
    _add_columns(engine, "agents", {
        "name": "VARCHAR(100) NOT NULL DEFAULT '工程分包审查智能体'",
        "description": "TEXT NULL",
        "usage_count": "INTEGER NOT NULL DEFAULT 0",
        "market_status": "VARCHAR(16) NOT NULL DEFAULT 'published'",
    })
    _add_columns(engine, "token_usage", {
        "chat_id": "VARCHAR(100) NULL",
        "dedupe_key": "VARCHAR(120) NULL",
        "input_count": "INTEGER NULL",
        "output_count": "INTEGER NULL",
        "token_count": "INTEGER NULL",
        "as_session_id": "VARCHAR(36) NULL",
        "unit_type": "VARCHAR(16) NOT NULL DEFAULT 'token'",
        "unit_scale": "INTEGER NOT NULL DEFAULT 1",
        "billable_quantity": "DECIMAL(18,3) NULL",
        "billable": "BOOLEAN NOT NULL DEFAULT 1",
        "status": "VARCHAR(16) NOT NULL DEFAULT 'completed'",
        "started_at": "DATETIME NULL",
        "finished_at": "DATETIME NULL",
        "provider_code": "VARCHAR(32) NULL",
        "cache_hit_input_count": "INTEGER NULL",
        "cache_miss_input_count": "INTEGER NULL",
        "cache_write_input_count": "INTEGER NULL",
        "cache_reported": "BOOLEAN NULL",
        "usage_status": "VARCHAR(16) NULL",
        "raw_usage_json": "JSON NULL",
        "request_id": "VARCHAR(64) NULL",
        "billing_status": "VARCHAR(16) NULL",
        "billing_scene": "VARCHAR(32) NULL",
        "agent_id": "INTEGER NULL",
        "billing_scope_type": "VARCHAR(32) NULL",
        "billing_scope_id": "VARCHAR(64) NULL",
    })
    migrated_usage = _migrate_legacy_usage(engine)
    backfilled_timing = _backfill_task_timing(engine)
    return {
        "renamed_usage": renamed_usage,
        "migrated_usage": migrated_usage,
        "backfilled_timing": backfilled_timing,
    }
