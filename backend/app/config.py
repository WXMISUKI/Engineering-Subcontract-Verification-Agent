"""分包模块需要的独立配置；不读取原广场配置文件。"""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    SECRET_KEY: str
    BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB: int
    BS_WEKNORA_KB_MAP: str


def get_settings() -> Settings:
    secret = os.environ.get('SECRET_KEY', '')
    if len(secret.encode('utf-8')) < 32:
        raise RuntimeError('SECRET_KEY 必须至少 32 字节')
    try:
        limit = int(os.environ.get('BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB', '200'))
    except ValueError as exc:
        raise RuntimeError('上传大小限制必须是正整数') from exc
    if limit <= 0:
        raise RuntimeError('上传大小限制必须是正整数')
    return Settings(secret, limit, os.environ.get('BS_WEKNORA_KB_MAP', ''))
