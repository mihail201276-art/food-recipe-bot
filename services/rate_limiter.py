from collections import defaultdict
from datetime import datetime, timedelta


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)
        self.requests: dict[int, list[datetime]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = datetime.now()
        self.requests[user_id] = [
            t for t in self.requests[user_id] if now - t < self.window
        ]
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        self.requests[user_id].append(now)
        return True


rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
