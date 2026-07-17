"""
本地文件存储

整体思路：
    开发期将上传原文件落盘到本地目录；接口与 MinIO 后端完全一致（均为 async），
    由 storage 包按配置选择，调用方无感知切换。

关键点：
    1. save 按 owner/novel 分目录，文件名用内容哈希避免重复。
    2. sha256 供去重使用（M1.8）。
    3. 全部接口为 async，与 minio_storage 对齐，保证可切换性。

实现逻辑：
    直接调用 os/pathlib 写文件；本地 IO 较快，直接 async def 包裹同步操作。
"""
import hashlib
import os

from config import UPLOAD_DIR


def sha256(data: bytes) -> str:
    """计算内容 sha256 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest()


async def save(owner_id: int, novel_id: int, filename: str, data: bytes) -> tuple[str, str]:
    """保存文件，返回 (本地路径, 内容哈希)。"""
    ext = os.path.splitext(filename)[1].lower()
    digest = sha256(data)
    rel_dir = os.path.join(UPLOAD_DIR, str(owner_id), str(novel_id))
    os.makedirs(rel_dir, exist_ok=True)
    path = os.path.join(rel_dir, f"{digest}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path, digest


async def read(path: str) -> bytes:
    """读取已保存文件字节（M3 切片时回读原文）。"""
    with open(path, "rb") as f:
        return f.read()


async def delete(object_key: str) -> None:
    """删除已保存文件（去重清理用）。"""
    if os.path.exists(object_key):
        os.remove(object_key)


async def exists(object_key: str) -> bool:
    """判断对象是否存在。"""
    return os.path.exists(object_key)


async def ensure_storage() -> None:
    """本地后端：确保上传根目录存在。"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
