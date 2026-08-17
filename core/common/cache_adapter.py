"""结果缓存薄封装（混合分析管道 M5 结果缓存 / P3）。

整体思路：
    提供两种后端的结果缓存，避免重复 LLM 调用浪费 token：
      - 进程内 TTLCache（CacheAdapter）：单进程、零依赖，适合单 worker 调试；
      - Redis 共享缓存（RedisLLMCache）：跨多 worker 共享，多进程部署下命中率不打折。
    不自研 LRU，直接复用成熟库（cachetools）；对外暴露统一 get/set/has/clear_prefix。

关键点：
    1. key 规范化：任意字符串 key 经 sha256 哈希，规避特殊字符/超长问题。
    2. Redis 后端 key 前缀固定 "llm_cache:"，ttl 由 config.CACHE_TTL 控制；
       进程内后端 TTL 默认 6h、maxsize 512，按 TTL+FIFO 淘汰。
    3. 缓存仅作加速层，所有落库仍以 DB 为准；缓存丢失不影响正确性，仅多一次 LLM 调用。

实现逻辑：
    构建时按 config.ENABLE_LLM_CACHE 选择后端；Redis 不可达时自动回落进程内，
    保证任何情况下分析链路不报错（仅退化为无缓存）。
"""
import hashlib
import json
from typing import Any

from cachetools import TTLCache
from config import ENABLE_LLM_CACHE, CACHE_TTL


def _norm(key: str) -> str:
    """key 规范化：sha256 哈希，避免特殊字符/超长。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class CacheAdapter:
    """进程内 TTL 结果缓存薄封装（不自研 LRU）。"""

    def __init__(self, ttl: int = 21600, maxsize: int = 512):
        """ttl 秒（默认 6h），maxsize 条（默认 512）。"""
        self._ttl = ttl
        self._store = TTLCache(maxsize=maxsize, ttl=ttl)
        # 反向索引：norm_key -> raw_key，供 clear_prefix 按原始前缀匹配
        # （sha256 不保持前缀关系，故必须保留原始 key 才能做前缀清理）
        self._raw_index: dict[str, str] = {}

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


class RedisLLMCache:
    """基于 Redis 的共享结果缓存（多 worker 部署下命中率一致）。

    整体思路：缓存值以 JSON 序列化存入 Redis，key 经 sha256 规范化并加前缀，
    跨进程共享，避免 N 个 worker 各存一份导致命中率下降。
    关键点：
        1. Redis 不可达时所有方法静默降级（get 返回 None / set 不抛错），
           不影响分析主链路，仅退化为无缓存。
        2. clear_prefix 用 SCAN 按前缀匹配删除（前缀含 sha256 前的原始 key 明文，
           故删除时按原始前缀扫描命中）。
    """

    _PREFIX = "llm_cache:"

    def __init__(self, ttl: int | None = None):
        self._ttl = ttl or CACHE_TTL

    def _client(self):
        from common.redis_client import get_redis_client
        return get_redis_client()

    def _rk(self, key: str) -> str:
        """原始 key -> Redis 全 key（含前缀 + sha256）。"""
        return self._PREFIX + _norm(key)

    def has(self, key: str) -> bool:
        try:
            return bool(self._client().exists(self._rk(key)))
        except Exception:
            return False

    def get(self, key: str) -> Any | None:
        try:
            raw = self._client().get(self._rk(key))
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        try:
            norm = _norm(key)
            client = self._client()
            # 主缓存：llm_cache:{sha256}=json(value)
            client.set(self._PREFIX + norm, json.dumps(value, ensure_ascii=False), ex=self._ttl)
            # 反向索引：llm_cache:raw:{sha256}=原始key明文，供 clear_prefix 前缀匹配
            client.set(self._PREFIX + "raw:" + norm, key, ex=self._ttl)
        except Exception:
            # Redis 不可达：静默降级，不影响主链路
            pass

    def delete(self, key: str) -> None:
        try:
            self._client().delete(self._rk(key))
        except Exception:
            pass

    def clear_prefix(self, prefix: str) -> int:
        """按原始前缀清理：扫描 llm_cache:* 命中明文前缀的项并删除，返回清理条数。"""
        try:
            client = self._client()
            removed = 0
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor, match=self._PREFIX + "*", count=200)
                for full in keys:
                    # 反向解出原始 key 明文（存值时同时写 raw:{norm}=rawkey 便于前缀匹配）
                    rawkey = client.get(self._PREFIX + "raw:" + full.split(self._PREFIX, 1)[1])
                    if rawkey and rawkey.startswith(prefix):
                        client.delete(full)
                        removed += 1
                if cursor == 0:
                    break
            return removed
        except Exception:
            return 0


def _build_cache():
    """按 ENABLE_LLM_CACHE 选择后端；启用则优先 Redis，失败回落进程内。"""
    if ENABLE_LLM_CACHE:
        try:
            # 探活一次，连不上则回落进程内
            from common.redis_client import ping
            if ping():
                return RedisLLMCache()
        except Exception:
            pass
        return CacheAdapter()
    return CacheAdapter()


# 默认缓存实例：ENABLE_LLM_CACHE=true 且 Redis 可达时为 Redis 共享缓存，否则进程内
default_cache = _build_cache()

