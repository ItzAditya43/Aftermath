# Aftermath: 3-minute demo script

**One-line pitch:** Aftermath is an incident detective with a memory. It reads the alert and logs, finds the
first domino, remembers what fixed (and what did *not* fix) similar incidents, and hands the on-call engineer a
safe plan. In the seeded history, incidents with memory were resolved in about 36 minutes versus 1 h 44 min without.

## Before you start (5 minutes, once)

```bash
# .env
AGENT_MODE=real
AI_PROVIDER=groq
AI_FALLBACK_PROVIDER=gemini
AUTO_SEED=true
FRONTEND_ORIGIN=http://localhost:3000

python scripts/check_providers.py          # both providers must say OK
uvicorn app.main:app --port 8000
cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

- Use a 1440×900 or larger window (smaller laptops currently hide some buttons; tracked in the frontend issues).
- Groq's free tier allows roughly 2 analyses a minute; Gemini takes over automatically, so pace the demo.
- Keep `data/demo_alerts.json` open to copy alerts from.

## The script

| Time | Do | Say |
| --- | --- | --- |
| 0:00 | Show the home screen | "Production is on fire at 2 a.m. The fix usually exists — in someone's memory. Aftermath makes it the team's memory." |
| 0:20 | **New incident** → paste the *payments-api connection pool* alert → **Create** → **Analyze** | "It reads the logs, matches known failure fingerprints and recalls similar past incidents." |
| 0:40 | Point at the recalled incidents (right) and the stars forming | "Three past incidents match, the closest at 89%. Each one carries what fixed it and what didn't." |
| 1:00 | Point at the headline and warnings | "Likely a connection leak — not 'add more connections'. And it warns: *restarting only gave temporary relief in INC-1017*." |
| 1:20 | Point at the steps and the "[needs approval]" tags | "Checks first, then fixes. Anything that changes production needs a human. The AI never runs commands." |
| 1:40 | **Record resolution** with the real cause and "restarted pods, came back in 20 min" as what failed → **Save** | "When we close it, it learns the real fix and grades its own guess." |
| 2:10 | Create the *api-gateway 502 after deploy* alert → **Analyze** | "Different incident, different chain: a deploy regression. It recalls the earlier 502 incidents and their rollbacks." |
| 2:35 | Create the *search-api* alert → **Analyze** | "When evidence is thin it says 'possibly' with a low score instead of bluffing." |
| 2:50 | Close | "Same engine works for security, network, Kubernetes, data pipelines, factory equipment — and written reports from hospitals, airlines, support desks and safety teams. Only the pattern pack changes." |

## If something goes wrong

- **No AI explanation / slow provider:** the analysis still completes with the built-in explanation (bounded to ~8 s
  before falling back). Say: "it never blocks on the AI."
- **API offline banner:** restart `uvicorn`; memory is persisted in `.aftermath/`.
- **Rate limited:** wait 60 s or set `AI_PROVIDER=gemini` for the rest of the demo.

## Questions judges ask

- *Is it just ChatGPT on logs?* No. Deterministic analysis does the detective work and shows its score breakdown;
  the AI reviews it, and evidence it invents is rejected.
- *Is it safe?* Read-only diagnostics only; state-changing steps need approval; secrets are redacted before any AI call;
  instructions hidden in logs are ignored (tested).
- *Does it learn?* Yes: every resolution is stored with what failed and what worked, and predictions are graded.
  After 30 graded outcomes the confidence becomes a calibrated probability.
- *Other industries?* Packs for security, network, Kubernetes, data pipelines, industrial equipment, healthcare
  operations, logistics/airlines, customer support and workplace safety ship today. The last four read written
  reports: paste a shift or safety report into the logs field. Packs are chosen with `AFTERMATH_PACKS`, and each
  has evaluated scenarios.
