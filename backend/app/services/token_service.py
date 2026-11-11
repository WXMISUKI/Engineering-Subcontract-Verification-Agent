"""Token 归因共享入口。chat/iwen/操作Agent 与 plus 模块共用此函数写 TokenUsage。

agent 归因靠 conversation_id → ConversationMapping.agent_id 推导,不在此处传 agent_id。
source: 'chat' / 'iwen' / 'operate' / 'as' / 'plus:<code>',用于区分来源(按模块看 token 成本)。
stage: 阶段级归因(如 plus 模块的 parameter_extraction / agent_review / report_manual),
缺省 None 表示调用方不区分阶段 —— 存量调用方语义不变。
as_session_id: as 会话归属(as_sessions.id)。as 侧 conversation_id 置 None(非
conversation_mappings 体系),会话级累计靠此列;缺省 None = 非 as/无归属,存量调用方零改动。
token_count 的 NULL 语义: None = 未知(上游未上报 usage), 0 = 已知零消耗。
调用方必须在拿不到真实 usage 时传 None, 不得用 0 兜底, 否则会把“未知”记成“零消耗”。
只 db.add,不 commit —— 由调用方决定提交时机(与 chat.py 现状一致)。
"""
import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError

from ..models.models import TokenUsage

logger = logging.getLogger(__name__)


def _as_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def usage_counts(usage: dict | None) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """把上游 usage dict 归一成 (input, output, total)。

    返回 None 表示「未知」：调用方必须原样传给 record()，不能拿 0 兜底。
    上游只给**完整分项**（无 total）时，total 用 input+output；缺任一分项时不推导
    （把缺失分项当 0 会把「未知」算成「已知」）。上游明确给了 0 就是已知零。
    """
    if not usage:
        return None, None, None
    if not any(k in usage for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return None, None, None
    input_count = _as_int(usage.get("prompt_tokens"))
    output_count = _as_int(usage.get("completion_tokens"))
    total = _as_int(usage.get("total_tokens"))
    if total is None and input_count is not None and output_count is not None:
        total = input_count + output_count
    return input_count, output_count, total


def record(*, db, user_id, user_type, conversation_id,
           input_count, output_count, token_count, source="chat", stage=None,
           as_session_id=None, chat_id=None, dedupe_key=None,
           raw_usage=None, model_code=None, unit_type=None, quantity=None,
           billable=None, status="completed", usage_status=None, enqueue=True,
           billing_scene=None, agent_id=None, scope_type=None, scope_id=None):
    """写入一条统一用量台账（TokenUsage），并登记出账 outbox。

    兼容旧调用：只传 input/output/total 时按 token 口径写四桶（缓存未上报
    → 输入按未命中计费并标 ``cache_reported=False``，但 hit 保持未知）。
    新调用点可直接传 ``raw_usage``（上游原始 usage）以保留缓存拆分。

    dedupe_key 非空时用 SAVEPOINT 插入：并发重复由 UNIQUE 约束兜底，
    IntegrityError 视为“已记录”，不抛出、不污染外层消息事务。

    计费是旁路：台账/出账登记失败只记 critical 日志，不阻断业务（shadow 语义）。
    """
    # 上游原始 usage 优先；只给三列时拼一个最小 usage 交给归一器。
    payload = raw_usage
    if payload is None and any(v is not None for v in (input_count, output_count, token_count)):
        payload = {
            "prompt_tokens": input_count,
            "completion_tokens": output_count,
            "total_tokens": token_count,
        }

    row = None
    try:
        from .billing.ledger import build_completed_row
        from .billing.schema import billing_tables_available

        # 旧库/局部测试库若没有计费表，只写最小台账、不解析目录（也自然不登记出账）。
        if billing_tables_available(db.get_bind()):
            row = build_completed_row(
                user_id=user_id,
                user_type=user_type,
                source=source,
                stage=stage,
                conversation_id=conversation_id,
                as_session_id=as_session_id,
                chat_id=chat_id,
                dedupe_key=dedupe_key,
                raw_usage=payload,
                model_code=model_code,
                unit_type=unit_type,
                quantity=quantity,
                billable=billable,
                billing_scene=billing_scene,
                agent_id=agent_id,
                scope_type=scope_type,
                scope_id=scope_id,
                db=db,
            )
            if usage_status:
                row.usage_status = usage_status
            row.status = status
    except Exception:  # noqa: BLE001 —— 计费旁路失败不阻断业务
        logger.exception("用量台账构造失败，退回最小写入 source=%s", source)
        row = None

    if row is None:
        row = TokenUsage(
            user_id=user_id,
            user_type=user_type,
            conversation_id=conversation_id,
            chat_id=chat_id,
            dedupe_key=dedupe_key,
            input_count=input_count,
            output_count=output_count,
            token_count=token_count,
            source=source,
            stage=stage,
            as_session_id=as_session_id,
        )

    if not enqueue:
        # 单次调用完全没上报 usage：只留 unknown 台账，不生成账单（A 要求）。
        row.billing_status = "unknown"
        setattr(row, "_skip_billing_enqueue", True)
    if dedupe_key:
        try:
            with db.begin_nested():
                db.add(row)
        except IntegrityError:
            logger.info("TokenUsage 重复写入已忽略 dedupe_key=%s", dedupe_key)
        except Exception:  # noqa: BLE001
            logger.exception("用量台账写入失败 dedupe_key=%s", dedupe_key)
        return
    # 只 add、不 flush：出账任务由 TokenUsage.after_insert 在同一事务登记（见 billing.ledger）。
    db.add(row)
