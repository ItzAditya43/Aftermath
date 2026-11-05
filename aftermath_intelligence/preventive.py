from __future__ import annotations

from dataclasses import dataclass

from .similarity import HistoricalIncident


@dataclass(frozen=True)
class PreventiveSignal:
    fingerprint: str
    incident_count: int
    services: tuple[str, ...]
    verified_resolution_count: int
    recommendation: str


def derive_preventive_signals(history: tuple[HistoricalIncident, ...] | list[HistoricalIncident], *,
                              minimum_incidents: int = 2) -> tuple[PreventiveSignal, ...]:
    appearances: dict[str, list[HistoricalIncident]] = {}
    for incident in history:
        for fingerprint in incident.fingerprints:
            appearances.setdefault(fingerprint, []).append(incident)
    result = []
    for fingerprint, incidents in appearances.items():
        if len(incidents) < minimum_incidents:
            continue
        services = tuple(sorted({service for case in incidents for service in case.services}))
        verified = sum(case.resolution_status.value == "verified" for case in incidents)
        result.append(PreventiveSignal(
            fingerprint=fingerprint, incident_count=len(incidents), services=services,
            verified_resolution_count=verified,
            recommendation="Review the recurring failure pattern and document preventive controls; prior fixes are not assumed to transfer.",
        ))
    return tuple(sorted(result, key=lambda item: (-item.incident_count, item.fingerprint)))
