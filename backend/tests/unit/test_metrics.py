"""Request metrics: counts, latency buckets, and bounded label cardinality."""

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.metrics import BUCKETS, Metrics
from app.config import Settings


def sample(text: str, name: str, **labels: str) -> float:
    """The value of one series in the rendered text."""
    for line in text.splitlines():
        if not line.startswith(name + "{"):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"no series {name} {labels}")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(_env_file=None))) as c:  # runs the app's startup
        yield c


def test_requests_are_counted_by_method_route_and_status(client: TestClient) -> None:
    for _ in range(3):
        assert client.get("/health").status_code == 200
    text = client.get("/metrics").text
    assert (
        sample(text, "adpilot_http_requests_total", method="GET", route="/health", status="200")
        == 3
    )
    assert "# TYPE adpilot_http_requests_total counter" in text
    assert "# TYPE adpilot_http_request_duration_seconds histogram" in text


def test_the_route_is_the_template_never_the_raw_path(client: TestClient) -> None:
    rec = uuid.uuid4()
    org = {"X-Dev-Org-Id": str(uuid.uuid4())}
    client.get(f"/api/v1/recommendations/{rec}", headers=org)
    text = client.get("/metrics").text
    assert 'route="/api/v1/recommendations/{rec_id}"' in text
    assert str(rec) not in text and org["X-Dev-Org-Id"] not in text  # no identifiers in labels


def test_unknown_paths_and_methods_cannot_create_new_series(client: TestClient) -> None:
    for i in range(25):
        client.get(f"/wp-admin/{i}.php")
    client.request("BREW", "/health")
    text = client.get("/metrics").text
    assert sample(text, "adpilot_http_requests_total", route="unmatched", status="404") == 25
    assert "wp-admin" not in text
    assert 'method="BREW"' not in text
    series = [ln for ln in text.splitlines() if ln.startswith("adpilot_http_requests_total{")]
    assert len(series) <= 6  # a scan of 25 paths added exactly one series


def test_server_errors_and_auth_failures_are_visible(client: TestClient) -> None:
    client.get("/ready")  # no database configured -> 503
    client.get("/api/v1/dashboard/summary")  # no tenant header -> 401
    text = client.get("/metrics").text
    assert sample(text, "adpilot_http_requests_total", route="/ready", status="503") == 1
    assert (
        sample(text, "adpilot_http_requests_total", route="/api/v1/dashboard/summary", status="401")
        == 1
    )


def test_latency_histogram_is_cumulative_and_consistent() -> None:
    m = Metrics()
    for seconds in (0.01, 0.2, 0.2, 3.0, 40.0):
        m.observe("GET", "/x", 200, seconds)
    text = m.render()
    counts = [
        sample(text, "adpilot_http_request_duration_seconds_bucket", route="/x", le=str(b))
        for b in BUCKETS
    ]
    assert counts == sorted(counts)  # cumulative: never decreasing
    assert (
        counts[0] == 1
        and sample(text, "adpilot_http_request_duration_seconds_bucket", route="/x", le="0.25") == 3
    )
    assert (
        sample(text, "adpilot_http_request_duration_seconds_bucket", route="/x", le="+Inf") == 5
    )  # the 40 s one
    assert counts[-1] == 4  # 30 s bucket excludes it
    assert sample(text, "adpilot_http_request_duration_seconds_count", route="/x") == 5
    assert sample(text, "adpilot_http_request_duration_seconds_sum", route="/x") == pytest.approx(
        43.41
    )


def test_label_values_are_escaped() -> None:
    m = Metrics()
    m.observe("GET", 'a"b\\c\nd', 200, 0.1)
    text = m.render()
    assert 'route="a\\"b\\\\c\\nd"' in text
    assert not re.search(r'route="[^"]*\n', text)  # no raw newline inside a label


def test_metrics_are_not_in_the_published_api_description(client: TestClient) -> None:
    assert "/metrics" not in client.get("/openapi.json").text
    assert client.get("/metrics").headers["content-type"].startswith("text/plain")
