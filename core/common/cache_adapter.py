"""结果缓存薄封装（混合分析管道 M5 结果缓存 / P3）。

整体思路：
    基于 cachetools.TTLCache 提供进程内结果缓存，避免重复 LLM 调用浪费 token；
    不自研 LRU，直接复用成熟库；对外暴露 get/set/has/delete/clear_prefix 与默认实例。

关键点：
    1. key 规范化：任意字符串 key 经 sha256 哈希，规避特殊字符/超长问题。
    2. TTL 默认 6 小时（21600s），maxsize 默认 512 条，按 TTL+FIFO 淘汰。
    3. 缓存仅作加速层，所有落库仍以 DB 为准；缓存丢失不影响正确性，仅多一次 LLM 调用。

实现逻辑：
    模块级 default_cache 单例；CacheAdapter 包装 TTLCache 并提供前缀清理。
"""
import hashlib
from typing import Any

from cachetools import TTLCache


class CacheAdapter:
    """进程内 TTL 结果缓存薄封装（不自研 LRU）。"""

    def __init__(self, ttl: int = 21600, maxsize: int = 512):
        """ttl 秒（默认 6h），maxsize 条（默认 512）。"""
        self._ttl = ttl
        self._store = TTLCache(maxsize=maxsize, ttl=ttl)
        # 反向索引：norm_key -> raw_key，供 clear_prefix 按原始前缀匹配
        # （sha256 不保持前缀关系，故必须保留原始 key 才能做前缀清理）
        self._raw_index: dict[str, str] = {}

    @staticmethod
    def _norm(key: str) -> str:
        """key 规范化：sha256 哈希，避免特殊字符/超长。"""
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def has(self, key: str) -> bool:
        """是否存在未过期的缓存项。"""
        return self._norm(key) in self._store

    def get(self, key: str) -> Any | None:
        """读取缓存，未命中返回 None。"""
        return self._store.get(self._norm(key))

    def set(self, key: str, value: Any) -> None:
        """写入缓存。"""
        nk = self._norm(key)
        self._store[nk] = value
        self._raw_index[nk] = key

    def delete(self, key: str) -> None:
        """删除指定 key（若不存在则静默忽略）。"""
        nk = self._norm(key)
        self._store.pop(nk, None)
        self._raw_index.pop(nk, None)

    def clear_prefix(self, prefix: str) -> int:
        """按原始前缀清理（如同一小说重分析时失效其缓存），返回清理条数。"""
        removed = [nk for nk, raw in self._raw_index.items() if raw.startswith(prefix)]
        for nk in removed:
            self._store.pop(nk, None)
            self._raw_index.pop(nk, None)
        return len(removed)


# 默认缓存实例（TTL 6h），供各分析链路共享
default_cache = CacheAdapter()
