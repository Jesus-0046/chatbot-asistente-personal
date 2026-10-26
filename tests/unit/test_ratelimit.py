"""Tests unitarios: rate limiter en memoria (Fase 1)."""

from __future__ import annotations

from asistente.application.ratelimit import RateLimiter


def test_permite_hasta_el_limite() -> None:
    rl = RateLimiter(max_per_minute=2)
    assert rl.is_allowed(user_id=7) is True
    assert rl.is_allowed(user_id=7) is True
    assert rl.is_allowed(user_id=7) is False


def test_es_por_usuario() -> None:
    rl = RateLimiter(max_per_minute=1)
    assert rl.is_allowed(user_id=1) is True
    assert rl.is_allowed(user_id=2) is True
    assert rl.is_allowed(user_id=2) is False


def test_remaining_evoluciona() -> None:
    rl = RateLimiter(max_per_minute=3)
    assert rl.remaining(user_id=5) == 3
    rl.is_allowed(user_id=5)
    rl.is_allowed(user_id=5)
    assert rl.remaining(user_id=5) == 1
