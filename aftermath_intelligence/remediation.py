from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .hypotheses import RootCauseHypothesis
from .tools import RiskLevel


class RecommendationStatus(StrEnum):
    RECOMMENDED = "recommended"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class RemediationRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation_id: str
    title: str
    rationale: str
    steps: tuple[str, ...]
    risk: RiskLevel
    requires_approval: bool = True
    status: RecommendationStatus = RecommendationStatus.RECOMMENDED
    hypothesis_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()


def recommend_remediation(hypotheses: tuple[RootCauseHypothesis, ...] | list[RootCauseHypothesis],
                          *, max_recommendations: int = 5) -> tuple[RemediationRecommendation, ...]:
    """Produce advice only. This module never executes a remediation step."""
    output: list[RemediationRecommendation] = []
    for hypothesis in hypotheses:
        text = f"{hypothesis.title} {hypothesis.explanation}".lower()
        steps: tuple[str, ...]
        title: str
        if "deployment" in text or "release" in text:
            title = "Validate and, if confirmed, roll back the suspected release"
            steps = (
                "Compare the release timestamp and changed services with the first failing events.",
                "Check health and error rates for the affected service and its dependencies.",
                "If the regression is confirmed, request an approved rollback through the deployment owner.",
            )
        elif "database" in text or "sql" in text:
            title = "Check database health and connection-pool pressure"
            steps = (
                "Inspect connection counts, pool wait time, query latency, and database health.",
                "Check for locks or recent schema/query changes tied to the first error timestamp.",
                "Review any pool, query, or database configuration change before applying it.",
            )
        elif "resource" in text or "memory" in text or "capacity" in text:
            title = "Check resource pressure before changing capacity"
            steps = (
                "Inspect container and host memory, CPU, disk, and restart metrics for the incident window.",
                "Compare current limits with recent peak usage and neighboring services.",
                "Propose a limit or capacity change for review; apply only after approval.",
            )
        elif "dependency" in text or "network" in text or "upstream" in text:
            title = "Verify the dependency path and service health"
            steps = (
                "Check dependency health, request latency, timeout rate, and recent connectivity changes.",
                "Compare caller errors with dependency-side logs for the same UTC interval.",
                "Use an approved retry, failover, or configuration change only after confirming the failure mode.",
            )
        elif "authentication" in text or "configuration" in text:
            title = "Validate credentials and configuration provenance"
            steps = (
                "Compare the active configuration version with the last known healthy version.",
                "Check credential expiry and access scope without exposing secret values.",
                "Have the owning engineer review any credential or configuration replacement.",
            )
        else:
            title = f"Gather evidence for: {hypothesis.title}"
            steps = ("Run read-only diagnostics that test this hypothesis.",
                     "Compare the result with the supporting and contradicting evidence before changing state.")
        output.append(RemediationRecommendation(
            recommendation_id=f"remediate:{hypothesis.hypothesis_id}", title=title,
            rationale=hypothesis.explanation, steps=steps, risk=RiskLevel.MEDIUM,
            requires_approval=True, hypothesis_ids=(hypothesis.hypothesis_id,),
            evidence_ids=hypothesis.supporting_evidence_ids,
            cautions=("This is an unverified hypothesis; do not treat the recommendation as authorization to act.",),
        ))
        if len(output) >= max_recommendations:
            break
    return tuple(output)


# --------------------------------------------------------------------------- evidence-driven plan (v2)

class RemediationKind(StrEnum):
    VERIFY = "verify"                    # read-only confirmation before acting
    MITIGATION = "immediate_mitigation"  # temporary relief
    ROOT_CAUSE_FIX = "root_cause_fix"
    PREVENTIVE = "preventive"
    MONITORING = "monitoring_improvement"
    INVESTIGATE = "further_investigation"


class RemediationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: RemediationKind
    description: str
    target_service: str | None = None
    hypothesis_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    expected_effect: str = ""
    risk: RiskLevel = RiskLevel.READ_ONLY
    requires_approval: bool = False
    temporary: bool = False
    verification: str = ""
    rollback: str | None = None
    source: str = "playbook"             # playbook | memory | llm


class RemediationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: tuple[RemediationStep, ...]
    warnings: tuple[str, ...] = ()
    blocked_actions: tuple[str, ...] = ()  # recommendations dropped because they failed before


_STATE_CHANGING = {
    "restart": RiskLevel.MEDIUM, "scale_out": RiskLevel.MEDIUM, "raise_limit": RiskLevel.MEDIUM,
    "rollback": RiskLevel.HIGH, "redeploy": RiskLevel.MEDIUM, "config_change": RiskLevel.MEDIUM,
    "code_fix": RiskLevel.MEDIUM, "failover": RiskLevel.HIGH, "cache_purge": RiskLevel.LOW,
    "kill_session": RiskLevel.HIGH, "pause_job": RiskLevel.LOW, "wait": RiskLevel.READ_ONLY, "other": RiskLevel.MEDIUM,
}


def plan_remediation(hypotheses, *, lessons=(), service: str | None = None, severity: str | None = None,
                     max_steps: int = 7) -> RemediationPlan:
    """Build an ordered plan from the leading hypotheses, the signature playbooks and memory lessons.

    Never executes anything. State-changing steps are marked `requires_approval`. Actions that
    failed or only gave temporary relief in similar past incidents are moved to warnings.
    """
    from .memory import ActionType, classify_action
    from .rca import HypothesisState
    from .signatures import BY_ID

    avoid = {lesson.action_type: lesson for lesson in lessons if lesson.kind == "avoid_action" and lesson.action_type}
    proven = [lesson for lesson in lessons if lesson.kind == "proven_fix"]
    steps: list[RemediationStep] = []
    warnings: list[str] = []
    generic: list[str] = []          # catalog advice; ranked after this incident's own history
    blocked: list[str] = []

    def add(kind: RemediationKind, text: str, *, h=None, temporary=False, source="playbook", reason="",
            expected="", verification="", rollback=None) -> None:
        if any(s.description == text for s in steps):
            return
        action = classify_action(text) if kind != RemediationKind.VERIFY else ActionType.WAIT
        if source == "memory" and action in avoid:
            warnings.append(f"\"{text[:120]}\" includes a {action.value.replace('_', ' ')} step; "
                            f"on its own that did not hold before ({avoid[action].message[:120]})")
        elif kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX} and action in avoid:
            lesson = avoid[action]
            blocked.append(text)
            warnings.append(f"Skipped \"{text}\": {lesson.message}")
            return
        risk = RiskLevel.READ_ONLY if kind in {RemediationKind.VERIFY, RemediationKind.INVESTIGATE} else _STATE_CHANGING.get(action.value, RiskLevel.MEDIUM)
        if kind in {RemediationKind.PREVENTIVE, RemediationKind.MONITORING}:
            risk = RiskLevel.LOW
        steps.append(RemediationStep(
            id=f"R{len(steps) + 1}", kind=kind, description=text, target_service=(h.affected_service if h else service),
            hypothesis_id=h.id if h else None, evidence_ids=h.supporting_evidence_ids[:5] if h else (),
            reason=reason or (f"Addresses: {h.title}" if h else ""), expected_effect=expected, risk=risk,
            # Anything that changes system state needs a human, whatever its risk level.
            requires_approval=kind in {RemediationKind.MITIGATION, RemediationKind.ROOT_CAUSE_FIX}
            or risk not in {RiskLevel.READ_ONLY, RiskLevel.LOW}, temporary=temporary,
            verification=verification or "Watch error rate and latency for 15 minutes; confirm the signature stops appearing",
            rollback=rollback, source=source))

    active = [h for h in hypotheses if h.status not in {HypothesisState.REJECTED}]
    leader = active[0] if active else None
    if leader is None or leader.category.value == "unknown":
        add(RemediationKind.INVESTIGATE, "Collect logs from the failing service starting 10 minutes before the alert", h=leader)
        add(RemediationKind.INVESTIGATE, "List deploys and configuration changes in the last 2 hours for the affected services", h=leader)
        add(RemediationKind.INVESTIGATE, "Check saturation metrics: CPU, memory, connections, queue depth", h=leader)
    for h in active[:2]:
        signature = BY_ID.get(h.signature_id or "")
        for check in (h.verification_steps or (signature.checks if signature else ()))[:2]:
            add(RemediationKind.VERIFY, check, h=h, reason=f"Confirms or rules out: {h.title}")
        if h is not leader:
            continue
        if signature:
            for mitigation in signature.mitigations[:2]:
                add(RemediationKind.MITIGATION, mitigation, h=h, temporary=True,
                    expected="Temporary relief while the root cause is fixed")
            for fix in signature.fixes[:2]:
                add(RemediationKind.ROOT_CAUSE_FIX, fix, h=h, expected="Removes the failure mechanism")
            for note in signature.avoid:
                generic.append(f"Avoid: {note}")
    for lesson in proven[:2]:
        text = lesson.message.split(" was resolved by: ", 1)[-1]
        add(RemediationKind.ROOT_CAUSE_FIX, f"Apply the fix that resolved {lesson.incident_ids[0]}: {text}", h=leader,
            source="memory", reason=f"Verified fix for a similar incident ({lesson.incident_ids[0]})",
            expected="Resolved the matching incident previously; confirm the same mechanism first")
    if leader and leader.category.value != "unknown":
        add(RemediationKind.MONITORING, f"Alert on the leading indicator for '{leader.title}' before it causes user-facing errors", h=leader)
    # Every action that failed or only helped briefly in a similar incident is a warning, even when this
    # plan never proposed it: responders reach for restarts and scale-outs on their own.
    warned = {classify_action(text) for text in blocked}
    warned |= {action for action, lesson in avoid.items() if any(lesson.message[:60] in w for w in warnings)}
    for action, lesson in avoid.items():
        if action not in warned:
            warnings.append(f"Avoid: {lesson.message}")
    for lesson in lessons:
        if lesson.kind == "avoid_action" and lesson.action_type is None:
            warnings.append(f"Avoid: {lesson.message}")
    for lesson in lessons:
        if lesson.kind == "contradiction":
            warnings.append(lesson.message)
    warnings.extend(generic)
    if severity == "SEV1":
        warnings.insert(0, "SEV1: post a status update before applying changes")
    order = [RemediationKind.VERIFY, RemediationKind.INVESTIGATE, RemediationKind.MITIGATION,
             RemediationKind.ROOT_CAUSE_FIX, RemediationKind.PREVENTIVE, RemediationKind.MONITORING]
    steps.sort(key=lambda s: order.index(s.kind))
    steps = [s.model_copy(update={"id": f"R{i + 1}"}) for i, s in enumerate(steps[:max_steps])]
    return RemediationPlan(steps=tuple(steps), warnings=tuple(dict.fromkeys(warnings)), blocked_actions=tuple(blocked))
