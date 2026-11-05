# Aftermath intelligence foundation

This checkout did not yet contain the shared backend, so the intelligence work
starts as an installable Python package with no database, API-server, connector,
or memory ownership. The backend can call these functions and map the Pydantic
events to its shared schemas during integration.

## Local setup

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

## Using Groq on its free tier

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

## Implemented intelligence layer

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
