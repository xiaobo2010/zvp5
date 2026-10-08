from redis import asyncio as aioredis

from . import config

__all__ = ['redis_client']

redis_client = aioredis.from_url(config.REDIS_URI)