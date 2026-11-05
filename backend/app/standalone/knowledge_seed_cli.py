"""导入已配置模型的独立知识服务；只能连接 Compose 内部 WeKnora。"""

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

import httpx
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from .knowledge_seed_setup import initialize_seed, validate_seed_inputs, seed_needs_vision
from .knowledge_admin_setup import _fingerprint, prepare_embedding, prepare_vision, register_admin
from .knowledge_binding_finish import finish_seed_setup
from .knowledge_upload_recovery import recover_pending_upload
from .setup_journal import SetupJournal, SetupJournalError
from .weknora_admin import WeKnoraAdmin


def read_environment(values: Mapping[str, str]) -> tuple[str, str, str]:
    names = ('WEKNORA_ADMIN_EMAIL', 'WEKNORA_ADMIN_PASSWORD', 'WEKNORA_EMBEDDING_MODEL_ID')
    result = []
    for name in names:
        value = values.get(name, '')
        if not value.strip():
            raise ValueError(f'缺少环境变量 {name}')
        result.append(value if name == 'WEKNORA_ADMIN_PASSWORD' else value.strip())
    return tuple(result)


def read_bootstrap_environment(values: Mapping[str, str]) -> tuple[str, str, str, str, str, str, int]:
    names = ('WEKNORA_ADMIN_USERNAME', 'WEKNORA_ADMIN_EMAIL', 'WEKNORA_ADMIN_PASSWORD',
             'EMBEDDING_MODEL', 'EMBEDDING_API_BASE', 'EMBEDDING_API_KEY', 'EMBEDDING_DIMENSION')
    result = []
    for name in names:
        value = values.get(name, '')
        if not value.strip():
            raise ValueError(f'缺少环境变量 {name}')
        result.append(value if name in ('WEKNORA_ADMIN_PASSWORD', 'EMBEDDING_API_KEY') else value.strip())
    try:
        dimension = int(result[-1])
    except ValueError:
        raise ValueError('EMBEDDING_DIMENSION 必须为正整数') from None
    if dimension <= 0:
        raise ValueError('EMBEDDING_DIMENSION 必须为正整数')
    return (*result[:-1], dimension)


def validate_database_url(value: str) -> str:
    try:
        url = make_url(value)
    except Exception:
        raise ValueError('独立数据库配置无效') from None
    if url.get_backend_name() != 'mysql' or url.host != 'mysql' or url.database != 'bs_review':
        raise ValueError('知识初始化只允许连接独立 Compose 的 bs_review 数据库')
    return value


def read_vision_environment(values: Mapping[str, str]) -> tuple[str, str, str]:
    result = []
    for name in ('KNOWLEDGE_VLM_MODEL', 'KNOWLEDGE_VLM_API_BASE', 'KNOWLEDGE_VLM_API_KEY'):
        value = values.get(name, '')
        if not value.strip():
            raise ValueError(f'缺少环境变量 {name}')
        result.append(value if name == 'KNOWLEDGE_VLM_API_KEY' else value.strip())
    return tuple(result)


def recover_bootstrap_upload(admin, config_dir, manifest, seed_root, package, *, username, email,
                             password, model, base_url, dimension, vision=None):
    """在原 bootstrap 写入任何远端对象前恢复唯一可验证的文件上传。"""
    journal = SetupJournal(config_dir / 'setup-journal.json')
    operations = journal.data['operations']
    pending = [(key, value) for key, value in operations.items()
               if not isinstance(value, dict) or value.get('status') != 'done']
    if not pending:
        return False
    if len(pending) != 1 or not pending[0][0].startswith('document:') or pending[0][1] != {'status': 'pending'}:
        raise SetupJournalError('存在无法自动核对的初始化操作')
    expected = {
        'administrator': {'status': 'done', 'id': _fingerprint({'username': username, 'email': email})},
        'embedding-configuration': {'status': 'done', 'id': _fingerprint(
            {'name': model, 'base_url': base_url, 'dimension': dimension})},
    }
    model_entry = operations.get('embedding-model')
    if not isinstance(model_entry, dict) or model_entry.get('status') != 'done' or not model_entry.get('id'):
        raise SetupJournalError('Embedding 模型身份未确认')
    expected['embedding-model'] = model_entry.copy()
    vision_id = None
    if vision:
        vision_name, vision_base = vision
        expected['vision-configuration'] = {'status': 'done', 'id': _fingerprint(
            {'name': vision_name, 'base_url': vision_base})}
        vision_entry = operations.get('vision-model')
        if not isinstance(vision_entry, dict) or vision_entry.get('status') != 'done' or not vision_entry.get('id'):
            raise SetupJournalError('OCR 模型身份未确认')
        expected['vision-model'] = vision_entry.copy()
        vision_id = vision_entry['id']
    if any(operations.get(key) != value for key, value in expected.items()):
        raise SetupJournalError('初始化身份或模型配置已变化')
    admin.login(email, password)
    return recover_pending_upload(admin, model_entry['id'], manifest, seed_root, config_dir, package,
                                  vision_model_id=vision_id, expected_operations=expected)


def main() -> None:
    parser = argparse.ArgumentParser(description='独立分包知识种子初始化')
    parser.add_argument('--bootstrap', action='store_true', help='创建新知识服务管理员和 Embedding 模型')
    args = parser.parse_args()
    if args.bootstrap:
        username, email, password, model, base_url, api_key, dimension = read_bootstrap_environment(os.environ)
    else:
        email, password, model_id = read_environment(os.environ)
    reference = Path(__file__).resolve().parents[1] / 'plus' / 'blockchain_supervision' / 'data' / 'reference_package.json'
    package = json.loads(reference.read_text(encoding='utf-8'))
    manifest, seed_root = Path('/app/knowledge/seed-manifest.json'), Path('/data/knowledge-source')
    documents = validate_seed_inputs(manifest, seed_root, package)
    needs_vision = seed_needs_vision(documents)
    vision_model_id = os.environ.get('WEKNORA_VLM_MODEL_ID', '').strip()
    if needs_vision and args.bootstrap:
        vision_model, vision_base, vision_key = read_vision_environment(os.environ)
    elif needs_vision and not vision_model_id:
        raise ValueError('扫描知识种子需要配置 WEKNORA_VLM_MODEL_ID')
    engine = create_engine(validate_database_url(os.environ.get('DATABASE_URL', '')), pool_pre_ping=True)
    inspector = inspect(engine)
    if not all(inspector.has_table(name) for name in ('agents', 'knowledge_providers', 'knowledge_base_bindings')):
        raise ValueError('请先初始化独立数据库')
    with httpx.Client(base_url='http://weknora:8080', timeout=60, trust_env=False) as client:
        admin = WeKnoraAdmin(client)
        if args.bootstrap:
            recover_bootstrap_upload(admin, Path('/data/knowledge'), manifest, seed_root, package,
                                     username=username, email=email, password=password, model=model,
                                     base_url=base_url, dimension=dimension,
                                     vision=(vision_model, vision_base) if needs_vision else None)
            register_admin(admin, Path('/data/knowledge'), username, email, password)
            model_id = prepare_embedding(admin, Path('/data/knowledge'), model, base_url, api_key, dimension)
            if needs_vision:
                vision_model_id = prepare_vision(admin, Path('/data/knowledge'), vision_model, vision_base, vision_key)
        else:
            admin.login(email, password)
        staged, final = Path('/data/knowledge/reference-mapping.staged.json'), Path('/data/knowledge/reference-mapping.json')
        initialize_seed(admin, model_id, manifest, seed_root, staged, package, vision_model_id=vision_model_id or None)
        finish_seed_setup(engine, admin, documents, staged, final)
    engine.dispose()
    print('知识种子、只读凭据和分包绑定已初始化；仍需验证五步召回。')


if __name__ == '__main__':
    main()
