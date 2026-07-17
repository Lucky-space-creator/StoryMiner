"""
密码安全

整体思路：
    使用 passlib 的 bcrypt 进行密码哈希与校验，避免自造轮子。

关键点：
    1. CryptContext 统一管理算法与兼容。
    2. hash_password/verify_password 为对外接口。

实现逻辑：
    直接委托 passlib CryptContext。
"""
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str) -> str:
    """对明文密码进行 bcrypt 哈希。"""
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    """校验明文与哈希是否匹配。"""
    return _pwd.verify(raw, hashed)
