"""
向量库抽象基类

整体思路：
    定义与具体向量库无关的异步契约，使上层（切片索引、相似检索）只面向抽象编程，
    便于后续在 Chroma / Milvus / pgvector 之间切换而无需改动业务代码。

关键点：
    1. collection 按知识库隔离（约定命名 kb_{kb_id}），id 使用 chunk 主键。
    2. upsert 幂等：同 id 覆盖，支撑全量重建与增量索引。
    3. query 返回 [(chunk_id, distance)]，按距离升序，由调用方回查 PG 组装业务字段。

实现逻辑：
    抽象方法（async）由具体实现类（chroma_store 等）实现；底层同步客户端经线程池转异步。
"""
from abc import ABC, abstractmethod
from typing import Optional


class VectorStore(ABC):
    """向量库统一异步接口。"""

    @abstractmethod
    async def upsert(
        self,
        collection: str,
        ids: list,
        vectors: list,
        documents: list[str],
        metadatas: Optional[list[dict]] = None,
    ) -> None:
        """写入/更新向量（幂等，同 id 覆盖）。

        :param collection: 集合名（约定 kb_{kb_id}）
        :param ids: chunk 主键列表
        :param vectors: 与 ids 等长的向量列表
        :param documents: 与 ids 等长的原文列表
        :param metadatas: 可选标量元数据（str/int/float/bool）
        """
        raise NotImplementedError

    @abstractmethod
    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        where: Optional[dict] = None,
    ) -> list[tuple]:
        """向量相似检索，返回 [(chunk_id, distance)]（升序）。

        :param collection: 集合名
        :param vector: 查询向量
        :param top_k: 返回条数
        :param where: 可选元数据过滤条件
        """
        raise NotImplementedError

    @abstractmethod
    async def delete(
        self,
        collection: str,
        ids: Optional[list] = None,
        where: Optional[dict] = None,
    ) -> None:
        """删除向量：优先按 ids，否则按 where 过滤。"""
        raise NotImplementedError

    @abstractmethod
    async def delete_collection(self, collection: str) -> None:
        """删除整个集合（全量重建索引前置）。"""
        raise NotImplementedError
