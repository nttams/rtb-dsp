"""Per-cluster Redis clients + Lua script loading.

**Not a single global client.** With two clusters, one client would send every write to one
Redis. :class:`ClusterRegistry` builds a client (and a set of registered scripts) per row in
the `clusters` Postgres table, keyed by both `id` and `name` (Redis keys use the cluster
*name*, per ``docs/redis-keys.md``, but ``budget_allocations`` and other FK-bearing tables use
the cluster *id* — both lookups are needed by different callers).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import redis.asyncio as aioredis
from redis.commands.core import AsyncScript

# lua/*.lua lives at the repo root, two levels above this file's directory
# (services/rtbdsp_common/redis_client.py -> services -> repo root).
LUA_DIR = Path(__file__).resolve().parents[2] / "lua"

# _common.lua defines now_ms, hclamp0, num, resv_member, parse_member and is PREPENDED to
# every script's text at load time — Redis has no `require`.
SCRIPT_NAMES = (
    "reserve",
    "settle",
    "release",
    "topup",
    "reclaim_expired",
    "reap_deleted",
    "dashboard_snapshot",
)


def _read_common() -> str:
    return (LUA_DIR / "_common.lua").read_text()


def load_scripts(client: aioredis.Redis) -> dict[str, AsyncScript]:
    """Register every script in ``lua/`` against ``client``, with ``_common.lua`` prepended.

    A script object returned by ``register_script`` is bound to the client it was registered
    against, so this must be called once per Redis client (i.e. once per cluster).
    """
    common = _read_common()
    out: dict[str, AsyncScript] = {}
    for name in SCRIPT_NAMES:
        body = (LUA_DIR / f"{name}.lua").read_text()
        out[name] = client.register_script(common + "\n" + body)
    return out


class ClusterEntry:
    __slots__ = ("id", "name", "redis_url", "client", "scripts")

    def __init__(self, id: int, name: str, redis_url: str):
        self.id = id
        self.name = name
        self.redis_url = redis_url
        self.client: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
        self.scripts: dict[str, AsyncScript] = load_scripts(self.client)


class ClusterRegistry:
    """Builds and caches one :class:`ClusterEntry` (client + scripts) per cluster row.

    Rows come from ``SELECT id, name, redis_url FROM clusters``. Call :meth:`refresh` to pick
    up newly added clusters; existing entries (and their live connections) are left untouched.
    """

    def __init__(self, pool):
        self._pool = pool
        self._by_id: dict[int, ClusterEntry] = {}
        self._by_name: dict[str, ClusterEntry] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_rows(cls, rows: list[tuple[int, str, str]]) -> "ClusterRegistry":
        """Build a registry directly from ``(id, name, redis_url)`` rows, bypassing Postgres.
        Used by tests and any caller that already has the cluster list.
        """
        reg = cls(pool=None)
        for cid, name, url in rows:
            entry = ClusterEntry(cid, name, url)
            reg._by_id[cid] = entry
            reg._by_name[name] = entry
        return reg

    async def refresh(self) -> None:
        if self._pool is None:
            return
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id, name, redis_url FROM clusters")
                rows = await cur.fetchall()
        async with self._lock:
            for cid, name, url in rows:
                if cid not in self._by_id:
                    entry = ClusterEntry(cid, name, url)
                    self._by_id[cid] = entry
                    self._by_name[name] = entry

    async def client_for(self, cluster_id: int) -> aioredis.Redis:
        if cluster_id not in self._by_id:
            await self.refresh()
        return self._by_id[cluster_id].client

    async def scripts_for(self, cluster_id: int) -> dict[str, AsyncScript]:
        if cluster_id not in self._by_id:
            await self.refresh()
        return self._by_id[cluster_id].scripts

    async def client_for_name(self, cluster_name: str) -> aioredis.Redis:
        if cluster_name not in self._by_name:
            await self.refresh()
        return self._by_name[cluster_name].client

    async def scripts_for_name(self, cluster_name: str) -> dict[str, AsyncScript]:
        if cluster_name not in self._by_name:
            await self.refresh()
        return self._by_name[cluster_name].scripts

    def all_entries(self) -> list[ClusterEntry]:
        return list(self._by_id.values())

    async def close(self) -> None:
        for entry in self._by_id.values():
            await entry.client.aclose()
