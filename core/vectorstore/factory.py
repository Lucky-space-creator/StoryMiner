"""
向量库工厂

整体思路：
    按配置（vector_store.type）单例创建对应向量库实现，业务层通过 get_vector_store() 获取，
    切换底层（chroma / milvus / pgvector）只需改配置，无需改动调用方。

关键点：
    1. 单例缓存：进程内仅构建一个客户端，避免重复连接开销。
    2. 当前仅注册 chroma；后续 milvus / pgvector 在此分支扩展。

实现逻辑：
    读取 config 的 VECTOR_STORE_TYPE 与对应参数，构建实现类实例并缓存到模块级变量。
"""
from vectorstore.base import VectorStore
from vectorstore.chroma_store import ChromaStore
from config import VECTOR_STORE_TYPE, VECTOR_STORE_CHROMA_DIR

_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """按配置返回向量库单例。"""
    global _store
    if _store is None:
        if VECTOR_STORE_TYPE == "chroma":
            _store = ChromaStore(VECTOR_STORE_CHROMA_DIR)
        else:
            raise ValueError(f"不支持的向量库类型: {VECTOR_STORE_TYPE}")
    return _store
