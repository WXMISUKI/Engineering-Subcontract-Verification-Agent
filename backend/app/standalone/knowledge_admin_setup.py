"""独立知识服务首次配置，凭据仅传给客户端，不写入初始化日志。"""

import hashlib
import json
import os
from pathlib import Path

from .setup_journal import SetupJournal, SetupJournalError, setup_lock


def _ready_journal(config_dir: Path) -> SetupJournal:
    journal = SetupJournal(config_dir / 'setup-journal.json')
    if any(not isinstance(row, dict) or row.get('status') != 'done'
           for row in journal.data['operations'].values()):
        raise SetupJournalError('存在结果未确认的操作，请先人工核对知识服务')
    return journal


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


def register_admin(admin, config_dir: Path, username: str, email: str, password: str) -> None:
    if not all((username.strip(), email.strip(), password.strip())):
        raise SetupJournalError('独立知识管理员配置不完整')
    identity = _fingerprint({'username': username, 'email': email})
    with setup_lock(config_dir / 'setup.lock'):
        journal = _ready_journal(config_dir)
        existing = journal.begin('administrator')
        if existing is not None and existing != identity:
            raise SetupJournalError('知识管理员身份与已有初始化记录不一致')
        if existing is None:
            admin.register(username, email, password)
            admin.login(email, password)
            journal.finish('administrator', identity)
        else:
            admin.login(email, password)


def prepare_embedding(admin, config_dir: Path, name: str, base_url: str,
                      api_key: str, dimension: int) -> str:
    if (not name.strip() or not base_url.strip() or not api_key.strip()
            or isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0):
        raise SetupJournalError('Embedding 配置不完整')
    fingerprint = _fingerprint({'name': name, 'base_url': base_url, 'dimension': dimension})
    with setup_lock(config_dir / 'setup.lock'):
        journal = _ready_journal(config_dir)
        existing = journal.begin('embedding-configuration')
        if existing is None:
            journal.finish('embedding-configuration', fingerprint)
        elif existing != fingerprint:
            raise SetupJournalError('Embedding 模型配置已变化，请规划新的初始化')
        model_id = journal.begin('embedding-model')
        if model_id is None:
            model_id = admin.create_embedding_model(name, base_url, api_key, dimension)
            journal.finish('embedding-model', model_id)
        return model_id


def prepare_vision(admin, config_dir: Path, name: str, base_url: str, api_key: str) -> str:
    if not name.strip() or not base_url.strip() or not api_key.strip():
        raise SetupJournalError('OCR 视觉模型配置不完整')
    fingerprint = _fingerprint({'name': name, 'base_url': base_url})
    with setup_lock(config_dir / 'setup.lock'):
        journal = _ready_journal(config_dir)
        existing = journal.begin('vision-configuration')
        if existing is None:
            journal.finish('vision-configuration', fingerprint)
        elif existing != fingerprint:
            raise SetupJournalError('OCR 视觉模型配置已变化，请规划新的初始化')
        model_id = journal.begin('vision-model')
        if model_id is None:
            model_id = admin.create_vision_model(name, base_url, api_key)
            journal.finish('vision-model', model_id)
        return model_id


def prepare_query_key(admin, config_dir: Path, kb_ids: list[str]) -> None:
    if not kb_ids or any(not isinstance(kb, str) or not kb.strip() for kb in kb_ids):
        raise SetupJournalError('知识检索范围无效')
    fingerprint = _fingerprint(sorted(set(kb_ids)))
    path = config_dir / 'query-key'
    with setup_lock(config_dir / 'setup.lock'):
        journal = _ready_journal(config_dir)
        existing = journal.begin('query-key-scope')
        if existing is None:
            journal.finish('query-key-scope', fingerprint)
        elif existing != fingerprint:
            raise SetupJournalError('知识检索范围已变化，请重新规划授权')
        key_id = journal.begin('query-key')
        if key_id is None:
            if path.exists():
                raise SetupJournalError('凭据文件已存在，需先人工核对')
            key_id, secret = admin.create_retrieval_key(sorted(set(kb_ids)))
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(secret)
                stream.flush()
                os.fsync(stream.fileno())
            journal.finish('query-key', key_id)
        elif not path.is_file() or not path.read_text(encoding='utf-8').strip():
            raise SetupJournalError('知识检索凭据文件缺失，请先人工核对')
