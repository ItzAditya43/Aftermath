"""Explainable anomaly detection without a trained model.

Each detector returns the observed value, the baseline it was compared against (when one
exists) and the method, so the UI and the LLM can show *why* something is anomalous.
A plain error is not an anomaly; an error rate far above its own baseline is.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from statistics import mean, median, pstdev

from .analysis import _fingerprint
from .models import NormalizedEvent, Severity

_FAILURE = {Severity.ERROR, Severity.CRITICAL}


class AnomalyKind(StrEnum):
    ONSET = "incident_onset"
    ERROR_RATE_SPIKE = "error_rate_spike"
    RESOURCE_SATURATION = "resource_saturation"
    RESTART_FREQUENCY = "restart_frequency"
    NEW_ERROR_SIGNATURE = "new_error_signature"
    SERVICE_ERROR_SHARE = "service_error_share"


@dataclass(frozen=True)
class AnomalyConfig:
    bucket_seconds: int = 60
    z_threshold: float = 3.0
    min_spike_count: int = 3
    saturation_ratio: float = 0.95
    min_waiting: int = 1
    restart_threshold: int = 3
    service_share_threshold: float = 0.6


@dataclass(frozen=True)
class Anomaly:
    kind: AnomalyKind
    summary: str
    severity: str                      # "low" | "medium" | "high"
    method: str
    service: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    observed: float | None = None
    baseline: float | None = None
    event_ids: tuple[str, ...] = ()
    details: dict[str, str | int | float | None] = field(default_factory=dict)


def detect_anomalies(events: Iterable[NormalizedEvent], *, baseline_events: Iterable[NormalizedEvent] = (),
                     config: AnomalyConfig | None = None) -> tuple[Anomaly, ...]:
    config = config or AnomalyConfig()
    events = tuple(events)
    baseline = tuple(baseline_events)
    found: list[Anomaly] = []
    found += _onset(events)
    found += _rate_spikes(events, baseline, config)
    found += _saturation(events, config)
    found += _restarts(events, config)
    found += _new_signatures(events, baseline)
    found += _service_share(events, config)
    return tuple(found)


def _onset(events: tuple[NormalizedEvent, ...]) -> list[Anomaly]:
    failures = sorted((e for e in events if e.severity in _FAILURE and e.timestamp), key=lambda e: e.timestamp)
    if not failures:
        return []
    first = failures[0]
    return [Anomaly(AnomalyKind.ONSET, f"First failure at {first.timestamp.isoformat()} in {first.service or 'unknown service'}: "
                    f"{first.message[:140]}", "medium", "earliest error/critical event", first.service,
                    first.timestamp, first.timestamp, event_ids=(first.event_id,))]


def _bucket(ts: datetime, width: int) -> datetime:
    return datetime.fromtimestamp(int(ts.timestamp()) // width * width, tz=timezone.utc)


def _rate_spikes(events: tuple[NormalizedEvent, ...], baseline: tuple[NormalizedEvent, ...],
                 config: AnomalyConfig) -> list[Anomaly]:
    width = config.bucket_seconds
    per_service: dict[str, Counter[datetime]] = defaultdict(Counter)
    ids: dict[tuple[str, datetime], list[str]] = defaultdict(list)
    for event in events:
        if event.severity in _FAILURE and event.timestamp:
            key = event.service or "unknown"
            bucket = _bucket(event.timestamp, width)
            per_service[key][bucket] += 1
            ids[(key, bucket)].append(event.event_id)
    baseline_rate: dict[str, float] = {}
    if baseline:
        counts: dict[str, Counter[datetime]] = defaultdict(Counter)
        for event in baseline:
            if event.severity in _FAILURE and event.timestamp:
                counts[event.service or "unknown"][_bucket(event.timestamp, width)] += 1
        for service, buckets in counts.items():
            stamps = [e.timestamp for e in baseline if e.timestamp]
            span = max(1, int((max(stamps) - min(stamps)).total_seconds() // width) + 1) if stamps else 1
            baseline_rate[service] = sum(buckets.values()) / span
    out: list[Anomaly] = []
    for service, buckets in per_service.items():
        if not buckets:
            continue
        start, end = min(buckets), max(buckets)
        series: list[tuple[datetime, int]] = []
        cursor = start
        while cursor <= end and len(series) < 10_000:
            series.append((cursor, buckets.get(cursor, 0)))
            cursor += timedelta(seconds=width)
        values = [v for _, v in series]
        if service in baseline_rate:
            ref_mean, ref_sd, method = baseline_rate[service], max(1.0, baseline_rate[service] ** 0.5), "baseline comparison (Poisson sd)"
        elif len(values) >= 5:
            med = median(values)
            mad = median(abs(v - med) for v in values) or 0.0
            ref_mean, ref_sd, method = med, max(1.0, 1.4826 * mad), "robust z-score vs in-window median"
        else:
            continue
        for stamp, value in series:
            z = (value - ref_mean) / ref_sd
            if value >= config.min_spike_count and z >= config.z_threshold:
                out.append(Anomaly(
                    AnomalyKind.ERROR_RATE_SPIKE,
                    f"{service}: {value} errors in {width}s at {stamp.isoformat()} vs baseline {ref_mean:.1f} (z={z:.1f})",
                    "high" if z >= 2 * config.z_threshold else "medium", method, service, stamp,
                    stamp + timedelta(seconds=width), float(value), round(ref_mean, 2),
                    tuple(ids[(service, stamp)][:50]), {"z_score": round(z, 2)}))
    return out


def _saturation(events: tuple[NormalizedEvent, ...], config: AnomalyConfig) -> list[Anomaly]:
    out: list[Anomaly] = []
    seen: set[tuple[str | None, str]] = set()
    for event in events:
        metrics = event.metadata.get("metrics") if isinstance(event.metadata, dict) else None
        if not isinstance(metrics, dict):
            continue
        reasons: list[tuple[str, float, float]] = []
        active = metrics.get("active")
        capacity = metrics.get("active_max") or metrics.get("total") or metrics.get("max") or metrics.get("maximumpoolsize")
        waiting = metrics.get("waiting") or metrics.get("pending") or metrics.get("queued") or 0
        if active is not None and capacity and active / capacity >= config.saturation_ratio:
            reasons.append(("pool", float(active), float(capacity)))
        if waiting and waiting >= config.min_waiting and metrics.get("idle", 1) == 0:
            reasons.append(("waiters", float(waiting), 0.0))
        for key in ("memory_percent", "mem_percent", "cpu_percent", "cpu", "disk_percent"):
            value = metrics.get(key)
            if value is not None and value >= 95:
                reasons.append((key, float(value), 100.0))
        for name, observed, cap in reasons:
            key = (event.service, name)
            if key in seen:
                continue
            seen.add(key)
            detail = (f"{int(observed)}/{int(cap)} in use" if name == "pool" else
                      f"{int(observed)} waiting with 0 idle" if name == "waiters" else f"{name}={observed:g}")
            out.append(Anomaly(AnomalyKind.RESOURCE_SATURATION, f"{event.service or 'service'} resource saturated: {detail}",
                               "high", "threshold on reported metrics", event.service, event.timestamp, event.timestamp,
                               observed, cap or None, (event.event_id,), {"resource": name}))
    return out


def _restarts(events: tuple[NormalizedEvent, ...], config: AnomalyConfig) -> list[Anomaly]:
    restarts: dict[str | None, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        text = event.message.lower()
        if event.event_type == "container_event" and any(w in text for w in ("restart", "back-off", "crashloop", "oomkilled", "killing")):
            restarts[event.service].append(event)
    return [Anomaly(AnomalyKind.RESTART_FREQUENCY, f"{service or 'container'} restarted/killed {len(items)} times",
                    "high", f">= {config.restart_threshold} lifecycle restarts in window", service,
                    min((e.timestamp for e in items if e.timestamp), default=None),
                    max((e.timestamp for e in items if e.timestamp), default=None), float(len(items)), None,
                    tuple(e.event_id for e in items[:50]))
            for service, items in restarts.items() if len(items) >= config.restart_threshold]


def _new_signatures(events: tuple[NormalizedEvent, ...], baseline: tuple[NormalizedEvent, ...]) -> list[Anomaly]:
    if not baseline:
        return []
    known = {_fingerprint(e) for e in baseline if e.severity in _FAILURE}
    fresh: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        if event.severity in _FAILURE:
            fp = _fingerprint(event)
            if fp not in known:
                fresh[fp].append(event)
    return [Anomaly(AnomalyKind.NEW_ERROR_SIGNATURE, f"Error not seen in baseline: {items[0].message[:140]}", "medium",
                    "fingerprint absent from baseline window", items[0].service, items[0].timestamp, items[-1].timestamp,
                    float(len(items)), 0.0, tuple(e.event_id for e in items[:20]))
            for items in fresh.values()]


def _service_share(events: tuple[NormalizedEvent, ...], config: AnomalyConfig) -> list[Anomaly]:
    counts = Counter(e.service for e in events if e.severity in _FAILURE and e.service)
    total = sum(counts.values())
    if len(counts) < 2 or total < 5:
        return []
    service, top = counts.most_common(1)[0]
    share = top / total
    if share < config.service_share_threshold:
        return []
    others = [c for s, c in counts.items() if s != service]
    return [Anomaly(AnomalyKind.SERVICE_ERROR_SHARE, f"{service} produced {share:.0%} of errors across {len(counts)} services",
                    "medium", "service share of total errors", service, observed=round(share, 3),
                    baseline=round(mean(others) / total, 3) if others else None,
                    details={"stdev_others": round(pstdev(others), 2) if len(others) > 1 else None})]
