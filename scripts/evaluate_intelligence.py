"""Offline evaluation of the intelligence engine against data/eval_scenarios.json.

    python scripts/evaluate_intelligence.py           # deterministic only, no API calls
    python scripts/evaluate_intelligence.py --llm     # also run the configured AI providers
    python scripts/evaluate_intelligence.py --memory  # recall against data/seed_incidents.json

Reports top-1 root-cause accuracy, category accuracy, symptom recall, unsafe steps (state-changing
steps without an approval flag) and how scores line up with correctness.

Scenarios can also carry a `history` (past incidents loaded into memory for that scenario) and extra
expectations: `origin_frame` (substring of the stack frame the plan must start from), `current_version`,
`must_not_mention` (text that must not appear in the root cause, steps, warnings or the AI context) and
`steps_include`. Any failed expectation fails the scenario.
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
from aftermath_intelligence.presentation import narrative_context, suggestion_fields  # noqa: E402
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
    checks_failed: list[str] = []
    for s in scenarios:
        run_engine = engine
        if s.get("history"):
            memory = InProcessMemory()
            for h in s["history"]:
                await memory.retain(build_memory_record(
                    incident_id=h["id"], title=h["title"], service=h["service"], alert_text=h["alert_text"], events=(),
                    root_cause=h["root_cause"], resolution=h["resolution"], failed_attempts=h.get("failed_attempts", ()),
                    created_at=datetime.fromisoformat(h["created_at"].replace("Z", "+00:00"))))
            run_engine = InvestigationEngine(router=router, memory=memory)
        report = await run_engine.run(IncidentCase(incident_id=s["id"], title=s["title"], service=s["service"],
                                               severity=s["severity"], alert_text=s["alert_text"], logs=s["logs"]))
        leader = report.hypotheses[0]
        expected = s["expected"]
        accepted = {expected["root_signature"], *expected.get("also_accept", ())}
        hit = (leader.signature_id or "unknown") in accepted
        correct += hit
        category_ok += leader.category.value in {expected["category"], *expected.get("also_accept_category", ())}
        problems = _expectation_problems(report, expected)
        checks_failed += [f"{s['id']}: {p}" for p in problems]
        hit = hit and not problems
        symptoms_total += len(expected["symptoms"])
        symptoms_found += sum(sym in report.chain.symptoms for sym in expected["symptoms"])
        unsafe += sum(1 for step in report.remediation.steps
                      if step.kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX} and not step.requires_approval)
        rank = next((i + 1 for i, h in enumerate(report.hypotheses) if (h.signature_id or "unknown") in accepted), None)
        tracker.record(GradedPrediction(s["id"], leader.score, "correct" if hit else "incorrect", rank))
        llm = f" llm={','.join(report.llm.providers) or '-'}" if args.llm else ""
        print(f"{'PASS' if hit else 'FAIL'} {s['id']:22} expected={expected['root_signature']:26} got={str(leader.signature_id):26} "
              f"score={leader.score:.2f}{llm}" + (f"  [{'; '.join(problems)}]" if problems else ""))
    n = len(scenarios)
    cal = tracker.report()
    print(f"\nroot-cause top-1: {correct}/{n} ({correct / n:.0%})   category: {category_ok}/{n}   "
          f"symptom recall: {symptoms_found}/{symptoms_total}   unsafe steps: {unsafe}")
    print(f"brier={cal.brier_score} top3={cal.top3_accuracy}  ({cal.note})  elapsed {time.monotonic() - started:.1f}s")
    print(f"extra checks failed: {len(checks_failed)}" + "".join(f"\n  - {c}" for c in checks_failed))
    if router:
        print("provider stats:", {k: v.as_dict() for k, v in router.stats.items()})
        await router.aclose()
    return 0 if correct == n and unsafe == 0 and not checks_failed else 1


def _expectation_problems(report, expected: dict) -> list[str]:
    fields = suggestion_fields(report)
    problems = []
    if "origin_frame" in expected and expected["origin_frame"] not in " ".join([fields["likely_root_cause"], *fields["steps"]]):
        problems.append(f"origin frame {expected['origin_frame']!r} not named (got {report.origin_frame!r})")
    if "current_version" in expected and report.current_version != expected["current_version"]:
        problems.append(f"current version {report.current_version!r} != {expected['current_version']!r}")
    visible = json.dumps([fields, narrative_context(report)], default=str)
    problems += [f"mentions {text!r}" for text in expected.get("must_not_mention", ()) if text in visible]
    steps = " ".join(fields["steps"])
    problems += [f"steps miss {text!r}" for text in expected.get("steps_include", ()) if text not in steps]
    return problems


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
