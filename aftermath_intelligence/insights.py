"""Cross-incident intelligence: preventive insights, failure clusters, counterfactuals.

Everything here is labelled as historical observation or hypothetical reasoning; none of it
is a prediction of future incidents.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from itertools import combinations

from pydantic import BaseModel, ConfigDict

from .memory import ActionType, MemoryRecord
from .resolution import ResolutionStatus
from .signatures import BY_ID, Role


class FailureCluster(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_ids: tuple[str, ...]
    services: tuple[str, ...]
    shared_signatures: tuple[str, ...]
    root_causes: tuple[str, ...]
    representative: str


class PreventiveInsight(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str                 # recurring_pattern | repeated_failed_mitigation | hot_service | monitoring_gap | common_root_cause
    observation: str
    recommendation: str
    incident_ids: tuple[str, ...]
    label: str = "historical observation"


def cluster_incidents(records: Iterable[MemoryRecord], *, threshold: float = 0.45) -> tuple[FailureCluster, ...]:
    """Union-find over signature Jaccard (60%) + same service (25%) + shared stack fingerprint (15%)."""
    records = tuple(records)
    parent = {r.incident_id: r.incident_id for r in records}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def sigs(r: MemoryRecord) -> set[str]:
        return {s for s in r.symptom_signatures if BY_ID.get(s) and BY_ID[s].role != Role.CHANGE}

    for a, b in combinations(records, 2):
        sa, sb = sigs(a), sigs(b)
        jac = len(sa & sb) / len(sa | sb) if sa | sb else 0.0
        score = 0.6 * jac + 0.25 * (a.service == b.service) + 0.15 * bool(set(a.stack_fingerprints) & set(b.stack_fingerprints))
        if score >= threshold:
            parent[find(b.incident_id)] = find(a.incident_id)
    groups: dict[str, list[MemoryRecord]] = defaultdict(list)
    for r in records:
        groups[find(r.incident_id)].append(r)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        shared = set.intersection(*(sigs(m) for m in members)) if members else set()
        members.sort(key=lambda m: (m.created_at is None, m.created_at))
        clusters.append(FailureCluster(
            incident_ids=tuple(m.incident_id for m in members), services=tuple(sorted({m.service for m in members})),
            shared_signatures=tuple(sorted(shared)), root_causes=tuple(m.root_cause or "unknown" for m in members),
            representative=members[-1].incident_id))
    return tuple(sorted(clusters, key=lambda c: -len(c.incident_ids)))


def preventive_insights(records: Iterable[MemoryRecord]) -> tuple[PreventiveInsight, ...]:
    records = tuple(records)
    insights: list[PreventiveInsight] = []
    for cluster in cluster_incidents(records):
        if len(cluster.incident_ids) >= 3:
            labels = ", ".join(BY_ID[s].label for s in cluster.shared_signatures if s in BY_ID) or "a shared signature"
            insights.append(PreventiveInsight(
                kind="recurring_pattern", incident_ids=cluster.incident_ids,
                observation=f"{len(cluster.incident_ids)} incidents on {', '.join(cluster.services)} share {labels}.",
                recommendation="Add a regression check or alert that catches this pattern before it pages "
                               "(e.g. a CI test or a leading-indicator alert)."))
    failed: dict[ActionType, list[str]] = defaultdict(list)
    for r in records:
        for attempt in r.attempts:
            if attempt.outcome in {ResolutionStatus.FAILED, ResolutionStatus.TEMPORARY}:
                failed[attempt.action_type].append(r.incident_id)
    for action, ids in failed.items():
        if len(set(ids)) >= 2 and action != ActionType.OTHER:
            insights.append(PreventiveInsight(
                kind="repeated_failed_mitigation", incident_ids=tuple(dict.fromkeys(ids)),
                observation=f"'{action.value.replace('_', ' ')}' was tried without lasting effect in {len(set(ids))} incidents.",
                recommendation=f"Update runbooks so {action.value.replace('_', ' ')} is not the first response for these patterns."))
    services = Counter(r.service for r in records)
    for service, count in services.most_common(3):
        if count >= 3:
            insights.append(PreventiveInsight(
                kind="hot_service", incident_ids=tuple(r.incident_id for r in records if r.service == service),
                observation=f"{service} was involved in {count} of {len(records)} recorded incidents.",
                recommendation=f"Prioritise reliability work and on-call documentation for {service}."))
    roots = Counter(s for r in records for s in r.root_cause_signatures if BY_ID.get(s) and BY_ID[s].role == Role.ORIGIN)
    for sig, count in roots.most_common(3):
        if count >= 2:
            fix = BY_ID[sig].fixes[0] if BY_ID[sig].fixes else "address the underlying defect class"
            insights.append(PreventiveInsight(
                kind="common_root_cause", incident_ids=tuple(r.incident_id for r in records if sig in r.root_cause_signatures),
                observation=f"{BY_ID[sig].label} was the confirmed cause {count} times.",
                recommendation=f"Systemic fix: {fix}."))
    slow = [r for r in records if r.time_to_resolve_minutes and r.time_to_resolve_minutes >= 120]
    if slow:
        insights.append(PreventiveInsight(
            kind="monitoring_gap", incident_ids=tuple(r.incident_id for r in slow),
            observation=f"{len(slow)} incident(s) took 2h+ to resolve.",
            recommendation="Review what evidence was missing early in these incidents and add it to dashboards/alerts."))
    return tuple(insights)


class CounterfactualAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    answer: str
    basis: tuple[str, ...]
    label: str = "hypothetical reasoning from recorded outcomes, not a historical fact"


def what_if_skipped(record: MemoryRecord, action_type: ActionType) -> CounterfactualAnswer:
    """'What if the engineer had not done X?' using only the recorded attempt outcomes."""
    question = f"What if '{action_type.value.replace('_', ' ')}' had not been done in {record.incident_id}?"
    matches = [a for a in record.attempts if a.action_type == action_type]
    if not matches:
        return CounterfactualAnswer(question=question, basis=(),
                                    answer="That action was not recorded for this incident, so there is nothing to reason from.")
    outcomes = {a.outcome for a in matches}
    basis = tuple(f"{a.description} -> {a.outcome.value}" for a in matches)
    if outcomes <= {ResolutionStatus.FAILED}:
        answer = ("It was recorded as having no effect, so skipping it would most likely not have changed the outcome; "
                  "it may have saved time.")
    elif outcomes <= {ResolutionStatus.TEMPORARY}:
        answer = ("It only gave temporary relief, so skipping it would likely have prolonged user impact until the real "
                  "fix, without changing the eventual resolution.")
    else:
        answer = ("It is recorded as (part of) the fix that worked, so without it the incident would plausibly have "
                  "continued until an equivalent fix was applied.")
    return CounterfactualAnswer(question=question, answer=answer, basis=basis)
