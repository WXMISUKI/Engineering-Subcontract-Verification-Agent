"""单实例运行保护；锁文件必须位于所有后端共用的上传卷。"""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_instance(directory):
    # 独立 Docker 运行于 Linux；不支持绕过锁在其他平台运行生产入口。
    import fcntl

    path = Path(directory) / '.bs-backend.lock'
    with path.open('a') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一个分包后端正在使用上传卷，禁止启动第二个实例') from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
