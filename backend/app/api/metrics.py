"""Request metrics in the Prometheus text format, with no extra dependency.

Counts and latency are kept per process, which is enough while there is one backend instance (see
docs/OPERATIONS.md); a second instance would need each scraped separately.

Cardinality is bounded on purpose, because the labels come from the request: the route is the
*template* (``/recommendations/{rec_id}``, never the raw path), an unmatched path is one label
(``unmatched``) so scanning cannot create series, and an unusual HTTP method is ``OTHER``. No
organization, user, query string, header or body ever becomes a label.
"""

import threading
from collections import defaultdict

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        # per route: bucket counts (index i = observations <= BUCKETS[i]), sum and count
        self._buckets: dict[str, list[int]] = {}
        self._sum: dict[str, float] = defaultdict(float)
        self._count: dict[str, int] = defaultdict(int)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        method = method if method in _METHODS else "OTHER"
        with self._lock:
            self._requests[(method, route, status)] += 1
            buckets = self._buckets.setdefault(route, [0] * len(BUCKETS))
            for i, bound in enumerate(BUCKETS):
                if seconds <= bound:
                    buckets[i] += 1
            self._sum[route] += seconds
            self._count[route] += 1

    def render(self) -> str:
        req, dur = "adpilot_http_requests_total", "adpilot_http_request_duration_seconds"
        lines = [
            f"# HELP {req} HTTP requests by method, route template and status.",
            f"# TYPE {req} counter",
        ]
        with self._lock:
            for (method, route, status), n in sorted(self._requests.items()):
                labels = f'method="{method}",route="{_escape(route)}",status="{status}"'
                lines.append(f"{req}{{{labels}}} {n}")
            lines += [
                f"# HELP {dur} Request latency by route template.",
                f"# TYPE {dur} histogram",
            ]
            for route in sorted(self._buckets):
                r = _escape(route)
                for bound, n in zip(BUCKETS, self._buckets[route], strict=True):
                    lines.append(f'{dur}_bucket{{route="{r}",le="{bound}"}} {n}')
                lines.append(f'{dur}_bucket{{route="{r}",le="+Inf"}} {self._count[route]}')
                lines.append(f'{dur}_sum{{route="{r}"}} {self._sum[route]:.6f}')
                lines.append(f'{dur}_count{{route="{r}"}} {self._count[route]}')
        return "\n".join(lines) + "\n"
