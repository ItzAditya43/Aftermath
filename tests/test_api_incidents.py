import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.models import Incident
from tests.conftest import ALERT


def test_health(client):
    body = client.get("/health").json()
    assert body == {"status": "ok", "agent_mode": "mock", "agent": "mock", "database": "ok"}


def test_create_incident(client):
    response = client.post("/incidents", json=ALERT)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "INC-1021"
    assert body["status"] == "open"
    assert body["severity"] == "SEV1"
    assert body["logs"] == ALERT["logs"]
    assert body["created_at"].endswith("Z")
    assert body["resolved_at"] is None and body["last_suggestion"] is None
    assert [e["type"] for e in body["timeline"]] == ["alert"]


def test_create_assigns_sequential_ids(make_incident):
    assert make_incident()["id"] == "INC-1021"
    assert make_incident()["id"] == "INC-1022"


def test_create_validation_error_shape(client):
    response = client.post("/incidents", json={"title": "x", "service": "Payments API", "severity": "SEV9"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    fields = {d["loc"][-1] for d in error["details"]}
    assert {"title", "service", "severity", "alert_text"} <= fields


def test_get_incident_with_timeline(client, make_incident):
    created = make_incident()
    body = client.get(f"/incidents/{created['id']}").json()
    assert body["id"] == created["id"]
    assert body["timeline"][0]["type"] == "alert"
    assert ALERT["alert_text"] in body["timeline"][0]["message"]


def test_get_unknown_incident_404(client):
    response = client.get("/incidents/INC-9999")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Incident INC-9999 not found"}}


def test_unknown_route_uses_error_shape(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_list_newest_first_and_filters(client, make_incident):
    first = make_incident(service="auth-service", title="auth: JWKS mismatch")
    second = make_incident()
    client.post(f"/incidents/{first['id']}/resolve", json={"root_cause": "rc", "fix_that_worked": "fix"})

    listed = client.get("/incidents").json()
    assert [i["id"] for i in listed] == [second["id"], first["id"]]
    assert "logs" not in listed[0]

    assert [i["id"] for i in client.get("/incidents?status=resolved").json()] == [first["id"]]
    assert [i["id"] for i in client.get("/incidents?status=open").json()] == [second["id"]]
    assert [i["id"] for i in client.get("/incidents?service=auth-service").json()] == [first["id"]]
    assert client.get("/incidents?service=orders-db").json() == []
    assert client.get("/incidents?status=bogus").status_code == 422
    assert len(client.get("/incidents?limit=1").json()) == 1


def test_resolve_incident(client, app, make_incident):
    incident = make_incident()
    # Pretend the alert fired 42 minutes ago.
    with Session(app.state.engine) as session:
        row = session.get(Incident, incident["id"])
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=42)
        session.add(row)
        session.commit()

    payload = {
        "root_cause": "Connection leak in RefundService",
        "fix_that_worked": "Raised pool size; fixed leak",
        "what_failed": ["Restarted pods"],
        "notes": "same as INC-1009",
    }
    response = client.post(f"/incidents/{incident['id']}/resolve", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "resolved"
    assert body["memory_saved"] is True
    assert body["root_cause"] == payload["root_cause"]
    assert body["resolution"] == payload["fix_that_worked"]
    assert body["failed_attempts"] == ["Restarted pods"]
    assert body["notes"] == "same as INC-1009"
    assert body["resolved_at"] is not None
    assert 42 <= body["time_to_resolve_minutes"] <= 43
    assert [e["type"] for e in body["timeline"]] == ["alert", "action", "resolution"]

    # The agent learned it.
    agent = app.state.agent
    assert incident["id"] in agent.memory
    assert agent.memory[incident["id"]].incident.failed_attempts == ["Restarted pods"]


def test_resolve_twice_conflict(client, make_incident):
    incident = make_incident()
    payload = {"root_cause": "rc", "fix_that_worked": "fix"}
    assert client.post(f"/incidents/{incident['id']}/resolve", json=payload).status_code == 200
    response = client.post(f"/incidents/{incident['id']}/resolve", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_resolve_validation_and_404(client, make_incident):
    incident = make_incident()
    assert client.post(f"/incidents/{incident['id']}/resolve", json={"root_cause": ""}).status_code == 422
    response = client.post("/incidents/INC-4040/resolve", json={"root_cause": "rc", "fix_that_worked": "fix"})
    assert response.status_code == 404


def test_resolve_still_succeeds_when_agent_memory_fails(client, app, make_incident):
    async def broken(*args, **kwargs):
        raise RuntimeError("hindsight unavailable")

    app.state.agent.learn_from_resolution = broken
    incident = make_incident()
    body = client.post(f"/incidents/{incident['id']}/resolve", json={"root_cause": "rc", "fix_that_worked": "fix"}).json()
    assert body["status"] == "resolved"
    assert body["memory_saved"] is False
    assert "hindsight unavailable" in body["timeline"][-1]["message"]


def test_cors_allows_frontend_origin(client):
    response = client.options(
        "/incidents",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = client.get("/health", headers={"Origin": "https://aftermath.example.com"})
    assert other.headers["access-control-allow-origin"] == "https://aftermath.example.com"
    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


# ---------------------------------------------------------------- secrets are redacted on write (#9)

SECRETS = ("Hunter2Secret!", "AKIAIOSFODNN7EXAMPLE", "ghp_abcdefghijklmnopqrstuvwxyz0123456789")


def test_secrets_in_logs_and_alert_are_never_stored_or_returned(client, make_incident):
    incident = make_incident(
        alert_text="DB auth failing, password=Hunter2Secret! on primary",
        logs=("ERROR connect failed password=Hunter2Secret! aws_key=AKIAIOSFODNN7EXAMPLE "
              "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
              "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available"),
    )
    for body in (incident, client.get(f"/incidents/{incident['id']}").json(), client.get("/incidents").json()):
        text = json.dumps(body)
        assert not any(secret in text for secret in SECRETS), text
    detail = client.get(f"/incidents/{incident['id']}").json()
    assert "[REDACTED" in detail["logs"] and "[REDACTED" in detail["alert_text"]
    # Ordinary log text is untouched, so analysis still has its evidence.
    assert "HikariPool-1 - Connection is not available" in detail["logs"]


def test_secrets_in_resolution_are_redacted_before_storage_and_memory(client, app, make_incident):
    learned = []
    original = app.state.agent.learn_from_resolution

    async def spy(incident, resolution):
        learned.append(resolution)
        await original(incident, resolution)

    app.state.agent.learn_from_resolution = spy
    incident = make_incident()
    response = client.post(f"/incidents/{incident['id']}/resolve", json={
        "root_cause": "Rotated DB password=Hunter2Secret! was not deployed",
        "fix_that_worked": "Updated secret aws_key=AKIAIOSFODNN7EXAMPLE in the vault",
        "what_failed": ["Retried with token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"],
        "notes": "password=Hunter2Secret!",
    })
    assert response.status_code == 200
    stored = json.dumps(client.get(f"/incidents/{incident['id']}").json())
    assert not any(secret in stored for secret in SECRETS)
    assert not any(secret in learned[0].model_dump_json() for secret in SECRETS)


def test_ordinary_incident_text_is_unchanged_by_redaction(client, make_incident):
    incident = make_incident()
    assert (incident["title"], incident["alert_text"], incident["logs"]) == (ALERT["title"], ALERT["alert_text"], ALERT["logs"])


# ---------------------------------------------------------------- concurrent creates (#31)

def test_concurrent_creates_all_succeed_with_unique_ids(client):
    from concurrent.futures import ThreadPoolExecutor

    def create(i):
        return client.post("/incidents", json={**ALERT, "title": f"parallel incident {i}"})

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(create, range(20)))
    assert [r.status_code for r in responses] == [201] * 20, [r.text for r in responses if r.status_code != 201][:2]
    ids = [r.json()["id"] for r in responses]
    assert len(set(ids)) == 20
    assert len(client.get("/incidents").json()) == 20


def test_create_retries_when_another_process_took_the_id(client, make_incident, monkeypatch):
    from app import services

    taken = make_incident()["id"]
    real = services.next_incident_id
    calls = []

    def stale_then_real(session):
        calls.append(1)
        return taken if len(calls) == 1 else real(session)  # first pick collides, like a parallel worker

    monkeypatch.setattr(services, "next_incident_id", stale_then_real)
    response = client.post("/incidents", json=ALERT)
    assert response.status_code == 201 and response.json()["id"] != taken
    assert len(calls) == 2
