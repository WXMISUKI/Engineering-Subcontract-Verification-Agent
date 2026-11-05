"""仅对身份可完整核对的未知资料上传恢复本地日志。"""
import hashlib
import json
from pathlib import Path

from .knowledge_seed_setup import validate_seed_inputs
from .setup_journal import SetupJournal, SetupJournalError, setup_lock


def recover_pending_upload(admin, model_id: str, manifest: Path, seed_root: Path,
                           config_dir: Path, reference_package: dict, *, vision_model_id=None,
                           expected_operations=None) -> bool:
    documents = validate_seed_inputs(manifest, seed_root, reference_package)
    config = {'model_id': model_id, 'vision_model_id': vision_model_id or '',
              'documents': sorted((doc.sha256, doc.collection) for doc in documents)}
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    with setup_lock(config_dir / 'setup.lock'):
        journal = SetupJournal(config_dir / 'setup-journal.json')
        operations = journal.data['operations']
        if expected_operations and any(operations.get(key) != value for key, value in expected_operations.items()):
            raise SetupJournalError('初始化身份或模型配置已变化')
        pending = [(key, value) for key, value in operations.items()
                   if not isinstance(value, dict) or value.get('status') != 'done']
        if not pending:
            return False
        if len(pending) != 1 or pending[0][1] != {'status': 'pending'}:
            raise SetupJournalError('存在无法自动核对的初始化操作')
        key = pending[0][0]
        matched = [doc for doc in documents if key == 'document:' + doc.sha256]
        if len(matched) != 1 or operations.get('configuration') != {'status': 'done', 'id': fingerprint}:
            raise SetupJournalError('初始化操作或配置不一致，无法自动恢复')
        doc = matched[0]
        kb_entry = operations.get('kb:' + doc.collection)
        if (not isinstance(kb_entry, dict) or kb_entry.get('status') != 'done'
                or not isinstance(kb_entry.get('id'), str) or not kb_entry['id'].strip()):
            raise SetupJournalError('目标知识库身份未确认')
        document_id = admin.find_verified_document(kb_entry['id'], doc.path, doc.sha256, model_id)
        if not isinstance(document_id, str) or not document_id.strip():
            raise SetupJournalError('远端资料无法唯一核实')
        journal.finish(key, document_id)
        return True
