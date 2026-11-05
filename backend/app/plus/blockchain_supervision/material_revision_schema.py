"""D9 material-revision schema preview and isolated SQLite rehearsal.

This module is deliberately not wired into application startup.  It never reads
``DATABASE_URL`` and its public rehearsal function only writes a caller-named,
new SQLite copy.  The current/production database therefore cannot be changed by
importing or previewing this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Collection


MATERIAL_TABLE = "bs_material"
REVISION_INDEX = "ix_bs_material_superseded_by"
REPLACEMENT_INDEX = "ix_bs_material_replacement_target"
REVISION_COLUMNS = (
    "revision_no", "replacement_target_id", "superseded_by_id", "superseded_at"
)

_DDL = {
    "sqlite": {
        "revision_no": "ALTER TABLE bs_material ADD COLUMN revision_no INTEGER NULL;",
        "replacement_target_id": (
            "ALTER TABLE bs_material ADD COLUMN replacement_target_id INTEGER NULL;"
        ),
        "superseded_by_id": (
            "ALTER TABLE bs_material ADD COLUMN superseded_by_id INTEGER NULL;"
        ),
        "superseded_at": "ALTER TABLE bs_material ADD COLUMN superseded_at DATETIME NULL;",
        REVISION_INDEX: (
            "CREATE INDEX ix_bs_material_superseded_by "
            "ON bs_material (superseded_by_id);"
        ),
        REPLACEMENT_INDEX: (
            "CREATE INDEX ix_bs_material_replacement_target "
            "ON bs_material (replacement_target_id);"
        ),
    },
    "mysql": {
        "revision_no": (
            "ALTER TABLE bs_material "
            "ADD COLUMN revision_no INT NULL COMMENT '材料替代链修订号';"
        ),
        "replacement_target_id": (
            "ALTER TABLE bs_material ADD COLUMN replacement_target_id INT NULL "
            "COMMENT '待解析成功后替代的旧材料ID';"
        ),
        "superseded_by_id": (
            "ALTER TABLE bs_material ADD COLUMN superseded_by_id INT NULL "
            "COMMENT '直接替代材料ID（同任务/主体/类别）';"
        ),
        "superseded_at": (
            "ALTER TABLE bs_material ADD COLUMN superseded_at DATETIME NULL "
            "COMMENT '替代事务提交时间';"
        ),
        REVISION_INDEX: (
            "CREATE INDEX ix_bs_material_superseded_by "
            "ON bs_material (superseded_by_id);"
        ),
        REPLACEMENT_INDEX: (
            "CREATE INDEX ix_bs_material_replacement_target "
            "ON bs_material (replacement_target_id);"
        ),
    },
}


def _dialect_family(dialect_name: str) -> str:
    normalized = (dialect_name or "").strip().lower().split("+", 1)[0]
    if normalized in {"sqlite", "mysql", "mariadb"}:
        return "mysql" if normalized == "mariadb" else normalized
    raise ValueError("仅支持 sqlite / mysql DDL 预览")


def build_material_revision_ddl(
    dialect_name: str,
    *,
    existing_columns: Collection[str] = (),
    existing_indexes: Collection[str] = (),
) -> tuple[str, ...]:
    """Return the frozen D9 DDL preview without opening a database connection.

    ``existing_*`` lets an explicit, read-only inspection produce an idempotent
    plan.  Omitting them renders the complete design-116 preview.  Statements
    are data constants rather than SQLAlchemy type compilation because MySQL's
    reviewed contract intentionally uses ``INT`` and human-readable comments.
    """

    dialect = _dialect_family(dialect_name)
    columns = set(existing_columns)
    indexes = set(existing_indexes)
    statements = [
        _DDL[dialect][name] for name in REVISION_COLUMNS if name not in columns
    ]
    for index_name in (REVISION_INDEX, REPLACEMENT_INDEX):
        if index_name not in indexes:
            statements.append(_DDL[dialect][index_name])
    return tuple(statements)


def _open_sqlite_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _table_columns(connection: sqlite3.Connection) -> list[str]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (MATERIAL_TABLE,),
    ).fetchone()
    if not exists:
        raise ValueError("隔离源库不存在 bs_material 表")
    return [row[1] for row in connection.execute(f"PRAGMA table_info({MATERIAL_TABLE})")]


def _table_indexes(connection: sqlite3.Connection) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA index_list({MATERIAL_TABLE})")]


def _legacy_rows_sha256(
    connection: sqlite3.Connection,
    columns: Collection[str],
) -> str:
    names = list(columns)
    quoted = ", ".join('"%s"' % name.replace('"', '""') for name in names)
    order = '"id"' if "id" in names else "rowid"
    digest = hashlib.sha256()
    digest.update(json.dumps(names, ensure_ascii=False).encode("utf-8"))
    for row in connection.execute(
        f"SELECT {quoted} FROM {MATERIAL_TABLE} ORDER BY {order}"
    ):
        digest.update(
            json.dumps(row, ensure_ascii=False, default=repr, separators=(",", ":"))
            .encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _sqlite_snapshot(path: Path, *, legacy_columns: Collection[str] | None = None) -> dict:
    with _open_sqlite_read_only(path) as connection:
        columns = _table_columns(connection)
        indexes = _table_indexes(connection)
        compared_columns = list(legacy_columns) if legacy_columns is not None else columns
        if not set(compared_columns) <= set(columns):
            raise ValueError("隔离副本缺少源库原有列")
        row_count = connection.execute(
            f"SELECT COUNT(*) FROM {MATERIAL_TABLE}"
        ).fetchone()[0]
        new_columns_are_null = None
        if set(REVISION_COLUMNS) <= set(columns):
            non_null = connection.execute(
                f"SELECT COUNT(*) FROM {MATERIAL_TABLE} "
                "WHERE revision_no IS NOT NULL "
                "OR replacement_target_id IS NOT NULL "
                "OR superseded_by_id IS NOT NULL OR superseded_at IS NOT NULL"
            ).fetchone()[0]
            new_columns_are_null = non_null == 0
        return {
            "row_count": row_count,
            "legacy_columns": compared_columns,
            "legacy_rows_sha256": _legacy_rows_sha256(connection, compared_columns),
            "revision_columns": sorted(set(columns) & set(REVISION_COLUMNS)),
            "revision_indexes": sorted(
                set(indexes) & {REVISION_INDEX, REPLACEMENT_INDEX}
            ),
            "new_columns_are_null": new_columns_are_null,
        }


def rehearse_sqlite_material_revision_copy(
    source_path: str | Path,
    target_path: str | Path,
) -> dict:
    """Apply the D9 plan to a new disposable SQLite copy and read it back.

    The source is inspected in ``query_only`` mode and is never opened for a
    write.  The target must not exist and must resolve to a different path, so
    callers cannot accidentally turn a rehearsal into an in-place migration.
    """

    source = Path(source_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("隔离源库文件不存在")
    if source == target:
        raise ValueError("隔离副本路径不得与源库相同")
    if target.exists():
        raise ValueError("隔离副本目标已存在，拒绝覆盖")

    before = _sqlite_snapshot(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    copied = False
    try:
        shutil.copy2(source, target)
        copied = True
        with sqlite3.connect(target) as connection:
            columns = _table_columns(connection)
            indexes = _table_indexes(connection)
            statements = build_material_revision_ddl(
                "sqlite", existing_columns=columns, existing_indexes=indexes
            )
            for statement in statements:
                connection.execute(statement)
            connection.commit()
        after = _sqlite_snapshot(target, legacy_columns=before["legacy_columns"])
    except Exception:
        if copied and target.exists():
            target.unlink()
        raise

    legacy_rows_unchanged = (
        before["row_count"] == after["row_count"]
        and before["legacy_rows_sha256"] == after["legacy_rows_sha256"]
    )
    return {
        "source": str(source),
        "isolated_copy": str(target),
        "applied_statements": list(statements),
        "before": before,
        "after": after,
        "legacy_rows_unchanged": legacy_rows_unchanged,
        "new_columns_are_null": after["new_columns_are_null"] is True,
    }
