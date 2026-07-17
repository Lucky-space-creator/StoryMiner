"""
MinIO 对象存储后端

整体思路：
    实现与本地存储一致的 save/read/delete/exists/ensure_storage 接口，使调用方无感知切换；
    object_key 形如 "{owner_id}/{novel_id}/{sha256}{ext}"，按用户/小说隔离。

关键点：
    1. MinIO SDK 为同步阻塞调用，统一用 asyncio.to_thread 包裹，避免卡住事件循环。
    2. ensure_storage 在应用启动时建桶（幂等），桶缺失则自动创建。
    3. 去重仍用内容 sha256，与本地后端逻辑一致。

实现逻辑：
    模块加载时构造 Minio 客户端；save 计算摘要并 put_object；read 经 get_object 流式读全；
    exists 用 stat_object 探测；delete 用 remove_object。
"""
import asyncio
import hashlib
import io
import os

from minio import Minio

from config import (
    MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
    MINIO_BUCKET, MINIO_SECURE,
)


_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)


def sha256(data: bytes) -> str:
    """计算内容 sha256 十六进制摘要（去重用）。"""
    return hashlib.sha256(data).hexdigest()


def _object_key(owner_id: int, novel_id: int, filename: str, digest: str) -> str:
    """生成对象键：owner/novel/sha256.ext。"""
    ext = os.path.splitext(filename)[1].lower()
    return f"{owner_id}/{novel_id}/{digest}{ext}"


def ensure_bucket_sync() -> None:
    """建桶（幂等）：不存在则创建。"""
    if not _client.bucket_exists(MINIO_BUCKET):
        _client.make_bucket(MINIO_BUCKET)


async def ensure_storage() -> None:
    """异步封装：确保桶存在。"""
    await asyncio.to_thread(ensure_bucket_sync)


def save_sync(owner_id: int, novel_id: int, filename: str, data: bytes) -> tuple[str, str]:
    """同步保存：返回 (object_key, 内容哈希)。"""
    digest = sha256(data)
    key = _object_key(owner_id, novel_id, filename, digest)
    _client.put_object(MINIO_BUCKET, key, io.BytesIO(data), length=len(data))
    return key, digest


async def save(owner_id: int, novel_id: int, filename: str, data: bytes) -> tuple[str, str]:
    """异步保存对象。"""
    return await asyncio.to_thread(save_sync, owner_id, novel_id, filename, data)


def read_sync(key: str) -> bytes:
    """同步读取：返回对象全部字节。"""
    resp = _client.get_object(MINIO_BUCKET, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


async def read(key: str) -> bytes:
    """异步读取对象。"""
    return await asyncio.to_thread(read_sync, key)


def delete_sync(key: str) -> None:
    """同步删除对象。"""
    _client.remove_object(MINIO_BUCKET, key)


async def delete(key: str) -> None:
    """异步删除对象。"""
    await asyncio.to_thread(delete_sync, key)


def exists_sync(key: str) -> bool:
    """同步探测对象是否存在。"""
    try:
        _client.stat_object(MINIO_BUCKET, key)
        return True
    except Exception:
        return False


async def exists(key: str) -> bool:
    """异步探测对象是否存在。"""
    return await asyncio.to_thread(exists_sync, key)
