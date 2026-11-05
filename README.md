# Aftermath

An incident response agent for on-call engineers. When a production alert comes in, the agent
recalls similar past incidents (root cause, the fix that worked, fixes that failed) from memory and
suggests a fix. When the incident is resolved, the resolution is saved back to memory, so the agent
gets better over time.

| Part | Folder | Owner |
|---|---|---|
| Backend API (FastAPI, SQLite, SSE) | `app/`, `data/`, `scripts/` | Manmath |
| AI agent (Groq, Hindsight) | `app/agent/real_agent.py`, `aftermath_intelligence/` | Aditya |
| Frontend (Next.js) | separate app, calls this API | Shashank |

---

## Backend API

### Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional; the defaults work with no keys (AGENT_MODE=mock)
```

### Run

```bash
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs with example payloads: http://localhost:8000/docs

### Demo in 4 requests

```bash
curl -X POST localhost:8000/admin/seed                 # 20 past incidents and the agent's memory
jq '.alerts[0]' data/demo_alerts.json | \
  curl -X POST localhost:8000/incidents -H 'Content-Type: application/json' -d @-   # -> INC-1021
curl -N localhost:8000/incidents/INC-1021/analyze      # watch the stream
curl -X POST localhost:8000/incidents/INC-1021/resolve -H 'Content-Type: application/json' \
     -d '{"root_cause":"Leak in ChargebackSyncJob","fix_that_worked":"Closed the connection in a finally block","what_failed":[]}'
curl localhost:8000/stats                              # time-to-resolve dropping over time
```

`data/demo_alerts.json` has four ready-made alerts:
- **payments-api pool exhaustion:** the 4th occurrence; recalls INC-1017, INC-1009 and INC-1001.
- **api-gateway 502s after a deploy:** recalls the earlier Friday-deploy 502s.
- **notification-worker OOM:** recalls the earlier batch-job OOMs.
- **search-api cluster red:** a new kind of incident, so nothing in memory matches and confidence is low.

`POST /admin/reset` wipes everything so you can run the demo again.

### Endpoints

| Method | Path | Body / query | Returns |
|---|---|---|---|
| `POST` | `/incidents` | `{title, service, severity, alert_text, logs}` | `201` incident and timeline |
| `GET` | `/incidents` | `?status=open\|investigating\|resolved&service=&limit=&offset=` | incident summaries, newest first (no `logs`) |
| `GET` | `/incidents/{id}` | | incident and `timeline[]` and `last_suggestion` |
| `GET` | `/incidents/{id}/analyze` | | **Server-Sent Events** stream (see below) |
| `POST` | `/incidents/{id}/resolve` | `{root_cause, fix_that_worked, what_failed[], notes?}` | resolved incident and `memory_saved`; `409` if already resolved |
| `GET` | `/incidents/{id}/memories` | | past incidents the agent used, most relevant first, each with `past_incident` details |
| `GET` | `/stats` | | totals, `avg_ttr_with_memory` vs `avg_ttr_without_memory`, monthly `ttr_over_time`, per-incident `resolutions` |
| `POST` | `/admin/seed` | `?rebase_dates=true` | `{seeded, skipped, ingested}` (idempotent) |
| `POST` | `/admin/reset` | | `{deleted: {...}}` |
| `GET` | `/health` | | `{status, agent_mode, agent, database}` |

Field values:
- `severity`: `SEV1`–`SEV4`
- `status`: `open` → `investigating` (set when analysis starts) → `resolved`
- Timeline `type`: `alert | analysis | action | resolution`
- Incident IDs look like `INC-1021`. Seed incidents are `INC-1001` to `INC-1020`.
- Timestamps are ISO-8601 UTC (`...Z`).

**Errors** always have this shape, with status `404`, `409`, `422` or `500`:

```json
{"error": {"code": "not_found", "message": "Incident INC-9999 not found"}}
```

On `422`, the error also includes `details: [{loc, msg, type}]`.

### The analysis stream (SSE)

`GET /incidents/{id}/analyze` returns `text/event-stream`. Each message has an `event:` name and a JSON
`data:` payload that repeats the name in `type`. The events arrive in this order:

| event | when | data |
|---|---|---|
| `memory` | 0–3 times, first | `{past_incident_id, title, service, summary, relevance_score, root_cause, resolution, failed_attempts[], time_to_resolve_minutes}` |
| `token` | many | `{text}`: append these to show the explanation typing out |
| `suggestion` | once | `{likely_root_cause, steps[], warnings[], confidence, based_on[]}` |
| `done` | last | `{incident_id, memories_used}` |
| `error` | instead of `done` if the agent fails | `{code, message}` |

The backend saves each recalled memory (`GET /memories`), the suggestion (`last_suggestion` on the
incident), and two `analysis` timeline events. Running the analysis again replaces the earlier memories.

### Frontend: consuming the stream (for Shashank)

```js
// lib/analyze.js
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function analyzeIncident(incidentId, { onMemory, onToken, onSuggestion, onDone, onError }) {
  const es = new EventSource(`${API}/incidents/${incidentId}/analyze`);
  const parse = (e) => JSON.parse(e.data);

  es.addEventListener("memory", (e) => onMemory?.(parse(e)));
  es.addEventListener("token", (e) => onToken?.(parse(e).text));
  es.addEventListener("suggestion", (e) => onSuggestion?.(parse(e)));
  es.addEventListener("done", (e) => {
    es.close(); // IMPORTANT: otherwise EventSource auto-reconnects and re-runs the analysis
    onDone?.(parse(e));
  });
  es.addEventListener("error", (e) => {
    es.close();
    // Our server-sent "error" events carry JSON data; a dropped connection fires "error" with no data.
    onError?.(e.data ? parse(e) : { code: "connection_lost", message: "Lost connection to the server" });
  });

  return () => es.close(); // call on unmount
}
```

```jsx
// in a component
useEffect(() => {
  setMemories([]); setText(""); setSuggestion(null);
  return analyzeIncident(id, {
    onMemory: (m) => setMemories((prev) => [...prev, m]),
    onToken: (t) => setText((prev) => prev + t),
    onSuggestion: setSuggestion,
    onError: (err) => setError(err.message),
  });
}, [id]);
```

CORS allows the origins in `FRONTEND_ORIGIN` (comma-separated, default `http://localhost:3000`).
Add the deployed frontend URL there.

### Plugging in the real agent (for Aditya)

The backend talks to the agent only through `app/agent/interface.py`:

```python
class AgentService(Protocol):
    def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]   # write as `async def` + `yield`
    async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None
    async def ingest_history(self, incidents: list[IncidentContext]) -> None
```

- `IncidentContext` and `ResolutionContext` are plain Pydantic models, so the agent never touches the database.
- `analyze` yields `MemoryEvent` (one per recalled incident), then `TokenEvent` chunks, then one `SuggestionEvent`.
  - The backend sends `done` for you.
  - If `analyze` raises an exception, the frontend gets an `error` event.
  - Yielding plain dicts such as `{"type": "token", "text": "..."}` also works.
- `ingest_history` is called by `POST /admin/seed` with the newly inserted (resolved) incidents.
- `learn_from_resolution` is called by `POST /incidents/{id}/resolve`. If it raises, the incident still
  resolves, and the response returns `memory_saved: false`.
- Optional: `async def reset(self)` is called by `POST /admin/reset`.

To switch over:
1. Implement the stub class in `app/agent/real_agent.py`. Its docstring maps each method to Hindsight
   retain/recall and Groq. You can call `aftermath_intelligence` from it.
2. Set `AGENT_MODE=real`, `GROQ_API_KEY`, `HINDSIGHT_API_KEY` and `HINDSIGHT_BANK_ID` in `.env`.
3. Run `pytest`. `tests/test_api_analyze.py` checks the event order and payloads the frontend relies on.

`app/agent/mock_agent.py` is a working reference implementation. You don't need to change anything else in `app/`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODE` | `mock` | `mock` or `real` |
| `GROQ_API_KEY`, `HINDSIGHT_API_KEY`, `HINDSIGHT_BANK_ID` | empty, empty, `aftermath-incidents` | real agent only |
| `DATABASE_URL` | `sqlite:///./aftermath.db` | any SQLAlchemy URL (`postgres://` is accepted too) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | CORS allowlist, comma-separated |
| `AUTO_SEED` | `false` | seed on startup if the DB is empty |
| `MOCK_TOKEN_DELAY_MS` | `30` | typing speed of the mock agent's stream |

`.env` is gitignored. Never commit real keys.

### Seed data

`data/seed_incidents.json` holds 20 resolved incidents from May to October 2026 across `payments-api`,
`auth-service`, `checkout-web`, `orders-db`, `notification-worker` and `api-gateway`. They include
realistic logs (Java/Hikari and Python stack traces, Postgres errors, Envoy 502s, `OOMKilled`) and
recurring patterns whose time-to-resolve falls each time:

| Pattern | Incidents | Time to resolve |
|---|---|---|
| payments-api connection pool exhausted (restart tried first, didn't help) | 1001 → 1009 → 1017 | 190 → 68 → 24 min |
| api-gateway 502s after Friday deploys (config change) | 1002 → 1011 → 1020 | 145 → 52 → 18 min |
| notification-worker OOMKilled during batch jobs | 1005 → 1012 → 1018 | 175 → 80 → 30 min |
| orders-db migration lock contention | 1008 → 1015 | 110 → 35 min |
| auth-service JWKS key rotation | 1006 → 1016 | 115 → 22 min |
| checkout-web ChunkLoadError after deploy | 1007 → 1019 | 70 → 15 min |

**Generating the data:** the file is produced by `python scripts/generate_seed.py`, which is
deterministic. Edit the script, not the JSON.

**Date rebasing:** by default, `/admin/seed` shifts every date by whole weeks so the newest incident
is about 1 week old. Shifting by whole weeks keeps the weekdays right ("Friday deploy"), and
timestamps inside the logs are shifted as well.

### Tests

```bash
pytest
```

The suite covers every endpoint, including reading the SSE stream, the error shapes, CORS, seeding and
stats, and the mock agent's recall. It runs against a temporary SQLite file with zero token delay.

### Deploying to Render

`render.yaml` defines one free web service. To deploy:
1. In Render, choose **New → Blueprint** and pick this repo.
2. Set `FRONTEND_ORIGIN` (and the agent keys, once the real agent is in use).
3. Check that `GET /health` returns `{"status": "ok"}`.

The free plan's disk is ephemeral, so SQLite is wiped whenever the service restarts or spins down
when idle. `AUTO_SEED=true` re-seeds the demo data on startup, but live incidents created during a
demo won't survive a restart. If they need to persist, point `DATABASE_URL` at a Render Postgres
instance.

### Layout

```
app/
  main.py          app factory, CORS, error handlers, routers
  config.py        settings from env
  db.py            engine/session
  models.py        Incident, TimelineEvent, MemoryReference tables
  schemas.py       request/response models (with /docs examples)
  services.py      incident, resolve and analysis-stream logic
  seeding.py       seed/reset
  errors.py        {"error": {...}} handling
  api/             incidents.py, stats.py (stats and health), admin.py
  agent/
    interface.py   AgentService protocol and event types (shared contract)
    mock_agent.py  realistic fake agent
    real_agent.py  stub for the Groq/Hindsight agent
    factory.py     picks mock or real from AGENT_MODE
data/              seed_incidents.json, demo_alerts.json
scripts/           generate_seed.py
tests/
```

---

## Intelligence package (`aftermath_intelligence/`)

This checkout did not yet contain the shared backend, so the intelligence work
starts as an installable Python package with no database, API-server, connector,
or memory ownership. The backend can call these functions and map the Pydantic
events to its shared schemas during integration.

### Local setup

Copy `.env.example` to `.env` and set the provider keys you plan to use.
`.env` is ignored by Git. Gemini is the current default; set
`AI_FALLBACK_PROVIDER=groq` if you want Groq as a fallback. The provider adapter
makes a request with at most one short retry for temporary errors, moves to the
fallback on an error or quota response, and caches
responses locally by prompt hash to avoid spending free-tier quota repeatedly.
Set `AFTERMATH_LLM_CACHE=0` to disable that cache.

Install, run the checks, and produce a local report:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m aftermath_intelligence.demo --no-llm
```

Run `python -m aftermath_intelligence.demo` to include one compact Gemini
reasoning request. If the free-tier service is unavailable, the deterministic
analysis still produces a report.

### Using Groq on its free tier

Keep the Groq account on its Free tier and check the account's Limits and Usage
pages for actual quotas. Groq limits apply at organization level and vary by
model and account; the published rate-limit table is for the Developer plan,
so its numbers are not Free-tier guarantees. This adapter uses Groq's
OpenAI-compatible `/chat/completions` endpoint, bounds the response, honors
short `Retry-After` windows, and falls back on quota or service errors. The
cache avoids repeated identical requests. Parse and summarize logs locally
before sending evidence to a model.

`openai/gpt-oss-20b` is configured because Groq currently lists it as a
supported production model. If a Free-tier key cannot access it, check the
active models endpoint (`GET https://api.groq.com/openai/v1/models`) and choose
a model the account can access. Do not rotate through multiple keys or accounts
to bypass quotas.

Install and run the deterministic analysis:

```python
from aftermath_intelligence import analyze_events, ingest_text

parsed = ingest_text(log_text, incident_id="inc-123", source="api.log")
report = analyze_events(parsed.events)
```

For an LLM task, use `ProviderRouter.complete(...)` or `complete_json(...)` from
`aftermath_intelligence.providers`. Keep deterministic parsing and correlation
in the engine; send only compact, relevant evidence to a provider.

### Implemented intelligence layer

- Text, JSON, JSONL, JSON arrays, timestamped logs, and Python exception traces
  normalize to provenance-preserving UTC event models. Exact duplicate events
  are removed without dropping malformed-line locations.
- Deterministic analysis includes error fingerprints/groups, service and
  severity distributions, rare errors, co-occurrence, bursts, repeated failure
  sequences, and robust time-bucket spike detection.
- Correlation edges identify close-in-time cross-service errors and
  deployment-before-error associations. Reports label these as correlations,
  not causal proof.
- Evidence-linked, competing hypotheses remain unverified until externally
  confirmed. Gemini/Groq provider selection is runtime-configurable; structured
  output is validated, unknown evidence IDs are discarded, and provider calls
  are cached and bounded.
- The investigation agent has a bounded loop, a connector-owned tool contract,
  allowlisted arguments, replay events, and strict read-only execution. Any
  state-changing tool requires approval and is never executed by this package.
- Safe remediation advice, resolution evaluation, memory-ready payloads,
  historical similarity/clustering/contradiction checks, prevention signals,
  replay metrics, reports, and structured postmortems are available as modules.

Docker and GitHub connector implementations, the application backend/database,
frontend contracts, and Hindsight remain integration points because none of
those systems are present in this checkout. The package defines adapters for
connector tools and historical context; it does not fake their results.
