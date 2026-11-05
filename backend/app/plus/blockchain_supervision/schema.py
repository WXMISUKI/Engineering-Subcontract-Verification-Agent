"""Narrow legacy-table repair for existing BS subject/finding columns and index.

create_all handles new tables, but cannot add columns to existing tables.
Types, nullability and compatible defaults come from the current ORM models.
MySQL DDL commits implicitly: callers must keep a backup before execution.
"""
from sqlalchemy import inspect, literal
from sqlalchemy.engine import Engine

from .models import BsFinding, BsSubject, BsTask


def ensure_bs_schema(engine: Engine) -> dict[str, list[str]]:
    """幂等补齐历史遗漏列与主体级结论索引，保留原行及 NULL 兼容语义。"""
    report: dict[str, list[str]] = {
        "added_columns": [], "added_indexes": [], "skipped_tables": [],
    }
    targets = (
        (BsSubject.__table__, ("origin", "retired")),
        (BsFinding.__table__, ("evidence_json", "missing_json", "subject_id")),
    )
    with engine.begin() as connection:
        quote = connection.dialect.identifier_preparer.quote
        for table, names in targets:
            inspector = inspect(connection)
            if not inspector.has_table(table.name):
                report["skipped_tables"].append(table.name)
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for name in names:
                if name in existing:
                    continue
                column = table.c[name]
                definition = f"{quote(name)} {column.type.compile(dialect=connection.dialect)}"
                if not column.nullable:
                    definition += " NOT NULL"
                if column.default is not None and column.default.is_scalar:
                    default = literal(column.default.arg).compile(
                        dialect=connection.dialect, compile_kwargs={"literal_binds": True},
                    )
                    definition += f" DEFAULT {default}"
                connection.exec_driver_sql(
                    f"ALTER TABLE {quote(table.name)} ADD COLUMN {definition}"
                )
                report["added_columns"].append(f"{table.name}.{name}")

        # 极早期测试/存量表可能连 task_id、kind、item_id 都没有；补列仍可安全
        # 完成，但组合索引必须等四个前置列全部存在后再建，不能让启动迁移失败。
        inspector = inspect(connection)
        if inspector.has_table(BsFinding.__tablename__):
            existing_columns = {
                column["name"] for column in inspector.get_columns(BsFinding.__tablename__)
            }
            index_name = "ix_bs_finding_task_kind_subject_item"
            existing_indexes = {
                index["name"] for index in inspector.get_indexes(BsFinding.__tablename__)
            }
            if ({"task_id", "kind", "subject_id", "item_id"} <= existing_columns
                    and index_name not in existing_indexes):
                index = next(
                    item for item in BsFinding.__table__.indexes if item.name == index_name
                )
                index.create(bind=connection)
                report["added_indexes"].append(index_name)
    return report


def ensure_bs_task_scope_schema(
    engine: Engine, *, allow_ddl: bool = True,
) -> dict[str, list[str]]:
    """幂等补齐 P4-05 任务辖域字段，历史行保持 NULL。

    服务启动默认不允许执行这段 DDL；迁移窗口可显式传入 ``allow_ddl=True``。
    这样旧库缺少候选字段时，启动只读链路不会偷偷改表结构。
    """
    report: dict[str, list[str]] = {
        "added_columns": [], "skipped_tables": [],
    }
    if not allow_ddl:
        report["skipped_tables"].append("ddl_disabled")
        return report
    with engine.begin() as connection:
        quote = connection.dialect.identifier_preparer.quote
        inspector = inspect(connection)
        if not inspector.has_table(BsTask.__tablename__):
            report["skipped_tables"].append(BsTask.__tablename__)
            return report
        existing = {column["name"] for column in inspector.get_columns(BsTask.__tablename__)}
        for name in ("jurisdiction", "profile_code"):
            if name in existing:
                continue
            column = BsTask.__table__.c[name]
            definition = f"{quote(name)} {column.type.compile(dialect=connection.dialect)}"
            connection.exec_driver_sql(
                f"ALTER TABLE {quote(BsTask.__tablename__)} ADD COLUMN {definition}"
            )
            report["added_columns"].append(f"{BsTask.__tablename__}.{name}")
    return report


