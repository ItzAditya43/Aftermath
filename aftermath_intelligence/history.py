from __future__ import annotations

from typing import Protocol

from .models import NormalizedEvent
from .similarity import HistoricalIncident, SimilarIncident, find_similar_incidents


class HistoricalContextAdapter(Protocol):
    """Memory owner's interface; implementation and persistence remain external."""

    def retrieve_incidents(self, events: tuple[NormalizedEvent, ...], *, limit: int = 10) -> tuple[HistoricalIncident, ...]: ...


def retrieve_similar_context(events: tuple[NormalizedEvent, ...], adapter: HistoricalContextAdapter,
                             *, limit: int = 10) -> tuple[SimilarIncident, ...]:
    cases = adapter.retrieve_incidents(events, limit=limit)
    return find_similar_incidents(events, cases)
