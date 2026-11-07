# Aftermath

An incident response agent for on-call engineers. When a production alert comes in, the agent
recalls similar past incidents (root cause, the fix that worked, fixes that failed) from memory and
suggests a fix. When the incident is resolved, the resolution is saved back to memory, so the agent
gets better over time.

| Part | Folder | Owner |
|---|---|---|
| Backend API (FastAPI, SQLite, SSE) | `app/`, `data/`, `scripts/` | Manmath |
| Intelligence engine and real agent (Groq → Gemini → Ollama) | `aftermath_intelligence/`, `app/agent/real_agent.py` | Aditya |
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

### The agent contract

The backend talks to the agent only through `app/agent/interface.py`:

```python
class AgentService(Protocol):
    def analyze(self, incident: IncidentContext) -> AsyncIterator[AgentEvent]   # write as `async def` + `yield`
    async def learn_from_resolution(self, incident: IncidentContext, resolution: ResolutionContext) -> None
    async def ingest_history(self, incidents: list[IncidentContext]) -> None
```

- `IncidentContext` and `ResolutionContext` are plain Pydantic models, so the agent never touches the database.
- `analyze` yields `MemoryEvent` (one per recalled incident), then `TokenEvent` chunks, then one `SuggestionEvent`.
  The backend sends `done`; if `analyze` raises, the frontend gets an `error` event.
- `ingest_history` is called by `POST /admin/seed`; `learn_from_resolution` by `POST /incidents/{id}/resolve`
  (if it raises, the incident still resolves with `memory_saved: false`); optional `reset()` by `POST /admin/reset`.

Two implementations exist: `mock_agent.py` (no keys, scripted recall) and `real_agent.py`, the intelligence engine
described in [Intelligence engine](#intelligence-engine-aftermath_intelligence). `AGENT_MODE` picks one.

Both state `likely_root_cause` plainly (no "Likely…" hedge), since the frontend pre-fills it when resolving. The mock
has no graded history, so it sets `confidence_calibrated` only when two or more past incidents agree. That lets the
UI's "consensus" state be demoed without keys; a single match stays a ranking score.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODE` | `mock` | `mock` or `real` |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | empty | real agent; all other engine settings are listed under [Intelligence engine](#configuration-1) |
| `HINDSIGHT_API_KEY`, `HINDSIGHT_BANK_ID` | empty, `aftermath-incidents` | reserved for the Hindsight memory adapter (not built yet) |
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
    real_agent.py  the intelligence engine behind the contract (AGENT_MODE=real)
    factory.py     picks mock or real from AGENT_MODE
data/              seed_incidents.json, demo_alerts.json
scripts/           generate_seed.py
tests/
```

---

## Intelligence engine (`aftermath_intelligence/`)

The engine works out *why* an incident is happening and what to do about it. It reads the alert and logs, finds
the first domino rather than the loudest symptom, remembers what fixed (and what did not fix) similar incidents,
and returns a ranked, evidence-backed diagnosis with a safe plan. `app/agent/real_agent.py` plugs it into the
backend contract above. It is selected with `AGENT_MODE=real`.

The AI is a reviewer, not the source of truth. Deterministic analysis does the detective work and shows its
scoring; the AI checks it and writes the explanation. If every AI provider is down, the analysis still completes.

### How an analysis works

1. **Read the evidence.** Logs are normalised from JSON, logfmt, nginx/access logs, Docker output, syslog and
   plain text, including multi-line Java, Python, Node and Go stack traces. The engine finds the "origin frame",
   the first line of your own code in the trace.
2. **Match known failures.** 59 failure signatures (32 core plus 5 domain packs) are matched. Each one is tagged as
   an origin, mechanism, contributor, symptom or change.
3. **Flag anomalies, with the reason.** It flags the incident onset, error spikes against a baseline (robust
   z-score), saturated resources (for example `40/40 connections, 212 waiting`), restart loops and new error types.
4. **Build the failure chain.** Root cause → contributing factors → triggers → downstream symptoms, as a causal
   graph whose edges are marked observed, known or inferred.
5. **Recall memory.** It finds similar past incidents and turns them into lessons: fixes to avoid (they failed or
   only helped briefly), proven fixes, "similar symptoms, different cause" warnings, and recurrences.
6. **Rank explanations.** Competing hypotheses are scored with a visible point-by-point breakdown. When the evidence
   is thin it reports "insufficient evidence" instead of guessing. Scores are rankings, not probabilities, until
   calibration has enough data (see below).
7. **AI review (optional).** Groq, falling back to Gemini, then Ollama, assesses the hypotheses. Any evidence
   reference the AI invents is rejected. Read-only diagnostics (Docker, GitHub) can run here.
8. **Plan.** Ordered steps: checks first, then mitigations and fixes. Every state-changing step is marked
   `[needs approval]`, and fixes that failed before become warnings.
9. **Stream.** Memory events, then the explanation token by token, then one suggestion.
10. **Learn on resolve.** The confirmed fix and failed attempts are stored, the engine's own prediction is graded,
    and a restart that only helped briefly is never recorded as the fix.

### Quick start (real mode)

```bash
cp .env.example .env
# set GROQ_API_KEY and GEMINI_API_KEY, then:
#   AGENT_MODE=real  AI_PROVIDER=groq  AI_FALLBACK_PROVIDER=gemini
python scripts/check_providers.py          # each provider should print OK (uses a few tokens)
uvicorn app.main:app --reload
```

Then use the demo requests above, or follow [`docs/DEMO.md`](docs/DEMO.md), a 3-minute walkthrough for judges.

### Configuration

All settings are optional except at least one provider key. Defaults are in `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` / `AI_FALLBACK_PROVIDER` | `groq` / `gemini` | Provider chain; the fallback can be a comma list, e.g. `gemini,ollama` |
| `GROQ_API_KEY`, `GROQ_MODEL` | empty, `openai/gpt-oss-120b` | Groq |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | empty, `gemini-3.5-flash-lite` | Google Gemini |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `http://localhost:11434/v1`, `gpt-oss:120b-cloud` | Local or self-hosted fallback, no key needed |
| `AFTERMATH_LLM_TIMEOUT_SECONDS`, `AFTERMATH_LLM_MAX_RETRIES` | `15`, `2` | Per-call limits |
| `AFTERMATH_LLM_CACHE` | `1` | Cache identical prompts in memory (`AFTERMATH_CACHE_DIR` adds a disk cache) |
| `AFTERMATH_MAX_LLM_CALLS` | `3` | AI calls allowed per investigation |
| `AFTERMATH_INVESTIGATION_SECONDS` | `20` | Hard deadline for the investigation, AI calls included |
| `AFTERMATH_NARRATIVE_FIRST_TOKEN_SECONDS`, `AFTERMATH_NARRATIVE_SECONDS` | `8`, `25` | Use the built-in explanation if the AI is slow |
| `AFTERMATH_MEMORY_PATH` | `.aftermath/memory.jsonl` | Local incident memory (empty = in-process only) |
| `AFTERMATH_CALIBRATION_PATH` | `.aftermath/calibration.jsonl` | Graded predictions for calibration |
| `AFTERMATH_CALIBRATION_MIN_SAMPLES` | `30` | Graded outcomes needed before confidence is a calibrated probability (15 for the demo seed) |
| `AFTERMATH_PACKS` | `all` | Domain packs to load: `all`, `none` or a list such as `security,network` |
| `AFTERMATH_DOCKER_TOOLS`, `DOCKER_SOCKET` | `0`, `/var/run/docker.sock` | Read-only Docker diagnostics |
| `GITHUB_REPO`, `AFTERMATH_SERVICE_REPOS`, `GITHUB_TOKEN` | empty | Commit and deploy correlation (`svc=owner/repo,...`); the token is optional for public repos |
| `AFTERMATH_LIVE_TESTS` | `0` | `1` runs `tests/test_live_providers.py` against the real APIs |

`.aftermath/` and `.env` are gitignored.

### AI providers: what to know

- **Groq "403" errors.** Groq's Cloudflare front end blocks Python `urllib`'s default User-Agent. The engine uses
  `httpx` with an explicit User-Agent; don't swap in `urllib`.
- **Free-tier limits.** Groq's free tier is about 8,000 tokens per minute, roughly 2 full analyses a minute. On a
  `429` the router honours `Retry-After`, briefly disables Groq, and Gemini answers instead. Pace live demos, or use
  a paid key.
- **Reasoning models** (`gpt-oss`). They are called with low reasoning effort and hidden reasoning. If a stream
  returns no text, the built-in explanation is used.
- **Router behaviour.** Bounded retries, a circuit breaker, a per-investigation call budget, structured JSON output
  with one repair turn, and streaming that can fail over only before the first token.

### Memory, learning and calibration

- `FileMemory` stores one record per resolved incident in `.aftermath/memory.jsonl`. Writes are atomic and the
  records survive restarts. Reports always say which memory backend was used. It is labelled `local_file` and never
  claims to be Hindsight.
- **Hindsight:** a Hindsight adapter only needs `recall(query)` and `retain(record)`, the `IncidentMemory`
  protocol in `memory.py`. Pass it as `RealAgent(settings, memory=...)`. It isn't built yet; the backend owns it.
- **Calibration:** each resolve grades the prediction (correct, partially correct, incorrect). Seeding also
  backtests history: each resolved incident is replayed, oldest first, against memory holding only the incidents
  before it, and the top hypothesis is graded against the confirmed root cause (deterministic, no AI calls; a
  grade from a live resolution is never overwritten). After `AFTERMATH_CALIBRATION_MIN_SAMPLES` graded outcomes
  (default 30), `confidence` becomes an isotonic-calibrated probability, clamped to 5–95%, and the suggestion's
  `confidence_calibrated` is `true`. The frontend only shows a percentage, or "consensus", when it is `true`.
  The demo seed backtests 20 incidents, so set the threshold to 15 to see calibrated confidence in a demo.
  With so few samples the top bucket can rest on a single incident, so treat demo percentages as illustrative.
- **Root cause wording:** `likely_root_cause` states the cause without "Likely:" / "Possibly:", because engineers
  save it as the confirmed cause. A tentative leading hypothesis adds a "Low certainty: …" warning instead.

### Diagnostics connectors (read-only)

| Connector | Tools | Notes |
|---|---|---|
| Docker Engine API (unix socket) | `docker_container_logs`, `docker_container_health`, `docker_container_metrics` | Fixed GET requests only; no shell or exec; exact container name or compose service |
| GitHub REST | `github_recent_commits`, plus commits and workflow runs used for change correlation | GET only; repositories come from config, never from the model |

Tool output becomes evidence that can confirm or weaken a hypothesis. Tools are registered in a `ToolRegistry`;
anything not read-only needs a single-use human approval, and nothing the AI writes is ever executed.

### Incident packs

The pipeline is domain-neutral; packs add patterns and playbooks for other kinds of incidents.

| Pack | Covers |
|---|---|
| `security` | Brute force, injection attempts, privilege escalation, account compromise, malware, exfiltration, leaked secrets |
| `network` | Link down or flapping, BGP/OSPF down, DNS outage, packet loss and latency |
| `kubernetes` | Image pull failure, unschedulable pods, node NotReady, evictions, quota exceeded |
| `data_pipeline` | Consumer lag, schema drift, failed or late jobs, data-quality failures |
| `industrial` | Overheating, vibration and bearings, pressure excursions, sensor dropout, PLC faults, power events, trips |

To add a pack, create a module in `aftermath_intelligence/packs/` that defines `PACK = Pack(...)` with
`Signature`s (patterns, checks, mitigations, fixes, avoid, causes), register it in `packs/__init__.py`, and add
scenarios to `data/eval_scenarios.json`.

### Safety

- **Prompt injection.** Everything sent to an AI is wrapped in `<incident_data>` fences, and instructions inside
  logs are ignored (tested).
- **Secrets.** These are redacted before any AI call or report:
  - passwords, tokens and API keys;
  - AWS, GitHub, GitLab, Slack and Stripe keys;
  - JWTs and private keys;
  - credentials in connection strings.

  A test asserts that none of them reach a provider request.
- **Approval.** State-changing steps always need human approval; fixes that failed before are blocked and shown as
  warnings.

### Tests and evaluation

```bash
pytest                                      # 183 tests, no network or keys needed
python scripts/evaluate_intelligence.py     # 20 scenarios across 6 fields
python scripts/check_providers.py [groq]    # live provider smoke test
AFTERMATH_LIVE_TESTS=1 pytest tests/test_live_providers.py
```

Current evaluation: 20/20 correct root causes, 20/20 categories, 14/14 symptoms, 0 unsafe steps. CI
(`.github/workflows/ci.yml`) runs lint, tests and this evaluation on every PR, and fails if accuracy drops or any
unsafe step appears. [`scripts/qa/`](scripts/qa/README.md) holds the black-box API, browser, UX and accessibility
checks used in the November review; the evidence is in `docs/qa/`.

### Using the engine directly

```python
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine
from aftermath_intelligence.presentation import deterministic_narrative, suggestion_fields

report = await InvestigationEngine().run(IncidentCase(
    incident_id="INC-1", title="checkout failing", service="payments-api",
    severity="SEV1", alert_text="5xx at 38%", logs=log_text))
print(report.hypotheses[0].title, report.remediation.warnings)
print(deterministic_narrative(report)); print(suggestion_fields(report))
```

Pass `router=LLMRouter(LLMSettings.from_env())`, `memory=FileMemory(path)` and `tools=registry` to enable the AI,
memory and diagnostics. `RealAgent.investigation(incident_id)` returns the full report as JSON for the planned
`GET /incidents/{id}/investigation` endpoint (#12). The report includes the graph, the hypotheses with their score
breakdowns, the evidence, the plan and a step-by-step replay.

### Module map

| Area | Modules |
|---|---|
| Pipeline | `investigation.py` (state machine, limits, replay), `presentation.py` (narrative, suggestion) |
| Evidence | `ingestion.py`, `stacktrace.py`, `signatures.py`, `packs/`, `anomalies.py`, `evidence.py`, `changes.py` |
| Reasoning | `causal.py`, `rca.py`, `debate.py`, `prompts.py` |
| Memory and learning | `memory.py`, `store.py`, `learning.py`, `calibration.py`, `insights.py`, `postmortem.py` |
| Action | `remediation.py`, `tools.py`, `connectors/` |
| AI | `llm/` (router, adapters, config, structured output), `redaction.py` |

`agent.py`, `analysis.py`, `correlation.py`, `hypotheses.py`, `providers.py`, `engine.py`, `planner.py`,
`reporting.py` and `demo.py` are the earlier standalone API (`python -m aftermath_intelligence.demo --no-llm`).
They are kept for compatibility; new work should use `investigation.py`.

### Not done yet

- **Hindsight memory adapter.** The interface exists; `FileMemory` stands in until the adapter is built.
- **Full-investigation endpoint and UI.** The endpoint is #12 and the screen is #20; the data is already available.
- **Calibrated confidence.** It needs about 30 real resolved incidents before scores become probabilities.
