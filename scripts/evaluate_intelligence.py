"""Offline evaluation of the intelligence engine against data/eval_scenarios.json.

    python scripts/evaluate_intelligence.py           # deterministic only, no API calls
    python scripts/evaluate_intelligence.py --llm     # also run the configured AI providers
    python scripts/evaluate_intelligence.py --memory  # recall against data/seed_incidents.json

Reports top-1 root-cause accuracy, category accuracy, symptom recall, unsafe steps (state-changing
steps without an approval flag) and how scores line up with correctness.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aftermath_intelligence.calibration import CalibrationTracker, GradedPrediction  # noqa: E402
from aftermath_intelligence.ingestion import ingest_text  # noqa: E402
from aftermath_intelligence.investigation import IncidentCase, InvestigationEngine  # noqa: E402
from aftermath_intelligence.llm import LLMRouter, LLMSettings  # noqa: E402
from aftermath_intelligence.memory import InProcessMemory, build_memory_record  # noqa: E402
from aftermath_intelligence.remediation import RemediationKind  # noqa: E402


async def seed_memory() -> InProcessMemory:
    memory = InProcessMemory()
    for i in json.loads((ROOT / "data/seed_incidents.json").read_text())["incidents"]:
        await memory.retain(build_memory_record(
            incident_id=i["id"], title=i["title"], service=i["service"], alert_text=i["alert_text"],
            events=ingest_text(i["logs"], default_service=i["service"]).events, root_cause=i["root_cause"],
            resolution=i["resolution"], failed_attempts=i["failed_attempts"],
            created_at=datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")),
            time_to_resolve_minutes=i.get("time_to_resolve_minutes")))
    return memory


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="use the configured AI providers (costs tokens)")
    parser.add_argument("--memory", action="store_true", help="recall from the seed incident history")
    args = parser.parse_args()
    router = LLMRouter(LLMSettings.from_env()) if args.llm else None
    engine = InvestigationEngine(router=router, memory=await seed_memory() if args.memory else None)
    scenarios = json.loads((ROOT / "data/eval_scenarios.json").read_text())["scenarios"]
    tracker = CalibrationTracker(min_samples=30)
    correct = category_ok = unsafe = symptoms_found = symptoms_total = 0
    started = time.monotonic()
    for s in scenarios:
        report = await engine.run(IncidentCase(incident_id=s["id"], title=s["title"], service=s["service"],
                                               severity=s["severity"], alert_text=s["alert_text"], logs=s["logs"]))
        leader = report.hypotheses[0]
        expected = s["expected"]
        hit = (leader.signature_id or "unknown") == expected["root_signature"]
        correct += hit
        category_ok += leader.category.value == expected["category"]
        symptoms_total += len(expected["symptoms"])
        symptoms_found += sum(sym in report.chain.symptoms for sym in expected["symptoms"])
        unsafe += sum(1 for step in report.remediation.steps
                      if step.kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX} and not step.requires_approval)
        rank = next((i + 1 for i, h in enumerate(report.hypotheses) if (h.signature_id or "unknown") == expected["root_signature"]), None)
        tracker.record(GradedPrediction(s["id"], leader.score, "correct" if hit else "incorrect", rank))
        llm = f" llm={','.join(report.llm.providers) or '-'}" if args.llm else ""
        print(f"{'PASS' if hit else 'FAIL'} {s['id']:18} expected={expected['root_signature']:26} got={str(leader.signature_id):26} "
              f"score={leader.score:.2f}{llm}")
    n = len(scenarios)
    cal = tracker.report()
    print(f"\nroot-cause top-1: {correct}/{n} ({correct / n:.0%})   category: {category_ok}/{n}   "
          f"symptom recall: {symptoms_found}/{symptoms_total}   unsafe steps: {unsafe}")
    print(f"brier={cal.brier_score} top3={cal.top3_accuracy}  ({cal.note})  elapsed {time.monotonic() - started:.1f}s")
    if router:
        print("provider stats:", {k: v.as_dict() for k, v in router.stats.items()})
        await router.aclose()
    return 0 if correct == n and unsafe == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
