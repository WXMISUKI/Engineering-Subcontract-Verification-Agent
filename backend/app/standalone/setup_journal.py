"""知识初始化的本地操作日志，防止不确定结果被盲目重复执行。"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile


class SetupJournalError(RuntimeError):
    pass


@contextmanager
def setup_lock(path: Path):
    """互斥执行初始化；异常退出遗留的锁需要管理员核实后处理。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SetupJournalError('知识初始化锁已存在，请核实是否有其他初始化正在运行') from None
    original = os.fstat(fd)
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        try:
            current = path.stat()
            if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass


class SetupJournal:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise SetupJournalError('初始化日志无法读取') from None
            if not isinstance(self.data, dict) or self.data.get('version') != 1 or not isinstance(self.data.get('operations'), dict):
                raise SetupJournalError('初始化日志格式无效')
        else:
            self.data = {'version': 1, 'operations': {}}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.setup-', delete=False) as stream:
                temp_name = stream.name
                json.dump(self.data, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def begin(self, operation: str) -> str | None:
        entry = self.data['operations'].get(operation)
        if entry is not None:
            if not isinstance(entry, dict) or entry.get('status') != 'done' or not entry.get('id'):
                raise SetupJournalError('存在结果未确认的操作，请先人工核对知识服务')
            return entry['id']
        self.data['operations'][operation] = {'status': 'pending'}
        self._save()
        return None

    def finish(self, operation: str, external_id: str) -> None:
        if (self.data['operations'].get(operation) != {'status': 'pending'}
                or not isinstance(external_id, str) or not external_id.strip()):
            raise SetupJournalError('初始化操作或标识无效')
        self.data['operations'][operation] = {'status': 'done', 'id': external_id}
        self._save()
