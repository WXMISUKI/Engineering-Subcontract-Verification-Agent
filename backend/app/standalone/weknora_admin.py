"""新环境知识初始化所需的 WeKnora v0.9 管理 API；不记录凭据或响应正文。"""

import httpx
import json
from pathlib import Path
from urllib.parse import quote


class KnowledgeSetupError(RuntimeError):
    pass


class WeKnoraAdmin:
    def __init__(self, client: httpx.Client):
        self.client = client

    @staticmethod
    def _body(response: httpx.Response, expected: tuple[int, ...]) -> dict:
        if response.status_code not in expected:
            raise KnowledgeSetupError(f'知识服务请求失败（HTTP {response.status_code}）')
        try:
            body = response.json()
        except ValueError:
            raise KnowledgeSetupError('知识服务返回了无效响应') from None
        if not isinstance(body, dict) or body.get('success') is not True:
            raise KnowledgeSetupError('知识服务未确认操作成功')
        return body

    def login(self, email: str, password: str) -> None:
        body = self._body(self.client.post('/api/v1/auth/login', json={'email': email, 'password': password}), (200,))
        token = body.get('token')
        if not isinstance(token, str) or not token:
            raise KnowledgeSetupError('知识服务登录响应缺少令牌')
        self.client.headers['Authorization'] = 'Bearer ' + token

    def register(self, username: str, email: str, password: str) -> None:
        self._body(self.client.post('/api/v1/auth/register', json={
            'username': username, 'email': email, 'password': password,
        }), (201,))

    def create_knowledge_base(self, name: str, embedding_model_id: str, vision_model_id: str | None = None) -> str:
        payload = {
            'name': name, 'type': 'document', 'embedding_model_id': embedding_model_id,
        }
        if vision_model_id:
            payload['vlm_config'] = {'enabled': True, 'model_id': vision_model_id}
        body = self._body(self.client.post('/api/v1/knowledge-bases', json=payload), (201,))
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise KnowledgeSetupError('知识库创建响应缺少标识')
        return data['id']

    def create_embedding_model(self, name: str, base_url: str, api_key: str, dimension: int) -> str:
        if not name or not base_url or not api_key or not isinstance(dimension, int) or dimension <= 0:
            raise KnowledgeSetupError('Embedding 配置不完整')
        body = self._body(self.client.post('/api/v1/models', json={
            'name': name, 'type': 'Embedding', 'source': 'remote',
            'parameters': {'provider': 'generic', 'base_url': base_url, 'api_key': api_key,
                           'embedding_parameters': {'dimension': dimension}},
        }), (201,))
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise KnowledgeSetupError('Embedding 创建响应缺少标识')
        return data['id']

    def create_vision_model(self, name: str, base_url: str, api_key: str) -> str:
        if not name or not base_url or not api_key:
            raise KnowledgeSetupError('OCR 视觉模型配置不完整')
        body = self._body(self.client.post('/api/v1/models', json={
            'name': name, 'type': 'VLLM', 'source': 'remote',
            'parameters': {'provider': 'generic', 'base_url': base_url, 'api_key': api_key},
        }), (201,))
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise KnowledgeSetupError('OCR 视觉模型创建响应缺少标识')
        return data['id']

    def upload_document(self, kb_id: str, path: Path, enable_multimodal: bool = False) -> str:
        with path.open('rb') as stream:
            response = self.client.post(
                f'/api/v1/knowledge-bases/{quote(kb_id, safe="")}/knowledge/file',
                files={'file': (path.name, stream)},
                data={'process_config': json.dumps({'summary_enabled': False}),
                      'enable_multimodel': str(enable_multimodal).lower()},
            )
        # 409 必须由调用方核对原件后处理，不能当成同一文件直接复用。
        body = self._body(response, (200,))
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise KnowledgeSetupError('知识文件上传响应缺少标识')
        return data['id']

    def document_status(self, knowledge_id: str) -> str:
        body = self._body(self.client.get(f'/api/v1/knowledge/{quote(knowledge_id, safe="")}'), (200,))
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('parse_status'), str):
            raise KnowledgeSetupError('知识文件状态响应无效')
        return data['parse_status']

    def create_retrieval_key(self, kb_ids: list[str]) -> tuple[str, str]:
        if not kb_ids or any(not isinstance(item, str) or not item.strip() for item in kb_ids):
            raise KnowledgeSetupError('知识检索范围无效')
        body = self._body(self.client.get('/api/v1/auth/me'), (200,))
        data = body.get('data')
        tenant = data.get('tenant') if isinstance(data, dict) else None
        tenant_id = tenant.get('id') if isinstance(tenant, dict) else None
        if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
            raise KnowledgeSetupError('知识服务管理空间无效')
        body = self._body(self.client.post(f'/api/v1/tenants/{tenant_id}/api-keys', json={
            'name': 'bs-query', 'capabilities': ['retrieve'],
            'knowledge_base_ids': sorted(set(kb_ids)), 'full_access': False,
        }), (201,))
        data = body.get('data')
        key_id = data.get('id') if isinstance(data, dict) else None
        secret = data.get('token') if isinstance(data, dict) else None
        if isinstance(key_id, bool) or not isinstance(key_id, int) or key_id <= 0 or not isinstance(secret, str) or not secret.strip():
            raise KnowledgeSetupError('知识检索凭据响应无效')
        return str(key_id), secret
