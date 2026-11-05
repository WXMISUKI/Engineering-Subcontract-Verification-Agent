"""分包依据读取按当前账号、绑定和文件范围逐项授权。"""
from ...models.models import ConversationMapping, KnowledgeBaseBinding, KnowledgeProvider
from .binding_service import authorized_file_scope
from .context import OWNER_SR_CONVERSATION
from .provider_client import ProviderFileTooLargeError, ProviderNotFoundError, SecretResolutionError, WeKnoraAdminClient


class ReferenceOriginalAccessError(RuntimeError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def authorized_reference_provider(db, reference, user):
    if reference.owner_type != OWNER_SR_CONVERSATION:
        return None
    try:
        owner_id = int(reference.owner_key)
    except (TypeError, ValueError):
        return None
    owner = db.get(ConversationMapping, owner_id)
    if (owner is None or owner.is_deleted or owner.user_id != user.id
            or owner.user_type != user.user_type or reference.provider_id is None):
        return None
    binding = db.query(KnowledgeBaseBinding).filter_by(
        agent_id=owner.agent_id, provider_id=reference.provider_id,
        external_kb_id=reference.kb_id, is_enabled=True, allow_query=True).first()
    if binding is None:
        return None
    selected = authorized_file_scope(binding.selected_knowledge_ids)
    if selected is not None and reference.knowledge_id not in selected:
        return None
    provider = db.get(KnowledgeProvider, reference.provider_id)
    return provider if provider and provider.enabled and provider.provider_type == 'weknora' else None


def read_authorized_reference_original(db, reference, user, *, max_bytes, timeout_seconds=None):
    provider = authorized_reference_provider(db, reference, user)
    if provider is None:
        raise ReferenceOriginalAccessError(404, '该原文当前未获授权，请联系管理员核对知识库权限')
    try:
        options = {} if timeout_seconds is None else {'timeout_seconds': timeout_seconds}
        with WeKnoraAdminClient(provider.base_url, secret_ref=provider.secret_ref,
                                tenant_ref=provider.tenant_ref, **options) as client:
            limits = {} if timeout_seconds is None else {'overall_timeout_seconds': timeout_seconds}
            data, _ = client.fetch_knowledge_original(reference.kb_id, reference.knowledge_id,
                                                      max_bytes=max_bytes, **limits)
    except SecretResolutionError:
        raise ReferenceOriginalAccessError(503, '原文访问凭据未配置，请联系管理员') from None
    except ProviderFileTooLargeError:
        raise ReferenceOriginalAccessError(413, '原件超过在线预览上限，请联系管理员查阅') from None
    except ProviderNotFoundError:
        raise ReferenceOriginalAccessError(404, '知识库原件不存在，请联系管理员核对') from None
    except Exception:
        raise ReferenceOriginalAccessError(502, '知识库原文读取失败，请稍后重试或人工查阅原件') from None
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise ReferenceOriginalAccessError(502, '知识库未返回可用的原件，请人工查阅原件')
    return data
