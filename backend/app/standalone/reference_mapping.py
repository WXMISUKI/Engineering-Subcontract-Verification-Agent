"""按原件摘要应用新环境知识标识；缺失或冲突时拒绝沿用旧标识。"""
from copy import deepcopy
import json
from pathlib import Path
import re


class MappingError(ValueError):
    def __init__(self, message: str, *, code: str = 'mapping_invalid', context: dict | None = None):
        super().__init__(message)
        self.code = code
        self.context = dict(context or {})


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
        raise MappingError('知识标识映射不可用', code='mapping_file_unavailable') from exc
    if not isinstance(mapping, dict) or mapping.get('version') != 1 or not isinstance(mapping.get('documents'), list):
        raise MappingError('知识标识映射格式无效', code='mapping_file_invalid')
    by_hash = {}
    for entry_index, entry in enumerate(mapping['documents']):
        if not isinstance(entry, dict):
            raise MappingError('知识标识映射项无效', code='mapping_entry_invalid',
                               context={'entry_index': entry_index})
        digest = entry.get('sha256')
        kb_id, knowledge_id = entry.get('kb_id'), entry.get('knowledge_id')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise MappingError('知识标识映射缺失或重复', code='mapping_entry_invalid',
                               context={'entry_index': entry_index, 'field': 'sha256'})
        if not isinstance(kb_id, str) or not kb_id.strip() or not isinstance(knowledge_id, str) or not knowledge_id.strip():
            field = 'kb_id' if not isinstance(kb_id, str) or not kb_id.strip() else 'knowledge_id'
            raise MappingError('知识标识映射缺失或重复', code='mapping_entry_invalid',
                               context={'entry_index': entry_index, 'field': field})
        if digest in by_hash:
            raise MappingError('知识标识映射缺失或重复', code='mapping_hash_duplicate',
                               context={'entry_index': entry_index, 'source_hash_prefix': digest[:12]})
        by_hash[digest] = (kb_id, knowledge_id)
    result = deepcopy(package)
    if not isinstance(result.get('sources'), list):
        raise MappingError('依据清单格式无效', code='reference_package_invalid')
    for source_index, source in enumerate(result['sources']):
        if not isinstance(source, dict):
            raise MappingError('依据原件缺少新环境映射', code='reference_source_invalid',
                               context={'source_index': source_index})
        digest = source.get('original_sha256')
        item_id = source.get('item_id')
        safe_item_id = item_id[:64] if isinstance(item_id, str) else None
        hash_prefix = digest[:12] if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) else None
        source_context = {'source_index': source_index, 'item_id': safe_item_id,
                          'source_hash_prefix': hash_prefix}
        if digest not in by_hash:
            raise MappingError('依据原件缺少新环境映射', code='source_mapping_missing',
                               context=source_context)
        kb_id, knowledge_id = by_hash[digest]
        if (kb_id, knowledge_id) == (source.get('kb_id'), source.get('knowledge_id')):
            raise MappingError('不得沿用旧环境标识', code='legacy_identifier_reused',
                               context=source_context)
        source['kb_id'], source['knowledge_id'] = kb_id, knowledge_id
    return result
