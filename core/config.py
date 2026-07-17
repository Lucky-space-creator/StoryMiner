"""
配置模块

整体思路：
    从同目录 config.yml 读取运行配置，并允许环境变量覆盖，集中管理数据库、JWT、上传、CORS 等参数。

关键点：
    1. 配置源优先级：环境变量 > config.yml > 内置默认值。
    2. 对外暴露的常量名（DB_DSN 等）保持不变，调用方无需改动。
    3. 上传目录相对路径解析为基于本文件所在目录的绝对路径。

实现逻辑：
    模块加载时用 yaml.safe_load 解析 config.yml；逐项取环境变量或 yml 值；导出同名常量供其他模块 import。
"""
import base64
import hashlib
import os
from pathlib import Path

import yaml

# 配置目录与 yml 路径（与 config.py 同目录）
_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _CONFIG_DIR / "config.yml"

# 读取 yml（缺失则用空字典，全部回退默认值）
with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f) or {}

_database = _cfg.get("database", {})
_jwt = _cfg.get("jwt", {})
_upload = _cfg.get("upload", {})
_cors = _cfg.get("cors", {})
_encryption = _cfg.get("encryption", {})

# 数据库（异步 DSN）
DB_DSN = os.getenv("DB_DSN", _database.get("dsn", "postgresql+asyncpg://postgres:root@localhost:5432/story_rag"))

# JWT 鉴权
JWT_SECRET = os.getenv("JWT_SECRET", _jwt.get("secret", "story-rag-dev-secret-change-me"))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", _jwt.get("algorithm", "HS256"))
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", _jwt.get("expire_minutes", 60)))

# 上传限制（对齐 API 列表）
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(_CONFIG_DIR / _upload.get("dir", "uploads")))
MAX_TXT_BYTES = int(os.getenv("MAX_TXT_BYTES", _upload.get("max_txt_bytes", 50 * 1024 * 1024)))
MAX_OTHER_BYTES = int(os.getenv("MAX_OTHER_BYTES", _upload.get("max_other_bytes", 200 * 1024 * 1024)))
MAX_CHAPTERS = int(os.getenv("MAX_CHAPTERS", _upload.get("max_chapters", 5000)))
ALLOWED_EXT = set(_upload.get("allowed_ext", [".txt", ".epub", ".pdf", ".docx"]))

# 敏感信息加密密钥（Fernet，M9 模型 api_key / 后续 M11 MCP 凭据加密复用）
#   优先级：环境变量 ENCRYPTION_KEY > config.yml encryption.key > 由 JWT_SECRET 派生。
#   规则：44 字符视为合法 Fernet key 直接使用；否则按其内容 sha256 派生 32 字节 base64 key。
def _build_fernet_key(secret: str) -> bytes:
    """构建合法的 Fernet 密钥（32 字节 base64 编码）。"""
    if len(secret) == 44:
        return secret.encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


_ENC_SECRET = os.getenv("ENCRYPTION_KEY", _encryption.get("key", "")) or JWT_SECRET
ENCRYPTION_KEY = _build_fernet_key(_ENC_SECRET)

# CORS（开发期放开，生产请收紧）
_CORS_ORIGINS = _cors.get("origins", ["*"])
if isinstance(_CORS_ORIGINS, list):
    CORS_ORIGINS = _CORS_ORIGINS
else:
    CORS_ORIGINS = str(_CORS_ORIGINS).split(",")

# 向量库（chroma 本地持久化；后续可切 milvus / pgvector）
_vector_store = _cfg.get("vector_store", {})
VECTOR_STORE_TYPE = os.getenv("VECTOR_STORE_TYPE", _vector_store.get("type", "chroma"))
_chroma_dir = _vector_store.get("chroma", {}).get("persist_dir", "storage/chroma")
# 相对路径解析为基于本文件所在目录的绝对路径
VECTOR_STORE_CHROMA_DIR = os.getenv("VECTOR_STORE_CHROMA_DIR", str(_CONFIG_DIR / _chroma_dir))

# 存储后端（local / minio）
_storage = _cfg.get("storage", {})
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", _storage.get("backend", "local"))

# MinIO 对象存储
_minio = _cfg.get("minio", {})
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", _minio.get("endpoint", "127.0.0.1:9000"))
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", _minio.get("access_key", "minioadmin"))
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", _minio.get("secret_key", "minioadmin"))
MINIO_BUCKET = os.getenv("MINIO_BUCKET", _minio.get("bucket", "story-rag"))
MINIO_SECURE = str(os.getenv("MINIO_SECURE", _minio.get("secure", "false"))).lower() == "true"
