"""Layer 4 — semantic response cache.

Caches the *shape* of an answer, not a specific customer's answer. Queries are
normalised (order numbers replaced with a placeholder) before embedding, so
"Where is ORD-10042?" and "What's the status of ORD-10099?" hit the same entry
— but the entry is keyed on the resolved order data too, so the second customer
never receives the first customer's delivery date.

That distinction is the whole safety story for this layer: a semantic cache
that ignores it is a data-leak generator.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from app.config import settings
from app.services.embeddings import cosine, embed_one, normalize


@dataclass
class CacheEntry:
    query_norm: str
    vector: np.ndarray
    response: str
    confidence: float
    sources: list[str]
    context_key: str
    expires_at: float
    tier: str
    hits: int = 0
    created_at: float = field(default_factory=time.time)


@dataclass
class CacheLookup:
    hit: bool
    entry: CacheEntry | None = None
    similarity: float = 0.0


class SemanticCache:
    def __init__(self) -> None:
        self._entries: list[CacheEntry] = []
        self._lock = threading.Lock()
        self.lookups = 0
        self.hits = 0

    # -- keying ---------------------------------------------------------
    @staticmethod
    def context_key(order_ids: list[str], policy_sources: list[str]) -> str:
        """Two queries share a cache entry only if they resolve to the same facts.

        Without this, the cache would answer a question about one order using
        another order's data.
        """
        payload = "|".join(sorted(order_ids)) + "##" + "|".join(sorted(policy_sources))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # -- read -----------------------------------------------------------
    def lookup(self, query: str, context_key: str) -> CacheLookup:
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            if not self._entries:
                self.lookups += 1
                return CacheLookup(hit=False)

            q_vec = embed_one(query)
            best: CacheEntry | None = None
            best_sim = 0.0
            for entry in self._entries:
                if entry.context_key != context_key:
                    continue
                sim = cosine(q_vec, entry.vector)
                if sim > best_sim:
                    best_sim, best = sim, entry

            self.lookups += 1
            if best is not None and best_sim >= settings.cache_threshold:
                best.hits += 1
                self.hits += 1
                return CacheLookup(hit=True, entry=best, similarity=round(best_sim, 4))
            return CacheLookup(hit=False, similarity=round(best_sim, 4))

    # -- write ----------------------------------------------------------
    def store(
        self,
        query: str,
        response: str,
        confidence: float,
        sources: list[str],
        context_key: str,
        tier: str,
    ) -> None:
        ttl = settings.cache_ttl_order if tier == "simple" else settings.cache_ttl_policy
        entry = CacheEntry(
            query_norm=normalize(query),
            vector=embed_one(query),
            response=response,
            confidence=confidence,
            sources=sources,
            context_key=context_key,
            expires_at=time.time() + ttl,
            tier=tier,
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > settings.cache_max_entries:
                # Oldest-first eviction; the corpus of distinct question shapes
                # is small, so this rarely fires outside a load test.
                self._entries.sort(key=lambda e: e.created_at)
                self._entries = self._entries[-settings.cache_max_entries :]

    # -- admin ----------------------------------------------------------
    def _evict_expired(self, now: float) -> None:
        self._entries = [e for e in self._entries if e.expires_at > now]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.lookups = 0
            self.hits = 0

    def stats(self) -> dict:
        with self._lock:
            self._evict_expired(time.time())
            return {
                "entries": len(self._entries),
                "lookups": self.lookups,
                "hits": self.hits,
                "hit_rate": round(self.hits / self.lookups, 4) if self.lookups else 0.0,
                "threshold": settings.cache_threshold,
            }


semantic_cache = SemanticCache()
