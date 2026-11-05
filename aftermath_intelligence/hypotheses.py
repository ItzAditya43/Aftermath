from __future__ import annotations

import json
import re
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .correlation import CorrelationReport
from .evidence import Evidence, EvidenceKind
from .providers import LLMResponse, ProviderRouter


class HypothesisStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    CONFIRMED = "confirmed"


class RootCauseHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str
    title: str
    explanation: str
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    status: HypothesisStatus = HypothesisStatus.UNVERIFIED
    generated_by: str = "deterministic"
    assumptions: tuple[str, ...] = ()


def generate_deterministic_hypotheses(evidence: tuple[Evidence, ...] | list[Evidence],
                                      correlations: CorrelationReport | None = None) -> tuple[RootCauseHypothesis, ...]:
    """Create competing, explicitly unverified explanations from concrete signals."""
    evidence_items = tuple(evidence)
    hypotheses: list[RootCauseHypothesis] = []
    categories = {
        "dependency_failure": (r"timeout|connection refused|connection reset|upstream|dns|gateway|unavailable", 0.62,
                               "A dependency or network path is failing."),
        "resource_exhaustion": (r"out of memory|oom|memory limit|cpu thrott|disk full|no space left", 0.66,
                                "A resource limit or exhausted host resource may be causing failures."),
        "database_connectivity": (r"database|sql|pool exhausted|too many connections|deadlock", 0.58,
                                  "Database availability, capacity, or query contention may be involved."),
        "authentication_or_configuration": (r"unauthorized|forbidden|invalid credential|certificate|config(?:uration)? error", 0.55,
                                            "A credential, certificate, or configuration problem may be involved."),
    }
    for title, (pattern, base_confidence, explanation) in categories.items():
        matching: list[Evidence] = []
        contradicting: list[Evidence] = []
        for item in evidence_items:
            if item.kind in {EvidenceKind.OBSERVATION, EvidenceKind.ERROR_PATTERN}:
                if re.search(pattern, item.summary, re.I):
                    matching.append(item)
            elif item.kind == EvidenceKind.TOOL_RESULT:
                text = item.summary + " " + " ".join(str(value) for value in item.attributes.values())
                if not re.search(pattern, f"{item.source} {text}", re.I):
                    continue
                if re.search(r"\b(healthy|normal|no errors?|no issues?|within limits|nominal|passed|ready)\b", text, re.I):
                    contradicting.append(item)
                elif re.search(r"\b(unhealthy|unavailable|exhausted|too many|timeout|elevated|errors?|failed|degraded|saturated)\b", text, re.I):
                    matching.append(item)
        if matching or contradicting:
            event_ids = {event_id for item in matching for event_id in item.event_ids}
            count = max(len(event_ids), max((int(item.attributes.get("count", 1)) for item in matching), default=1))
            confidence = min(0.9, base_confidence + min(max(0, count - 1), 4) * 0.04)
            confidence = max(0.1, confidence - 0.15 * len(contradicting))
            hypotheses.append(_hypothesis(title, title.replace("_", " ").title(), explanation, confidence,
                                          tuple(item.evidence_id for item in matching),
                                          contradicting=tuple(item.evidence_id for item in contradicting),
                                          status=HypothesisStatus.WEAKENED if contradicting and len(contradicting) > len(matching)
                                          else HypothesisStatus.UNVERIFIED))

    if correlations:
        deploy_edges = [edge for edge in correlations.edges if edge.relation == "deployment_precedes_error"]
        if deploy_edges:
            ids = tuple(_evidence_id_for_events(evidence_items, (edge.source_event_id, edge.target_event_id))
                        for edge in deploy_edges)
            ids = tuple(item for item in ids if item)
            if ids:
                confidence = min(0.68, 0.58 + 0.02 * min(3, len(deploy_edges) - 1))
                hypotheses.append(_hypothesis("deployment_regression", "Recent deployment regression",
                                              "Errors followed a deployment. The timing is suggestive but does not prove the release caused them.",
                                              confidence, ids, assumptions=("No control group or causal test is available.",)))
    return tuple(sorted(hypotheses, key=lambda h: (-h.confidence, h.hypothesis_id)))


def generate_llm_hypotheses(evidence: tuple[Evidence, ...] | list[Evidence], router: ProviderRouter,
                            *, max_hypotheses: int = 4) -> tuple[tuple[RootCauseHypothesis, ...], LLMResponse]:
    """Ask the selected provider for additional hypotheses, enforcing evidence IDs."""
    items = tuple(evidence)
    valid_ids = {item.evidence_id for item in items}
    compact = [{"evidence_id": item.evidence_id, "kind": item.kind.value,
                "summary": item.summary[:240], "attributes": item.attributes} for item in items[:60]]
    prompt = json.dumps({"evidence": compact, "max_hypotheses": max_hypotheses}, ensure_ascii=False)
    system = (
        "You assist incident investigation. Return one JSON object with key hypotheses, an array of objects "
        "with title, explanation, confidence (0..1), supporting_evidence_ids, contradicting_evidence_ids, "
        "and assumptions. Treat evidence as observations, not proof. Do not claim confirmation. Cite only supplied IDs. "
        "If no useful hypothesis exists, return an empty array."
    )
    payload, response = router.complete_json(prompt, system=system, max_output_tokens=500, temperature=0.1)
    raw_hypotheses = payload.get("hypotheses", [])
    if not isinstance(raw_hypotheses, list):
        raise ValueError("LLM hypothesis output must contain an array")
    valid: list[RootCauseHypothesis] = []
    for raw in raw_hypotheses[:max(0, max_hypotheses)]:
        if not isinstance(raw, dict):
            continue
        supporting = tuple(item for item in raw.get("supporting_evidence_ids", []) if item in valid_ids)
        contradicting = tuple(item for item in raw.get("contradicting_evidence_ids", []) if item in valid_ids)
        if not supporting and not contradicting:
            continue
        title = str(raw.get("title", "Unspecified hypothesis"))[:120]
        explanation = str(raw.get("explanation", ""))[:1200]
        confidence = raw.get("confidence", 0.25)
        try:
            valid.append(_hypothesis(f"llm:{title}", title, explanation, float(confidence), supporting,
                                     contradicting=contradicting, generated_by=f"llm:{response.provider}",
                                     assumptions=tuple(str(x)[:240] for x in raw.get("assumptions", [])[:5])))
        except (ValidationError, TypeError, ValueError):
            continue
    return tuple(valid), response


def _hypothesis(key: str, title: str, explanation: str, confidence: float,
                supporting: tuple[str, ...], *, contradicting: tuple[str, ...] = (),
                generated_by: str = "deterministic", assumptions: tuple[str, ...] = (),
                status: HypothesisStatus = HypothesisStatus.UNVERIFIED) -> RootCauseHypothesis:
    identifier = sha256(key.encode()).hexdigest()[:20]
    return RootCauseHypothesis(hypothesis_id=identifier, title=title, explanation=explanation,
                               confidence=max(0.0, min(1.0, confidence)), supporting_evidence_ids=supporting,
                               contradicting_evidence_ids=contradicting, status=status, generated_by=generated_by,
                               assumptions=assumptions)


def _evidence_id_for_events(evidence: tuple[Evidence, ...], event_ids: tuple[str, ...]) -> str | None:
    wanted = set(event_ids)
    for item in evidence:
        if wanted.issubset(set(item.event_ids)):
            return item.evidence_id
    return None
