"""仅将已核对的独立种子绑定到分包智能体，不覆盖现有配置。"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.models import KnowledgeBaseBinding, KnowledgeProvider
from ..plus.blockchain_supervision.manifest import manifest
from .identity import Agent


class BindingSetupError(ValueError):
    pass


def validated_bindings(documents, mapping_path: Path) -> dict[str, tuple[str, list[str]]]:
    try:
        mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BindingSetupError('知识映射不可读取') from None
    if not isinstance(mapping, dict) or mapping.get('version') != 1 or not isinstance(mapping.get('documents'), list):
        raise BindingSetupError('知识映射格式无效')
    expected = {doc.sha256: doc.collection for doc in documents}
    if len(expected) != len(documents):
        raise BindingSetupError('种子摘要重复')
    seen, file_ids, kb_collections, result = set(), set(), {}, {}
    for row in mapping['documents']:
        if not isinstance(row, dict):
            raise BindingSetupError('知识映射项无效')
        digest, collection, kb, file_id = (row.get(k) for k in ('sha256', 'collection', 'kb_id', 'knowledge_id'))
        if (not isinstance(digest, str) or not isinstance(collection, str)
                or digest not in expected or digest in seen or expected[digest] != collection
                or not isinstance(kb, str) or not kb.strip() or len(kb) > 100
                or not isinstance(file_id, str) or not file_id.strip() or len(file_id) > 100
                or file_id in file_ids):
            raise BindingSetupError('知识映射与种子不一致或重复')
        if kb in kb_collections and kb_collections[kb] != collection:
            raise BindingSetupError('不同集合不能共用目标库')
        if collection in result and result[collection][0] != kb:
            raise BindingSetupError('同一集合出现多个目标库')
        seen.add(digest); file_ids.add(file_id); kb_collections[kb] = collection
        if collection not in result:
            result[collection] = (kb, [])
        result[collection][1].append(file_id)
    if seen != set(expected):
        raise BindingSetupError('知识映射缺少种子文件')
    return result


def bind_seed_knowledge(engine, documents, mapping_path: Path) -> None:
    targets = validated_bindings(documents, mapping_path)
    with Session(engine) as db, db.begin():
        agent = db.scalar(select(Agent).where(Agent.slug == manifest['code']))
        if agent is None:
            agent = Agent(slug=manifest['code'])
            db.add(agent); db.flush()
        provider = db.scalar(select(KnowledgeProvider).where(KnowledgeProvider.name == 'standalone-weknora'))
        if provider is None:
            provider = KnowledgeProvider(name='standalone-weknora', provider_type='weknora',
                                         base_url='http://weknora:8080/api/v1',
                                         secret_ref='env://WEKNORA_API_KEY', enabled=True)
            db.add(provider); db.flush()
        if (provider.provider_type != 'weknora' or provider.base_url != 'http://weknora:8080/api/v1'
                or provider.secret_ref != 'env://WEKNORA_API_KEY' or not provider.enabled):
            raise BindingSetupError('已有知识服务配置与独立版不一致')
        existing = db.scalars(select(KnowledgeBaseBinding).where(
            KnowledgeBaseBinding.agent_id == agent.id)).all()
        allowed_ids = {kb for kb, _ in targets.values()}
        if any(row.provider_id != provider.id or row.external_kb_id not in allowed_ids for row in existing):
            raise BindingSetupError('已有分包绑定超出种子范围')
        by_kb = {row.external_kb_id: row for row in existing}
        for collection, (kb_id, files) in targets.items():
            row = by_kb.get(kb_id)
            if row is None:
                db.add(KnowledgeBaseBinding(agent_id=agent.id, provider_id=provider.id,
                    external_kb_id=kb_id, display_name='bs_' + collection,
                    selected_knowledge_ids=json.dumps(sorted(files)), is_enabled=True, allow_query=True,
                    allow_ingest=False, allow_manage=False, priority=100, retrieval_weight=1.0))
            else:
                try:
                    selected = json.loads(row.selected_knowledge_ids or 'null')
                except (ValueError, TypeError):
                    selected = None
                if (not isinstance(selected, list) or any(not isinstance(item, str) for item in selected)
                        or sorted(selected) != sorted(files) or not row.is_enabled
                        or not row.allow_query or row.allow_ingest or row.allow_manage):
                    raise BindingSetupError('已有分包绑定与种子范围不一致')
