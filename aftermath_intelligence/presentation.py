"""Turn an `InvestigationReport` into engineer-facing text and the backend's suggestion shape.

The deterministic narrative here is what users see when no LLM is reachable, so it must be
as specific as the LLM version: real signatures, real past incident IDs, real warnings.
"""
from __future__ import annotations

import re
from typing import Any

from .investigation import InvestigationReport, Phase
from .prompts import confidence_words
from .rca import HypothesisState


def narrative_context(report: InvestigationReport) -> dict[str, Any]:
    """Compact, LLM-ready summary (also useful for logs/debugging)."""
    return {
        "incident": {"id": report.incident_id, "service": report.service, "severity": report.severity,
                     "status": report.status.value},
        "headline": report.headline,
        "hypotheses": [{"title": h.title, "evidence_strength": confidence_words(h.score), "status": h.status.value,
                        "reasoning": h.reasoning or h.description, "based_on": list(h.based_on_incidents)}
                       for h in report.hypotheses[:3]],
        "comparison": report.hypothesis_comparison,
        "key_evidence": [{"signature": s.label, "count": s.count, "log": s.snippet[:160]} for s in report.signatures[:5]],
        "anomalies": [a.summary for a in report.anomalies[:4]],
        "failure_chain": {"roots": list(report.chain.root_candidates), "contributing": list(report.chain.contributing),
                          "symptoms": list(report.chain.symptoms), "triggers": list(report.chain.triggers)},
        "past_incidents": [{"id": r.record.incident_id, "relevance": r.relevance, "root_cause": r.record.root_cause,
                            "fix": r.record.resolution,
                            "did_not_work": [a.description for a in r.record.attempts if a.outcome.value in {"failed", "temporary"}]}
                           for r in report.recalled_incidents[:3]],
        "warnings": list(report.remediation.warnings[:5]),
        "plan": [s.description for s in report.remediation.steps[:6]],
        "memory": "not consulted" if not report.memory_consulted else f"{report.memory_backend} memory",
        "limitations": list(report.degraded),
    }


def deterministic_narrative(report: InvestigationReport) -> str:
    leader = report.hypotheses[0] if report.hypotheses else None
    parts: list[str] = []
    if report.recalled_incidents:
        best = report.recalled_incidents[0]
        n = len(report.recalled_incidents)
        parts.append(f"I found {n} similar incident{'s' if n > 1 else ''} in memory; the closest is "
                     f"{best.record.incident_id} ({best.record.title}), matching on "
                     f"{'; '.join(best.matching_points) or 'symptoms'}. ")
    elif report.memory_consulted:
        parts.append(f"Nothing similar to this {report.service} incident is in memory yet, so this is based on the "
                     "current evidence alone. ")
    else:
        parts.append("Historical memory was not consulted for this analysis. ")
    if leader is None or leader.status == HypothesisState.UNRESOLVED:
        signals = ", ".join(s.label for s in report.signatures[:3])
        parts.append("The evidence does not point to a known failure mechanism"
                     + (f" (I only see {signals})" if signals else "") + ". ")
    else:
        parts.append(f"The leading explanation ({confidence_words(leader.score)}) is {leader.title.lower()}: "
                     f"{leader.description} ")
        sig = next((s for s in report.signatures if s.id == leader.signature_id), None)
        if sig:
            parts.append(f'The logs show it directly: "{sig.snippet[:140]}" ({sig.count} occurrence'
                         f'{"s" if sig.count != 1 else ""}). ')
        if report.chain.symptoms:
            parts.append("It also explains the downstream symptoms ("
                         + ", ".join(s.replace('_', ' ') for s in report.chain.symptoms) + "). ")
        if len(report.hypotheses) > 1 and report.hypothesis_comparison:
            parts.append(report.hypothesis_comparison + " ")
    avoid = [lesson for lesson in report.lessons if lesson.kind == "avoid_action"]
    if avoid:
        parts.append("\n\nWhat did NOT work before: " + "; ".join(lesson.message for lesson in avoid[:3])
                     + ". I'd skip those. ")
    contradiction = next((lesson for lesson in report.lessons if lesson.kind == "contradiction"), None)
    if contradiction:
        parts.append("\n\n" + contradiction.message + " ")
    recurrence = next((lesson for lesson in report.lessons if lesson.kind == "recurrence"), None)
    if recurrence:
        parts.append("\n\n" + recurrence.message + " ")
    if report.degraded:
        parts.append("\n\n(Limitations: " + "; ".join(report.degraded[:2]) + ".) ")
    parts.append("\n\nHere is the plan I'd follow:")
    return "".join(parts)


_HEDGE = re.compile(
    r"^\s*(?:(?:likely|possibly|probably|perhaps|unknown)\s*[:,-]\s*)?"
    r"(?:(?:the\s+)?(?:most\s+)?(?:likely|probable|possible|primary)?\s*(?:root\s+)?cause"
    r"(?:\s+of\s+[^,]{1,80}?)?\s+(?:is|was|appears to be|seems to be)\s*(?:that\s+)?(?=\S)"
    r"|(?:it\s+is\s+)?(?:most\s+)?(?:likely|probably|possibly)\s+(?:that\s+)?(?=[a-z]))?", re.I)


_TRAILING_HEDGE = re.compile(r"\s+(?:is|was|appears to be)\s+the\s+(?:most\s+)?(?:likely|probable)\s+(?:root\s+)?cause\b", re.I)


def clean_headline(text: str) -> str:
    """Strip hedges the AI adds itself ("The most likely root cause is ...") so the label is not doubled."""
    stripped = _HEDGE.sub("", text.strip(), count=1).strip()
    stripped = _TRAILING_HEDGE.sub("", stripped).strip()
    if not stripped:
        return text.strip()
    return stripped[0].upper() + stripped[1:]


def suggestion_fields(report: InvestigationReport) -> dict[str, Any]:
    """Map to the backend's `Suggestion` contract (likely_root_cause, steps, warnings, confidence, based_on)."""
    leader = report.hypotheses[0] if report.hypotheses else None
    tentative = False
    if leader is None or leader.status == HypothesisState.UNRESOLVED:
        root = "Unknown: " + (report.headline if report.signatures else "no known failure signature in the alert or logs.")
    else:
        # The cause is stated plainly: engineers save this text as the confirmed root cause, so a hedge
        # ("Likely: ...") would end up in memory. Weak certainty is reported as a warning instead.
        root = clean_headline(report.headline) if report.headline else leader.title
        tentative = leader.status != HypothesisState.LIKELY
    steps = []
    for step in report.remediation.steps:
        marker = " (temporary relief)" if step.temporary else ""
        approval = " [needs approval]" if step.requires_approval else ""
        steps.append(f"{step.description}{marker}{approval}")
    steps.append("Watch error rate and latency for 15 minutes before resolving")
    warnings = list(report.remediation.warnings)
    if tentative:
        warnings.insert(0, "Low certainty: this is a possible cause, but the evidence does not single it out yet.")
    if report.status == Phase.AWAITING_APPROVAL:
        warnings.append("Some diagnostics need human approval before they can run.")
    if not report.memory_consulted:
        warnings.append("Historical memory was not consulted.")
    based_on = list(dict.fromkeys(i for h in report.hypotheses[:2] for i in h.based_on_incidents)) or \
        [r.record.incident_id for r in report.recalled_incidents[:3]]
    return {"likely_root_cause": root[:600], "steps": steps[:9], "warnings": list(dict.fromkeys(warnings))[:8],
            "confidence": round(min(0.95, max(0.05, report.top_score)), 2), "based_on": based_on}
