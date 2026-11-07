"""#35: per-client rate limits on create / analyze / resolve, and the optional shared API key."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ratelimit import RateLimiter
from tests.conftest import ALERT, LOCAL_CLIENT

RESOLUTION = {"root_cause": "Leak", "fix_that_worked": "Fixed the leak"}


def _client(settings, client=LOCAL_CLIENT, **overrides):
    return TestClient(create_app(settings.model_copy(update=overrides)), client=client)


def _analyze(c, incident_id, headers=None):
    with c.stream("GET", f"/incidents/{incident_id}/analyze", headers=headers or {}) as response:
        body = "".join(response.iter_text())
        return response.status_code, response.headers, body


def test_analyze_is_limited_per_client_with_retry_after(settings):
    with _client(settings, analyze_rate_limit_per_minute=3) as c:
        incident = c.post("/incidents", json=ALERT).json()
        assert [_analyze(c, incident["id"])[0] for _ in range(3)] == [200, 200, 200]
        status, headers, body = _analyze(c, incident["id"])
    assert status == 429
    assert int(headers["retry-after"]) >= 1
    assert '"code":"rate_limited"' in body


def test_limits_are_per_client(settings):
    app = create_app(settings.model_copy(update={"write_rate_limit_per_minute": 2}))
    with TestClient(app, client=("10.0.0.1", 1)) as a, TestClient(app, client=("10.0.0.2", 1)) as b:
        assert [a.post("/incidents", json=ALERT).status_code for _ in range(3)] == [201, 201, 429]
        assert b.post("/incidents", json=ALERT).status_code == 201


def test_writes_over_the_limit_create_nothing(settings):
    with _client(settings, write_rate_limit_per_minute=2) as c:
        first = c.post("/incidents", json=ALERT).json()
        c.post("/incidents", json=ALERT)
        assert c.post("/incidents", json=ALERT).status_code == 429
        assert c.post(f"/incidents/{first['id']}/resolve", json=RESOLUTION).status_code == 429  # same write bucket
        assert len(c.get("/incidents").json()) == 2
        assert c.get(f"/incidents/{first['id']}").json()["status"] == "open"


def test_reads_are_never_limited(settings):
    with _client(settings, write_rate_limit_per_minute=1, analyze_rate_limit_per_minute=1) as c:
        assert all(c.get("/incidents").status_code == 200 for _ in range(20))


def test_zero_disables_the_limit(settings):
    with _client(settings, write_rate_limit_per_minute=0) as c:
        assert all(c.post("/incidents", json=ALERT).status_code == 201 for _ in range(40))


def test_window_slides():
    now = [0.0]
    limiter = RateLimiter(2, window=60, clock=lambda: now[0])
    assert limiter.check("a") is None and limiter.check("a") is None
    assert limiter.check("a") == pytest.approx(60)
    now[0] = 30
    assert limiter.check("a") == pytest.approx(30)
    now[0] = 60.5
    assert limiter.check("a") is None


# ---------------------------------------------------------------- optional shared API key

@pytest.fixture
def keyed(settings):
    with _client(settings, api_key="team-key") as c:
        yield c


def test_api_key_guards_create_analyze_and_resolve(keyed):
    assert keyed.post("/incidents", json=ALERT).status_code == 401
    assert keyed.post("/incidents", json=ALERT, headers={"X-API-Key": "wrong"}).status_code == 401
    incident = keyed.post("/incidents", json=ALERT, headers={"X-API-Key": "team-key"}).json()
    assert _analyze(keyed, incident["id"])[0] == 401
    assert _analyze(keyed, incident["id"], {"X-API-Key": "team-key"})[0] == 200
    assert keyed.post(f"/incidents/{incident['id']}/resolve", json=RESOLUTION).status_code == 401
    ok = keyed.post(f"/incidents/{incident['id']}/resolve", json=RESOLUTION, headers={"X-API-Key": "team-key"})
    assert ok.status_code == 200


def test_api_key_leaves_reads_open(keyed):
    assert keyed.get("/incidents").status_code == 200
    assert keyed.get("/stats").status_code == 200


def test_unauthenticated_calls_do_not_use_up_the_quota(settings):
    with _client(settings, api_key="team-key", write_rate_limit_per_minute=2) as c:
        for _ in range(5):
            assert c.post("/incidents", json=ALERT).status_code == 401
        headers = {"X-API-Key": "team-key"}
        assert [c.post("/incidents", json=ALERT, headers=headers).status_code for _ in range(3)] == [201, 201, 429]


def test_no_api_key_by_default(client):
    assert client.post("/incidents", json=ALERT).status_code == 201
