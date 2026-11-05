"""按原件摘要应用新环境知识标识；缺失或冲突时拒绝沿用旧标识。"""
from copy import deepcopy
import json
from pathlib import Path
import re


class MappingError(ValueError):
    pass


def load_collection_map(mapping_path: Path) -> dict[str, str]:
    try:
        mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MappingError('知识集合映射不可用') from None
    if not isinstance(mapping, dict) or mapping.get('version') != 1 or not isinstance(mapping.get('documents'), list):
        raise MappingError('知识集合映射格式无效')
    result, seen, kb_collections = {}, set(), {}
    for entry in mapping['documents']:
        if not isinstance(entry, dict):
            raise MappingError('知识集合映射项无效')
        digest, collection, kb = (entry.get(k) for k in ('sha256', 'collection', 'kb_id'))
        knowledge_id = entry.get('knowledge_id')
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or digest in seen
                or not isinstance(collection, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', collection)
                or not isinstance(kb, str) or not kb.strip()
                or not isinstance(knowledge_id, str) or not knowledge_id.strip()):
            raise MappingError('知识集合映射缺失或重复')
        if (collection in result and result[collection] != kb) or (kb in kb_collections and kb_collections[kb] != collection):
            raise MappingError('知识集合和目标库关系不一致')
        seen.add(digest)
        result[collection], kb_collections[kb] = kb, collection
    if not result:
        raise MappingError('知识集合映射为空')
    return result


def apply_reference_mapping(package: dict, mapping_path: Path) -> dict:
    try:
        mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MappingError('知识标识映射不可用') from exc
    if not isinstance(mapping, dict) or mapping.get('version') != 1 or not isinstance(mapping.get('documents'), list):
        raise MappingError('知识标识映射格式无效')
    by_hash = {}
    for entry in mapping['documents']:
        if not isinstance(entry, dict):
            raise MappingError('知识标识映射项无效')
        digest = entry.get('sha256')
        kb_id, knowledge_id = entry.get('kb_id'), entry.get('knowledge_id')
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or not isinstance(kb_id, str) or not kb_id.strip()
                or not isinstance(knowledge_id, str) or not knowledge_id.strip() or digest in by_hash):
            raise MappingError('知识标识映射缺失或重复')
        by_hash[digest] = (kb_id, knowledge_id)
    result = deepcopy(package)
    if not isinstance(result.get('sources'), list):
        raise MappingError('依据清单格式无效')
    for source in result['sources']:
        if not isinstance(source, dict) or source.get('original_sha256') not in by_hash:
            raise MappingError('依据原件缺少新环境映射')
        kb_id, knowledge_id = by_hash[source['original_sha256']]
        if (kb_id, knowledge_id) == (source.get('kb_id'), source.get('knowledge_id')):
            raise MappingError('不得沿用旧环境标识')
        source['kb_id'], source['knowledge_id'] = kb_id, knowledge_id
    return result
