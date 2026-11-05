"""独立应用只使用部署方配置的模型。"""
import os


def effective_model_code_or_default(db, scope_type, scope_id):
    code = os.environ.get('LLM_MODEL', '').strip()
    if not code:
        raise RuntimeError('模型尚未配置')
    return code
