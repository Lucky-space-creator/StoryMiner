"""
对称加密工具（Fernet）

整体思路：
    统一 API Key / 凭据的加密存储与读取，供 M9 模型密钥、后续 M11 MCP 鉴权复用，
    杜绝敏感信息明文落库。

关键点：
    1. 使用 cryptography.Fernet 对称加密；密钥来自 config.ENCRYPTION_KEY。
    2. encrypt/decrypt 为纯函数；空值透传（None/空串不加密）。
    3. 解密失败吞异常返回空串，避免历史脏数据导致接口 500。

实现逻辑：
    模块加载时构建单例 Fernet；对外暴露 encrypt/decrypt。
"""
from cryptography.fernet import Fernet, InvalidToken

from config import ENCRYPTION_KEY

# 单例 Fernet（进程内复用，避免重复构建）
_fernet = Fernet(ENCRYPTION_KEY)


def encrypt(plain: str | None) -> str | None:
    """加密明文；空值透传。"""
    if not plain:
        return plain
    return _fernet.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt(token: str | None) -> str:
    """解密密文；空值或失败返回空串。"""
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""
