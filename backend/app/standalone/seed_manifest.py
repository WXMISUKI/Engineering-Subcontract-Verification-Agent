"""校验离线知识种子清单；本模块不连接知识服务、不更改文件。"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re


class SeedManifestError(ValueError):
    """知识种子不可安全导入。"""


@dataclass(frozen=True)
class SeedDocument:
    path: Path
    sha256: str
    collection: str


def validate_seed_manifest(manifest_path: Path, seed_root: Path) -> list[SeedDocument]:
    root = seed_root.resolve(strict=True)
    if not root.is_dir():
        raise SeedManifestError('seed root must be a directory')
    try:
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SeedManifestError('cannot read seed manifest') from exc
    if not isinstance(payload, dict) or payload.get('version') != 1:
        raise SeedManifestError('unsupported manifest version')
    entries = payload.get('documents')
    if not isinstance(entries, list) or not entries:
        raise SeedManifestError('documents must be a nonempty list')
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SeedManifestError('invalid document entry')
        name, digest, collection = (entry.get(key) for key in ('path', 'sha256', 'collection'))
        if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
            raise SeedManifestError('invalid document path')
        relative = Path(name)
        if relative.is_absolute() or PureWindowsPath(name).drive or any(part in ('.', '..') for part in name.split('/')):
            raise SeedManifestError('document path must stay within seed root')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise SeedManifestError('invalid sha256')
        if not isinstance(collection, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', collection):
            raise SeedManifestError('invalid collection')
        try:
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise SeedManifestError('document path must stay within seed root')
            if path in seen:
                raise SeedManifestError('duplicate document')
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError as exc:
            raise SeedManifestError('cannot read document') from exc
        if actual != digest:
            raise SeedManifestError('sha256 mismatch')
        seen.add(path)
        result.append(SeedDocument(path=path, sha256=digest, collection=collection))
    return result
