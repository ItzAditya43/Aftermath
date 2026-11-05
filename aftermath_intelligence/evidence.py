from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from .analysis import AnalysisReport
from .correlation import CorrelationReport
from .models import NormalizedEvent, Severity
from .redaction import redact_sensitive_text, redact_sensitive_data


class EvidenceKind(StrEnum):
    OBSERVATION = "observation"
    ERROR_PATTERN = "error_pattern"
    TEMPORAL_SPIKE = "temporal_spike"
    CORRELATION = "correlation"
    TOOL_RESULT = "tool_result"
    DEPLOYMENT = "deployment"
    HISTORICAL = "historical"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    kind: EvidenceKind
    summary: str
    source: str
    event_ids: tuple[str, ...] = ()
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EvidenceLedger:
    """Deduplicated, append-only evidence collection for one investigation."""

    def __init__(self, items: list[Evidence] | None = None):
        self._items: dict[str, Evidence] = {item.evidence_id: item for item in items or []}

    @property
    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items.values())

    def add(self, item: Evidence) -> Evidence:
        self._items.setdefault(item.evidence_id, item)
        return self._items[item.evidence_id]

    def by_id(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def add_event_facts(self, events: list[NormalizedEvent] | tuple[NormalizedEvent, ...]) -> None:
        for event in events:
            if event.severity not in {Severity.WARNING, Severity.ERROR, Severity.CRITICAL} and event.event_type == "log":
                continue
            summary = redact_sensitive_text(f"{event.severity.value.upper()}: {event.message[:240]}")
            self.add(_evidence(EvidenceKind.OBSERVATION, summary, event.source, (event.event_id,), {
                "service": event.service, "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "severity": event.severity.value, "exception_type": event.exception_type,
            }))

    def add_analysis(self, report: AnalysisReport) -> None:
        for group in report.error_groups:
            self.add(_evidence(EvidenceKind.ERROR_PATTERN,
                               f"{group.count} matching errors: {group.fingerprint[:180]}", "deterministic_log_analysis",
                               group.event_ids, {"count": group.count, "services": ",".join(group.services),
                                                 "exception_type": group.exception_type}))
        for spike in report.temporal_spikes:
            self.add(_evidence(EvidenceKind.TEMPORAL_SPIKE,
                               f"Error bucket at {spike.start} contains {spike.count} errors (baseline {spike.baseline_count}).",
                               "deterministic_temporal_analysis", spike.event_ids, {"count": spike.count,
                                                                        "baseline_count": spike.baseline_count,
                                                                        "start": spike.start}))
        for burst in report.error_bursts:
            self.add(_evidence(EvidenceKind.TEMPORAL_SPIKE,
                               f"Error burst from {burst.start} through {burst.end}: {burst.count} errors.",
                               "deterministic_burst_analysis", (), {"count": burst.count,
                                                                     "bucket_count": burst.bucket_count}))
        for pair in report.error_cooccurrences:
            self.add(_evidence(EvidenceKind.ERROR_PATTERN,
                               f"Error patterns co-occurred in {pair.bucket_count} time buckets.",
                               "deterministic_cooccurrence_analysis", pair.event_ids,
                               {"left_fingerprint": pair.left_fingerprint,
                                "right_fingerprint": pair.right_fingerprint,
                                "bucket_count": pair.bucket_count}))
        for sequence in report.repeated_failure_sequences:
            self.add(_evidence(EvidenceKind.ERROR_PATTERN,
                               f"Repeated failure sequence in service {sequence.service}, observed {sequence.count} times.",
                               "deterministic_sequence_analysis", sequence.event_ids,
                               {"service": sequence.service, "count": sequence.count,
                                "fingerprints": " -> ".join(sequence.fingerprints)}))

    def add_correlations(self, report: CorrelationReport) -> None:
        for edge in report.edges:
            self.add(_evidence(EvidenceKind.CORRELATION, edge.rationale, "deterministic_correlation",
                               (edge.source_event_id, edge.target_event_id), {"relation": edge.relation,
                                                                            "confidence": edge.confidence,
                                                                            "delta_seconds": edge.time_delta_seconds}))

    def add_tool_result(self, *, tool_name: str, summary: str, source_event_ids: tuple[str, ...] = (),
                        attributes: dict[str, str | int | float | bool | None] | None = None) -> Evidence:
        safe = redact_sensitive_data(attributes or {})
        return self.add(_evidence(EvidenceKind.TOOL_RESULT, redact_sensitive_text(summary), tool_name,
                                 source_event_ids, safe))

    def add_historical_match(self, *, incident_id: str, similarity: float,
                             shared_fingerprints: tuple[str, ...], contradicts: bool) -> Evidence:
        return self.add(_evidence(
            EvidenceKind.HISTORICAL,
            f"Historical incident {incident_id} has a similarity score of {similarity:.2f}.",
            "historical_context_adapter", (),
            {"incident_id": incident_id, "similarity": similarity,
             "shared_fingerprints": "; ".join(shared_fingerprints[:5]),
             "contradicts_verified_conclusion": contradicts},
        ))


def _evidence(kind: EvidenceKind, summary: str, source: str, event_ids: tuple[str, ...],
              attributes: dict[str, str | int | float | bool | None]) -> Evidence:
    material = "\0".join((kind.value, source, summary, *sorted(event_ids)))
    identifier = sha256(material.encode()).hexdigest()[:24]
    return Evidence(evidence_id=identifier, kind=kind, summary=summary, source=source,
                    event_ids=event_ids, attributes=attributes)
