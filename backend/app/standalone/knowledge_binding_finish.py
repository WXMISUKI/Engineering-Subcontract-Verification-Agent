"""只有凭据和受限数据库绑定齐备时才发布可供业务使用的映射。"""

import os
from pathlib import Path

from sqlalchemy import inspect

from .knowledge_admin_setup import prepare_query_key
from .knowledge_binding_setup import bind_seed_knowledge, validated_bindings, BindingSetupError
from .setup_journal import setup_lock


def finish_seed_setup(engine, admin, documents, staged: Path, final: Path) -> None:
    if staged.parent.resolve() != final.parent.resolve():
        raise BindingSetupError('知识映射必须在同一配置目录内发布')
    targets = validated_bindings(documents, staged)
    inspector = inspect(engine)
    if not all(inspector.has_table(name) for name in ('agents', 'knowledge_providers', 'knowledge_base_bindings')):
        raise BindingSetupError('独立数据库尚未初始化')
    prepare_query_key(admin, final.parent, [kb_id for kb_id, _ in targets.values()])
    with setup_lock(final.parent / 'setup.lock'):
        bind_seed_knowledge(engine, documents, staged)
        os.replace(staged, final)
