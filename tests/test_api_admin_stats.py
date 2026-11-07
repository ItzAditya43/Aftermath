from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import LOCAL_CLIENT


def test_seed_loads_20_incidents(client, seeded):
    assert seeded == {"seeded": 20, "skipped": 0, "ingested": 20}
    incidents = client.get("/incidents").json()
    assert len(incidents) == 20
    assert {i["status"] for i in incidents} == {"resolved"}
    assert {i["service"] for i in incidents} == {
        "payments-api", "auth-service", "checkout-web", "orders-db", "notification-worker", "api-gateway"
    }


def test_seed_is_idempotent(client, seeded):
    again = client.post("/admin/seed").json()
    assert again == {"seeded": 0, "skipped": 20, "ingested": 0}
    assert len(client.get("/incidents").json()) == 20


def test_seed_rebases_dates_to_recent_past(client, seeded):
    incidents = client.get("/incidents").json()
    newest = datetime.fromisoformat(incidents[0]["created_at"].replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - newest).days
    assert 5 <= age_days <= 12
    oldest = datetime.fromisoformat(incidents[-1]["created_at"].replace("Z", "+00:00"))
    assert 150 <= (newest - oldest).days <= 190  # about 6 months of history


def test_seed_keeps_weekdays_and_log_timestamps_aligned(client, seeded):
    fridays = [i for i in client.get("/incidents?service=api-gateway").json() if "Friday" in i["title"]]
    assert len(fridays) == 3
    for incident in fridays:
        assert datetime.fromisoformat(incident["created_at"].replace("Z", "+00:00")).weekday() == 4
    detail = client.get("/incidents/INC-1001").json()
    assert detail["created_at"][:10] in detail["logs"]


def test_seeded_incident_has_timeline_and_memories(client, seeded):
    detail = client.get("/incidents/INC-1017").json()
    types = [e["type"] for e in detail["timeline"]]
    assert types[0] == "alert" and types[-1] == "resolution"
    memories = client.get("/incidents/INC-1017/memories").json()
    assert [m["past_incident_id"] for m in memories] == ["INC-1009", "INC-1001"]
    assert memories[0]["past_incident"]["title"]


def test_memories_404(client):
    assert client.get("/incidents/INC-4040/memories").status_code == 404


def test_seed_without_rebase_uses_file_dates(client):
    client.post("/admin/seed?rebase_dates=false")
    assert client.get("/incidents/INC-1001").json()["created_at"] == "2026-05-12T14:05:00Z"


def test_reset_wipes_everything(client, app, seeded):
    response = client.post("/admin/reset")
    assert response.status_code == 200
    deleted = response.json()["deleted"]
    assert deleted["incidents"] == 20
    assert deleted["timeline_events"] > 20
    assert deleted["memory_references"] > 0
    assert client.get("/incidents").json() == []
    assert app.state.agent.memory == {}
    assert client.post("/admin/seed").json()["seeded"] == 20


def test_stats_empty(client):
    stats = client.get("/stats").json()
    assert stats["total_incidents"] == 0
    assert stats["avg_time_to_resolve_minutes"] is None
    assert stats["ttr_over_time"] == [] and stats["resolutions"] == []


def test_stats_show_ttr_dropping(client, seeded):
    stats = client.get("/stats").json()
    assert stats["total_incidents"] == 20
    assert stats["resolved"] == 20 and stats["open"] == 0
    assert stats["by_service"]["payments-api"] == 4
    assert stats["avg_ttr_with_memory"] < stats["avg_ttr_without_memory"]

    buckets = stats["ttr_over_time"]
    assert len(buckets) >= 5
    assert buckets[-1]["avg_ttr_minutes"] < buckets[0]["avg_ttr_minutes"] / 3
    assert sum(b["resolved"] for b in buckets) == 20

    points = stats["resolutions"]
    assert len(points) == 20
    assert points == sorted(points, key=lambda p: p["resolved_at"])
    payments = [p["time_to_resolve_minutes"] for p in points if p["service"] == "payments-api" and "pool" in p["title"]]
    assert payments == sorted(payments, reverse=True)


def test_stats_counts_live_incidents(client, seeded, make_incident, analyze):
    incident = make_incident()
    analyze(incident["id"])
    client.post(f"/incidents/{incident['id']}/resolve", json={"root_cause": "rc", "fix_that_worked": "fix"})
    stats = client.get("/stats").json()
    assert stats["total_incidents"] == 21 and stats["resolved"] == 21
    assert stats["resolutions"][-1]["id"] == incident["id"]
    assert stats["resolutions"][-1]["memory_assisted"] is True


def test_openapi_documents_every_endpoint(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    expected = {
        ("/incidents", "post"), ("/incidents", "get"), ("/incidents/{incident_id}", "get"),
        ("/incidents/{incident_id}/analyze", "get"), ("/incidents/{incident_id}/resolve", "post"),
        ("/incidents/{incident_id}/memories", "get"), ("/stats", "get"), ("/admin/seed", "post"),
        ("/admin/reset", "post"), ("/health", "get"),
    }
    assert expected <= {(p, m) for p, ops in paths.items() for m in ops}
    schemas = spec["components"]["schemas"]
    assert schemas["IncidentCreate"]["examples"]
    assert schemas["ResolveRequest"]["examples"]
    assert "text/event-stream" in paths["/incidents/{incident_id}/analyze"]["get"]["responses"]["200"]["content"]
    assert client.get("/docs").status_code == 200


def test_auto_seed_on_startup_and_mock_memory_survives_restart(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    auto = settings.model_copy(update={"auto_seed": True})
    with TestClient(create_app(auto), client=LOCAL_CLIENT) as c:
        assert len(c.get("/incidents").json()) == 20

    # A fresh process (new app, same DB): the mock agent's memory is rebuilt from resolved incidents.
    restarted = create_app(auto)
    with TestClient(restarted, client=LOCAL_CLIENT) as c:
        assert len(c.get("/incidents").json()) == 20  # not seeded twice
        assert len(restarted.state.agent.memory) == 20


# ---------------------------------------------------------------- admin routes are protected (#8)


@pytest.fixture
def keyed_client(settings):
    app = create_app(settings.model_copy(update={"admin_api_key": "s3cret-admin-key"}))
    with TestClient(app, client=LOCAL_CLIENT) as c:
        yield c


@pytest.mark.parametrize("path", ["/admin/seed", "/admin/reset"])
@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}])
def test_admin_routes_need_the_key_when_one_is_set(keyed_client, path, headers):
    response = keyed_client.post(path, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_admin_routes_work_with_the_right_key(keyed_client):
    headers = {"X-Admin-Key": "s3cret-admin-key"}
    assert keyed_client.post("/admin/seed", headers=headers).json()["seeded"] == 20
    assert keyed_client.post("/admin/reset", headers=headers).status_code == 200
    assert keyed_client.get("/incidents").json() == []


def test_rejected_reset_deletes_nothing(keyed_client):
    keyed_client.post("/admin/seed", headers={"X-Admin-Key": "s3cret-admin-key"})
    assert keyed_client.post("/admin/reset").status_code == 401
    assert len(keyed_client.get("/incidents").json()) == 20


@pytest.mark.parametrize("path", ["/admin/seed", "/admin/reset"])
def test_without_a_key_admin_routes_refuse_non_local_callers(settings, path):
    with TestClient(create_app(settings), client=("203.0.113.7", 50000)) as remote:
        response = remote.post(path)
        assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"
        assert remote.get("/incidents").status_code == 200  # the rest of the API is unaffected


def test_without_a_key_admin_routes_work_from_localhost(settings):
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as local:
        assert local.post("/admin/seed").status_code == 200


def test_testclient_host_is_not_treated_as_localhost(settings):
    """#38: the test framework's placeholder host must not pass the localhost check."""
    with TestClient(create_app(settings)) as default:  # reports its host as "testclient"
        assert default.post("/admin/reset").status_code == 403


# ---------------------------------------------------------------- memory-assisted means "before resolving" (#34)

RESOLUTION = {"root_cause": "Leak in RefundService", "fix_that_worked": "Fixed the leak"}


def _assisted(client, incident_id):
    return next(r["memory_assisted"] for r in client.get("/stats").json()["resolutions"] if r["id"] == incident_id)


def test_analysing_after_resolving_does_not_make_an_incident_memory_assisted(client, seeded, make_incident, analyze):
    incident = make_incident()
    client.post(f"/incidents/{incident['id']}/resolve", json=RESOLUTION)
    assert _assisted(client, incident["id"]) is False
    events = analyze(incident["id"])
    assert any(name == "memory" for name, _ in events)  # the re-analysis still streams recalled memories
    assert _assisted(client, incident["id"]) is False
    assert client.get(f"/incidents/{incident['id']}/memories").json() == []


def test_reanalysing_a_resolved_incident_keeps_it_memory_assisted(client, seeded, make_incident, analyze):
    incident = make_incident()
    analyze(incident["id"])
    used_before = client.get(f"/incidents/{incident['id']}/memories").json()
    client.post(f"/incidents/{incident['id']}/resolve", json=RESOLUTION)
    assert _assisted(client, incident["id"]) is True
    analyze(incident["id"])
    assert _assisted(client, incident["id"]) is True
    assert client.get(f"/incidents/{incident['id']}/memories").json() == used_before
    detail = client.get(f"/incidents/{incident['id']}").json()
    assert detail["status"] == "resolved" and detail["last_suggestion"] is not None


def test_memory_recorded_after_resolution_is_not_counted(client, app, seeded, make_incident):
    """Rows written by older versions (re-analysis after resolve) are excluded too."""
    from datetime import timedelta

    from sqlmodel import Session

    from app.models import Incident, MemoryReference

    incident = make_incident()
    client.post(f"/incidents/{incident['id']}/resolve", json=RESOLUTION)
    with Session(app.state.engine) as session:
        resolved_at = session.get(Incident, incident["id"]).resolved_at
        session.add(MemoryReference(incident_id=incident["id"], past_incident_id="INC-1017", summary="later",
                                    relevance_score=0.9, used_at=resolved_at + timedelta(minutes=5)))
        session.commit()
    assert _assisted(client, incident["id"]) is False
