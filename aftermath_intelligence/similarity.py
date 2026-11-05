from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .analysis import _fingerprint
from .models import NormalizedEvent
from .resolution import ResolutionRecord, ResolutionStatus


@dataclass(frozen=True)
class HistoricalIncident:
    incident_id: str
    fingerprints: frozenset[str]
    services: frozenset[str]
    verified_root_causes: frozenset[str] = frozenset()
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED


@dataclass(frozen=True)
class SimilarIncident:
    incident_id: str
    similarity: float
    shared_fingerprints: tuple[str, ...]
    shared_services: tuple[str, ...]
    contradicts_verified_conclusion: bool


def historical_record(incident_id: str, events: tuple[NormalizedEvent, ...] | list[NormalizedEvent],
                      resolution: ResolutionRecord | None = None,
                      verified_root_causes: tuple[str, ...] = ()) -> HistoricalIncident:
    failures = [event for event in events if event.severity.value in {"error", "critical"}]
    return HistoricalIncident(
        incident_id=incident_id,
        fingerprints=frozenset(_fingerprint(event) for event in failures),
        services=frozenset(event.service for event in events if event.service),
        verified_root_causes=frozenset(verified_root_causes if resolution and resolution.status == ResolutionStatus.VERIFIED else ()),
        resolution_status=resolution.status if resolution else ResolutionStatus.UNRESOLVED,
    )


def find_similar_incidents(events: tuple[NormalizedEvent, ...] | list[NormalizedEvent],
                           history: tuple[HistoricalIncident, ...] | list[HistoricalIncident], *,
                           threshold: float = 0.1,
                           current_verified_root_causes: tuple[str, ...] = ()) -> tuple[SimilarIncident, ...]:
    current_fingerprints = {_fingerprint(e) for e in events if e.severity.value in {"error", "critical"}}
    current_services = {e.service for e in events if e.service}
    matches: list[SimilarIncident] = []
    for case in history:
        fp_union = current_fingerprints | set(case.fingerprints)
        svc_union = current_services | set(case.services)
        fp_score = len(current_fingerprints & case.fingerprints) / len(fp_union) if fp_union else 0
        svc_score = len(current_services & case.services) / len(svc_union) if svc_union else 0
        score = 0.8 * fp_score + 0.2 * svc_score
        if score < threshold:
            continue
        matches.append(SimilarIncident(
            incident_id=case.incident_id, similarity=round(score, 4),
            shared_fingerprints=tuple(sorted(current_fingerprints & case.fingerprints)),
            shared_services=tuple(sorted(current_services & case.services)),
            contradicts_verified_conclusion=bool(current_verified_root_causes and case.verified_root_causes
                                                   and not set(current_verified_root_causes) & case.verified_root_causes),
        ))
    return tuple(sorted(matches, key=lambda match: (-match.similarity, match.incident_id)))


def detect_historical_contradictions(history: tuple[HistoricalIncident, ...] | list[HistoricalIncident], *,
                                     minimum_overlap: float = 0.2) -> tuple[tuple[str, str, float], ...]:
    """Flag similar, verified cases that recorded incompatible root-cause labels."""
    conflicts: list[tuple[str, str, float]] = []
    for left, right in combinations(history, 2):
        if not left.verified_root_causes or not right.verified_root_causes:
            continue
        if left.verified_root_causes & right.verified_root_causes:
            continue
        union = left.fingerprints | right.fingerprints
        overlap = len(left.fingerprints & right.fingerprints) / len(union) if union else 0
        if overlap >= minimum_overlap:
            conflicts.append((left.incident_id, right.incident_id, round(overlap, 4)))
    return tuple(conflicts)


def cluster_historical_incidents(history: tuple[HistoricalIncident, ...] | list[HistoricalIncident], *,
                                 threshold: float = 0.35) -> tuple[tuple[str, ...], ...]:
    """Build deterministic connected components over similar historical cases."""
    parent = {case.incident_id: case.incident_id for case in history}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in combinations(history, 2):
        fingerprint_union = left.fingerprints | right.fingerprints
        service_union = left.services | right.services
        fp = len(left.fingerprints & right.fingerprints) / len(fingerprint_union) if fingerprint_union else 0
        svc = len(left.services & right.services) / len(service_union) if service_union else 0
        score = .8 * fp + .2 * svc
        if score >= threshold:
            root_left, root_right = find(left.incident_id), find(right.incident_id)
            if root_left != root_right:
                parent[root_right] = root_left
    clusters: dict[str, list[str]] = {}
    for incident_id in parent:
        clusters.setdefault(find(incident_id), []).append(incident_id)
    return tuple(sorted((tuple(sorted(ids)) for ids in clusters.values() if len(ids) > 1), key=lambda ids: ids))
