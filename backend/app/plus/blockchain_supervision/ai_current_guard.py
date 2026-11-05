"""核查模型上下文的当前代次和在线依据授权；不写入任务。"""
from ...models.models import ConversationMapping, KnowledgeBaseBinding, KnowledgeRef
from ...services.knowledge.context import OWNER_SR_CONVERSATION
from ...services.knowledge.reference_access import authorized_reference_provider
from . import access, knowledge_runs
from .models import BsFinding
from .reference_snapshots import CHECK_GROUPS


class AiContextChanged(Exception):
    """当前输入或授权发生变化，不能使用模型建议。"""


def assert_ai_context_current(db, task, run, user, *, allow_completed=False, require_references=True):
    """调用方必须用新事务调用；调用前后返回的连接身份也必须一致。"""
    current_task = access.require_bs_task(db, task.id, user.id, user.user_type)
    group = run.get("group_id") if isinstance(run, dict) else None
    if (group not in CHECK_GROUPS or run.get("task_id") != current_task.id
            or current_task.state not in ({"running", "review", "done"} if allow_completed else {"running", "review"})
            or run.get("state") != "completed"
            or knowledge_runs.get_reference_run(db, current_task, group_id=group) != run
            or knowledge_runs.material_fingerprint(db, current_task) != run.get("material_fingerprint")
            or knowledge_runs.rule_fingerprint() != run.get("rule_fingerprint")
            or knowledge_runs.dependency_fingerprint(db, group, current_task.id) != run.get("dependency_fingerprint")):
        raise AiContextChanged("核查输入或运行代次已变化")
    findings = (db.query(BsFinding).filter(BsFinding.task_id == current_task.id,
                BsFinding.kind.in_(CHECK_GROUPS[group]["kinds"])).all())
    if knowledge_runs.finding_fingerprint(findings) != run.get("finding_fingerprint"):
        raise AiContextChanged("核查结论已变化")
    mapping = db.get(ConversationMapping, current_task.conversation_id)
    identities = []
    for ref in run.get("references") or []:
        descriptor = KnowledgeRef(owner_type=OWNER_SR_CONVERSATION, owner_key=str(current_task.conversation_id),
            provider_id=ref.get("provider_id"), kb_id=ref.get("kb_id"), knowledge_id=ref.get("knowledge_id"))
        provider = authorized_reference_provider(db, descriptor, user)
        binding = db.get(KnowledgeBaseBinding, ref.get("binding_id"))
        if (provider is None or binding is None or mapping is None or binding.agent_id != mapping.agent_id
                or binding.provider_id != provider.id
                or binding.external_kb_id != ref.get("kb_id") or not binding.is_enabled or not binding.allow_query):
            raise AiContextChanged("参考来源授权已变化")
        identities.append((provider.id, provider.base_url, provider.secret_ref, provider.tenant_ref,
                           binding.id, ref.get("kb_id"), ref.get("knowledge_id")))
    if require_references and not identities:
        raise AiContextChanged("没有可核验的在线参考依据")
    return tuple(sorted(identities, key=str))
