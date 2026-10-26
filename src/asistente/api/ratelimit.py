"""Rate limiter in-memory por clave (Fase 10.0).

Ventana deslizante por clave (normalmente la IP del cliente) aplicada a los
endpoints NO autenticados de `/api/v1/auth/*`. Suficiente para un único
proceso; con varios workers se migra a Redis/Celery en fases posteriores.
"""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, *, max_per_minute: int) -> bool:
        if max_per_minute <= 0:
            return False
        now = time.monotonic()
        window_start = now - 60.0
        bucket = self._buckets[key]
        self._buckets[key] = bucket = [t for t in bucket if t > window_start]
        if len(bucket) >= max_per_minute:
            return False
        bucket.append(now)
        return True

    def remaining(self, key: str, *, max_per_minute: int) -> int:
        now = time.monotonic()
        window_start = now - 60.0
        bucket = self._buckets[key]
        bucket[:] = [t for t in bucket if t > window_start]
        return max(0, max_per_minute - len(bucket))
