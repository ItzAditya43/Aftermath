"""In-memory, per-client sliding-window rate limits for the endpoints that cost AI quota or write data."""

import math
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """At most `limit` requests per client per `window` seconds, for one named bucket.

    Kept per app instance (one process). The client is the socket address: behind a proxy that is the proxy, so
    the limit becomes shared by everyone, which still caps AI spend. `limit <= 0` disables the bucket.
    """

    def __init__(self, limit: int, window: float = 60.0, *, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, client: str) -> float | None:
        """Record a request; return None if allowed, or the seconds to wait before retrying."""
        if self.limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits[client]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1.0, hits[0] + self.window - now)
            hits.append(now)
            self._calls += 1
            if self._calls % 1000 == 0:  # drop idle clients so the table can't grow without bound
                for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[key]
        return None

    @staticmethod
    def retry_after_header(seconds: float) -> str:
        return str(math.ceil(seconds))
