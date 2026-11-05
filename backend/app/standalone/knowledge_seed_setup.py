"""对已准备好的新知识服务导入种子，完成前不发布参考映射。"""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import fitz

from .reference_mapping import apply_reference_mapping
from .seed_manifest import validate_seed_manifest
from .setup_journal import SetupJournal, SetupJournalError, setup_lock


def validate_seed_inputs(manifest: Path, seed_root: Path, reference_package: dict):
    documents = validate_seed_manifest(manifest, seed_root)
    hashes = [doc.sha256 for doc in documents]
    if len(set(hashes)) != len(hashes):
        raise SetupJournalError('同一原件摘要不能重复导入')
    sources = reference_package.get('sources') if isinstance(reference_package, dict) else None
    if not isinstance(sources, list) or any(not isinstance(s, dict) or s.get('original_sha256') not in hashes for s in sources):
        raise SetupJournalError('参考依据存在种子缺口')
    return documents


def seed_needs_vision(documents) -> bool:
    for doc in documents:
        if doc.path.suffix.lower() != '.pdf':
            continue
        try:
            with fitz.open(doc.path) as pdf:
                if any(len(page.get_text('text').strip()) < 20 for page in pdf):
                    return True
        except Exception:
            raise SetupJournalError('知识种子 PDF 无法检查') from None
    return False


def initialize_seed(admin, model_id: str, manifest: Path, seed_root: Path,
                    mapping_path: Path, reference_package: dict, *, timeout: float = 300,
                    poll_interval: float = 2, vision_model_id: str | None = None) -> None:
    if not model_id or timeout <= 0 or poll_interval <= 0:
        raise SetupJournalError('初始化模型或等待时间无效')
    documents = validate_seed_inputs(manifest, seed_root, reference_package)
    if seed_needs_vision(documents) and not vision_model_id:
        raise SetupJournalError('知识种子包含扫描页，必须配置 OCR 视觉模型')
    config = {'model_id': model_id, 'vision_model_id': vision_model_id or '',
              'documents': sorted((doc.sha256, doc.collection) for doc in documents)}
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with setup_lock(mapping_path.parent / 'setup.lock'):
        journal = SetupJournal(mapping_path.parent / 'setup-journal.json')
        if any(not isinstance(entry, dict) or entry.get('status') != 'done'
               for entry in journal.data['operations'].values()):
            raise SetupJournalError('存在结果未确认的操作，请先人工核对知识服务')
        existing = journal.begin('configuration')
        if existing is None:
            journal.finish('configuration', fingerprint)
        elif existing != fingerprint:
            raise SetupJournalError('种子清单或模型已变化，请规划新的初始化')
        kb_ids = {}
        for collection in sorted({doc.collection for doc in documents}):
            key = 'kb:' + collection
            kb_id = journal.begin(key)
            if kb_id is None:
                kb_id = admin.create_knowledge_base('bs_' + collection, model_id, vision_model_id)
                journal.finish(key, kb_id)
            kb_ids[collection] = kb_id
        mappings = []
        for doc in documents:
            key = 'document:' + doc.sha256
            knowledge_id = journal.begin(key)
            if knowledge_id is None:
                knowledge_id = admin.upload_document(kb_ids[doc.collection], doc.path,
                                                      enable_multimodal=bool(vision_model_id))
                journal.finish(key, knowledge_id)
            deadline = time.monotonic() + timeout
            while True:
                status = admin.document_status(knowledge_id)
                if status == 'completed':
                    break
                if status in ('failed', 'cancelled', 'deleting'):
                    raise SetupJournalError('知识文件解析未成功，需核对后处理')
                if time.monotonic() >= deadline:
                    raise SetupJournalError('知识文件解析尚未完成，可稍后重试')
                time.sleep(poll_interval)
            mappings.append({'sha256': doc.sha256, 'kb_id': kb_ids[doc.collection],
                             'knowledge_id': knowledge_id, 'collection': doc.collection})
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=mapping_path.parent,
                                             prefix='.mapping-', delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump({'version': 1, 'documents': mappings}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            apply_reference_mapping(reference_package, temp_path)
            os.replace(temp_path, mapping_path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
