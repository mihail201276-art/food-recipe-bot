import time
from services.rate_limiter import RateLimiter


def test_allows_within_limit():
    rl = RateLimiter(max_requests=3, window_seconds=60)
    assert rl.is_allowed(1)
    assert rl.is_allowed(1)
    assert rl.is_allowed(1)


def test_blocks_exceeding_limit():
    rl = RateLimiter(max_requests=2, window_seconds=60)
    assert rl.is_allowed(1)
    assert rl.is_allowed(1)
    assert not rl.is_allowed(1)


def test_expires_after_window():
    rl = RateLimiter(max_requests=1, window_seconds=1)
    assert rl.is_allowed(1)
    assert not rl.is_allowed(1)
    time.sleep(1.1)
    assert rl.is_allowed(1)


def test_separate_users():
    rl = RateLimiter(max_requests=1, window_seconds=60)
    assert rl.is_allowed(1)
    assert rl.is_allowed(2)
    assert not rl.is_allowed(1)
