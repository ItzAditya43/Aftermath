import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ALERT = {
    "title": "payments-api: connection pool exhausted, charges failing",
    "service": "payments-api",
    "severity": "SEV1",
    "alert_text": "[FIRING] PaymentsApiHighErrorRate: 5xx rate 38% on payments-api. HikariPool-1 active=50/50, pending=212.",
    "logs": (
        "ERROR c.a.payments.ChargeController - Charge failed\n"
        "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, "
        "request timed out after 30000ms.\n"
    ),
}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        agent_mode="mock",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        frontend_origin="http://localhost:3000,https://aftermath.example.com",
        auto_seed=False,
        mock_token_delay_ms=0,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_incident(client):
    def _make(**overrides) -> dict:
        response = client.post("/incidents", json={**ALERT, **overrides})
        assert response.status_code == 201, response.text
        return response.json()

    return _make


@pytest.fixture
def seeded(client) -> dict:
    response = client.post("/admin/seed")
    assert response.status_code == 200, response.text
    return response.json()


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse a text/event-stream body into [(event, data_json), ...], skipping comments/pings."""
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if name and data:
            events.append((name, json.loads("\n".join(data))))
    return events


@pytest.fixture
def analyze(client):
    def _analyze(incident_id: str) -> list[tuple[str, dict]]:
        with client.stream("GET", f"/incidents/{incident_id}/analyze") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())
        return parse_sse(body)

    return _analyze
