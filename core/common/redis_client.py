"""
Redis 客户端单例（混合分析管道 L2）

整体思路：
    封装 redis.asyncio 客户端，供 Celery broker 之外的轻量用途（如结果缓存、
    并发信号量、WebSocket 背压计数）。统一从这里取连接，避免散落创建连接池。

关键点：
    1. 兼容约束：本机 Redis 为 3.2.100 老版本，redis-py 8.x 默认 protocol=3，
       老服务端无法识别，必须用 protocol=2 协商。
    2. 单例：模块级缓存 _CLIENT，懒加载，首次访问时按 config.REDIS_URL 构建。
    3. 解析带认证的 URL：redis-py 8 对 redis://:password@host:port 已支持，
       但老 Redis 偶发解析异常，这里显式用 username/password 参数兜底。

实现逻辑：
    get_redis_client() 返回共享的 Redis 实例；ping() 做存活探测供启动时校验。
"""
from redis.asyncio import Redis

from config import REDIS_URL, REDIS_PASSWORD

# 模块级单例：整个进程共享一个连接池，避免重复建连。
_CLIENT: Redis | None = None


def get_redis_client() -> Redis:
    """获取（懒加载并缓存）共享的 Redis 异步客户端。

    实现逻辑：
        首次调用按 REDIS_URL 构建；protocol=2 兼容老 Redis 3.2；
        decode_responses=True 直接返回 str，省去手动编解码。
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = Redis.from_url(
            REDIS_URL,
            password=REDIS_PASSWORD,
            decode_responses=True,
            protocol=2,  # 关键：兼容本机 Redis 3.2.100（默认 3 不被老节点支持）
        )
    return _CLIENT


async def ping() -> bool:
    """Redis 存活探测，启动时调用，连不上返回 False 便于告警而不阻断。"""
    try:
        return bool(await get_redis_client().ping())
    except Exception as exc:  # noqa: BLE001
        print(f"[redis] ping 失败: {exc}")
        return False


async def close() -> None:
    """进程退出时关闭共享客户端连接池。"""
    global _CLIENT
    if _CLIENT is not None:
        await _CLIENT.aclose()
        _CLIENT = None
