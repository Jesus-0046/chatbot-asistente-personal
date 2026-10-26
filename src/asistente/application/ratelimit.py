"""Rate limiter simple (in-memory) — suficiente para un único proceso.

No es distribuido; para escalar se necesita Redis.  La configuración del
límite por usuario (peticiones por minuto) se lee de `settings`.
"""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, max_per_minute: int = 30) -> None:
        self._max = max_per_minute
        self._timestamps: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.monotonic()
        window = now - 60.0
        ts = self._timestamps[user_id]
        # Eliminar entradas fuera de la ventana
        self._timestamps[user_id] = ts = [t for t in ts if t > window]
        if len(ts) >= self._max:
            return False
        ts.append(now)
        return True

    def remaining(self, user_id: int) -> int:
        now = time.monotonic()
        window = now - 60.0
        ts = self._timestamps[user_id]
        ts[:] = [t for t in ts if t > window]
        return max(0, self._max - len(ts))
