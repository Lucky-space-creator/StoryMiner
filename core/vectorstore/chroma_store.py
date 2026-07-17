"""
Chroma 向量库实现（本地持久化）

整体思路：
    基于 chromadb.PersistentClient 将向量持久化到本地磁盘目录，按知识库分 collection，
    以 chunk 主键为记录 id；同步客户端经线程池包装为异步，避免阻塞事件循环。

关键点：
    1. 持久化目录由配置注入；collection 不存在时自动创建（get_or_create_collection）。
    2. upsert 幂等：重建索引时同 id 覆盖，无需先删。
    3. query 返回 [(chunk_id, distance)]，id 转回 int 供上层回查 PG。

实现逻辑：
    构造时建立 PersistentClient；所有 chromadb 调用经 run_in_executor 在线程池执行。
"""
import asyncio

import chromadb

from vectorstore.base import VectorStore


class ChromaStore(VectorStore):
    """Chroma 本地持久化向量库。"""

    def __init__(self, persist_dir: str):
        """建立本地持久化客户端。

        关键点：chromadb 客户端初始化会向默认 localhost:8000 发 HTTP 探测，
        与业务端口冲突被 FastAPI 截胡成 404；将内部探测端口改到 18000 规避。
        实际向量读写走进程内实现，不受该端口影响。
        """
        try:
            chromadb.set_telemetry(False)
        except Exception:
            pass
        settings = chromadb.Settings(
            chroma_server_host="localhost",
            chroma_server_http_port=18000,
        )
        self._client = chromadb.PersistentClient(path=persist_dir, settings=settings)

    def _collection(self, name: str):
        """获取或创建集合（按知识库隔离）。"""
        return self._client.get_or_create_collection(name)

    async def upsert(self, collection, ids, vectors, documents, metadatas=None):
        col = self._collection(collection)
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: col.upsert(
                ids=[str(i) for i in ids],
                embeddings=vectors,
                documents=documents,
                metadatas=metadatas,
            ),
        )

    async def query(self, collection, vector, top_k=5, where=None):
        col = self._collection(collection)
        res = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: col.query(query_embeddings=[vector], n_results=top_k, where=where),
        )
        ids = res["ids"][0]
        dists = res["distances"][0]
        return [(int(i), float(d)) for i, d in zip(ids, dists)]

    async def delete(self, collection, ids=None, where=None):
        col = self._collection(collection)
        if ids is not None:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: col.delete(ids=[str(i) for i in ids])
            )
        elif where is not None:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: col.delete(where=where)
            )

    async def delete_collection(self, collection):
        # 幂等删除：集合不存在时静默忽略，避免重建索引（先删 PG 切片再删集合）因集合缺失而 500。
        def _del():
            try:
                self._client.delete_collection(collection)
            except Exception:
                pass
        await asyncio.get_running_loop().run_in_executor(None, _del)
