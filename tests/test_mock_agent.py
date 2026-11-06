import asyncio
import json
from datetime import datetime, timezone

from app.agent.factory import create_agent
from app.agent.interface import AgentService, IncidentContext, ResolutionContext
from app.agent.mock_agent import MockAgent, _split_steps, detect_signatures
from app.config import Settings
from app.seeding import SEED_PATH


def _seed_contexts() -> list[IncidentContext]:
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))["incidents"]
    return [IncidentContext(**{k: v for k, v in i.items() if k in IncidentContext.model_fields}) for i in items]


def _new(service: str, title: str, alert: str, logs: str = "") -> IncidentContext:
    return IncidentContext(
        id="INC-9001", title=title, service=service, severity="SEV2", status="open",
        alert_text=alert, logs=logs, created_at=datetime.now(timezone.utc),
    )


def _run(agent: MockAgent, incident: IncidentContext) -> list:
    async def collect():
        return [event async for event in agent.analyze(incident)]

    return asyncio.run(collect())


def test_factory_returns_mock_that_satisfies_protocol():
    agent = create_agent(Settings(_env_file=None, agent_mode="mock"))
    assert isinstance(agent, MockAgent)
    assert isinstance(agent, AgentService)


def test_factory_real_mode_builds_real_agent():
    agent = create_agent(Settings(_env_file=None, agent_mode="real"))
    assert agent.name == "real"


def test_signatures():
    assert set(detect_signatures("Last State: OOMKilled, Exit Code: 137, weekly digest batch")) >= {"oom", "batch_job"}
    assert "pool_exhausted" in detect_signatures("HikariPool-1 - Connection is not available")
    assert "bad_gateway" in detect_signatures('"status":502')
    assert detect_signatures("all good") == {}


def test_recalls_recurring_patterns():
    agent = MockAgent(token_delay_ms=0)
    asyncio.run(agent.ingest_history(_seed_contexts()))
    cases = {
        ("api-gateway", "502 after deploy", '[FIRING] 502 rate 16% after deploy of api-gateway release v2.58.0'):
            {"INC-1002", "INC-1011", "INC-1020"},
        ("notification-worker", "worker OOMKilled", "pod restarted (OOMKilled) during batch, exit code 137"):
            {"INC-1005", "INC-1012", "INC-1018"},
        ("orders-db", "lock timeouts", "ERROR: canceling statement due to lock timeout during migration deploy"):
            {"INC-1008", "INC-1015"},
        ("auth-service", "401 spike", "jwt verification failed: no matching key found in JWKS for kid"):
            {"INC-1006", "INC-1016"},
    }
    for (service, title, alert), expected in cases.items():
        events = _run(agent, _new(service, title, alert))
        recalled = {e.past_incident_id for e in events if e.type == "memory"}
        assert recalled == expected, (service, recalled)
        assert events[-1].type == "suggestion"


def test_unrelated_incident_on_same_service_not_recalled():
    agent = MockAgent(token_delay_ms=0)
    asyncio.run(agent.ingest_history(_seed_contexts()))
    events = _run(agent, _new("payments-api", "payments-api: disk full", "No space left on device on /var/log"))
    assert [e for e in events if e.type == "memory"] == []


def test_ingest_ignores_unresolved_and_learn_overrides():
    agent = MockAgent(token_delay_ms=0)
    open_incident = _new("payments-api", "pool", "connection pool exhausted")
    asyncio.run(agent.ingest_history([open_incident]))
    assert agent.memory == {}
    asyncio.run(agent.learn_from_resolution(open_incident, ResolutionContext(root_cause="leak", fix_that_worked="fix")))
    assert agent.memory["INC-9001"].incident.status == "resolved"
    assert agent.memory["INC-9001"].incident.resolution == "fix"


def test_steps_are_imperative():
    assert _split_steps("Rolled back api-gateway to v2.40.3; set idle_timeout to 4s and redeployed") == [
        "Roll back api-gateway to v2.40.3",
        "Set idle_timeout to 4s and redeploy",
    ]
