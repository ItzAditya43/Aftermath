from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from statistics import median

from .models import NormalizedEvent, Severity
from .redaction import redact_sensitive_text


def _fingerprint(event: NormalizedEvent) -> str:
    text = event.stack_trace or event.message
    text = redact_sensitive_text(text)
    text = re.sub(r"\d{4}-\d\d-\d\d[T ][0-9:.]+(?:Z|[+-]\d\d:?\d\d)?", "<time>", text)
    text = re.sub(r"\b0x[0-9a-fA-F]+\b", "<addr>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return re.sub(r"[^a-z0-9._<> ]", "", text)[:240]


@dataclass(frozen=True)
class ErrorGroup:
    fingerprint: str
    count: int
    event_ids: tuple[str, ...]
    services: tuple[str, ...]
    exception_type: str | None


@dataclass(frozen=True)
class Spike:
    start: str
    count: int
    baseline_count: int
    event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RareError:
    fingerprint: str
    event_id: str
    service: str | None


@dataclass(frozen=True)
class ErrorCooccurrence:
    left_fingerprint: str
    right_fingerprint: str
    bucket_count: int
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class ErrorBurst:
    start: str
    end: str
    count: int
    bucket_count: int


@dataclass(frozen=True)
class FailureSequence:
    service: str
    fingerprints: tuple[str, ...]
    count: int
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisReport:
    event_count: int
    severity_counts: dict[str, int]
    service_error_counts: dict[str, int]
    error_groups: tuple[ErrorGroup, ...]
    temporal_spikes: tuple[Spike, ...]
    rare_errors: tuple[RareError, ...] = ()
    error_cooccurrences: tuple[ErrorCooccurrence, ...] = ()
    error_bursts: tuple[ErrorBurst, ...] = ()
    repeated_failure_sequences: tuple[FailureSequence, ...] = ()


def analyze_events(events: tuple[NormalizedEvent, ...] | list[NormalizedEvent], *, bucket_minutes: int = 5,
                   sequence_window_seconds: int = 300) -> AnalysisReport:
    severity = Counter(event.severity.value for event in events)
    service_errors: Counter[str] = Counter()
    grouped: dict[str, list[NormalizedEvent]] = defaultdict(list)
    buckets: Counter[datetime] = Counter()
    event_buckets: dict[datetime, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        if event.severity in {Severity.ERROR, Severity.CRITICAL}:
            service_errors[event.service or "unknown"] += 1
            grouped[_fingerprint(event)].append(event)
            if event.timestamp:
                width = max(1, bucket_minutes)
                bucket_seconds = width * 60
                epoch_bucket = int(event.timestamp.timestamp()) // bucket_seconds * bucket_seconds
                start = datetime.fromtimestamp(epoch_bucket, tz=timezone.utc)
                buckets[start] += 1
                event_buckets[start].append(event)
    groups = tuple(sorted((ErrorGroup(
        fingerprint=key, count=len(items), event_ids=tuple(e.event_id for e in items),
        services=tuple(sorted({e.service for e in items if e.service})),
        exception_type=next((e.exception_type for e in items if e.exception_type), None),
    ) for key, items in grouped.items()), key=lambda item: (-item.count, item.fingerprint)))
    values = list(buckets.values())
    baseline = median(values) if values else 0.0
    mad = median([abs(value - baseline) for value in values]) if values else 0.0
    # Don't infer a spike from a single populated bucket; there is no baseline yet.
    spikes = tuple(Spike(start=stamp.isoformat(), count=count, baseline_count=round(baseline),
                         event_ids=tuple(event.event_id for event in event_buckets[stamp]))
                   for stamp, count in sorted(buckets.items())
                   if len(values) >= 3 and count >= max(3, baseline + 3 * max(1, mad)))
    rare = tuple(RareError(fingerprint=group.fingerprint, event_id=group.event_ids[0],
                           service=group.services[0] if group.services else None)
                 for group in groups if group.count == 1)
    pair_buckets: dict[tuple[str, str], list[tuple[datetime, tuple[str, ...]]]] = defaultdict(list)
    for bucket, bucket_events in event_buckets.items():
        frequency = Counter(_fingerprint(event) for event in bucket_events)
        distinct = [fingerprint for fingerprint, _ in frequency.most_common(40)]
        for left, right in combinations(distinct, 2):
            ids = [event.event_id for event in bucket_events
                   if _fingerprint(event) in {left, right}]
            pair_buckets[(left, right)].append((bucket, tuple(ids)))
    cooccurrences = tuple(ErrorCooccurrence(left_fingerprint=pair[0], right_fingerprint=pair[1],
                                            bucket_count=len(buckets_for_pair),
                                            event_ids=tuple(dict.fromkeys(event_id for _, ids in buckets_for_pair for event_id in ids)))
                          for pair, buckets_for_pair in sorted(pair_buckets.items()))
    width = max(1, bucket_minutes)
    busy = [(stamp, count) for stamp, count in sorted(buckets.items()) if count >= 3]
    burst_groups: list[list[tuple[datetime, int]]] = []
    for item in busy:
        if not burst_groups or (item[0] - burst_groups[-1][-1][0]).total_seconds() > width * 60:
            burst_groups.append([item])
        else:
            burst_groups[-1].append(item)
    bursts = tuple(ErrorBurst(start=items[0][0].isoformat(), end=items[-1][0].isoformat(),
                              count=sum(count for _, count in items), bucket_count=len(items))
                   for items in burst_groups)
    failures_by_service: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        if event.service:
            failures_by_service[event.service].append(event)
    sequence_counts: dict[tuple[str, tuple[str, ...]], list[tuple[str, str]]] = defaultdict(list)
    for service, service_events in failures_by_service.items():
        ordered = sorted(service_events, key=lambda event: (event.timestamp is None, event.timestamp, event.event_id))
        for first, second in zip(ordered, ordered[1:], strict=False):
            if (first.timestamp and second.timestamp
                    and (second.timestamp - first.timestamp).total_seconds() > max(1, sequence_window_seconds)):
                continue
            if first.severity in {Severity.ERROR, Severity.CRITICAL} or second.severity in {Severity.ERROR, Severity.CRITICAL}:
                signature = (service, (_fingerprint(first), _fingerprint(second)))
                sequence_counts[signature].append((first.event_id, second.event_id))
    sequences = tuple(FailureSequence(
        service=service, fingerprints=signature, count=len(pairs),
        event_ids=tuple(dict.fromkeys(event_id for pair in pairs for event_id in pair)))
        for (service, signature), pairs in sorted(sequence_counts.items()) if len(pairs) >= 2)
    return AnalysisReport(len(events), dict(severity), dict(service_errors), groups, spikes,
                          rare, cooccurrences, bursts, sequences)
