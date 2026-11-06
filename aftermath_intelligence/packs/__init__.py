"""Incident packs: domain pattern libraries that extend the core catalog beyond application incidents.

The investigation pipeline (evidence, failure chain, ranked hypotheses, memory lessons, approval-gated
plan) is domain-neutral; only the patterns and playbooks change per field. A pack is a named tuple of
`Signature`s with the same contract as the core catalog: read-only checks, temporary mitigations,
permanent fixes, actions to avoid, and the signatures each one commonly causes.

Select packs with ``AFTERMATH_PACKS`` (comma list of pack ids, ``all`` (default) or ``none``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..signatures import Signature


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    description: str
    signatures: tuple["Signature", ...]


def all_packs() -> tuple[Pack, ...]:
    from .data_pipeline import PACK as DATA
    from .industrial import PACK as INDUSTRIAL
    from .kubernetes import PACK as KUBERNETES
    from .network import PACK as NETWORK
    from .security import PACK as SECURITY
    return (SECURITY, NETWORK, KUBERNETES, DATA, INDUSTRIAL)


def selected_pack_ids(raw: str | None = None) -> tuple[str, ...]:
    available = tuple(p.id for p in all_packs())
    raw = (os.getenv("AFTERMATH_PACKS", "all") if raw is None else raw).strip().lower()
    if raw in {"", "all"}:
        return available
    if raw == "none":
        return ()
    wanted = tuple(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))
    unknown = [p for p in wanted if p not in available]
    if unknown:
        raise ValueError(f"unknown incident pack(s): {', '.join(unknown)} (available: {', '.join(available)})")
    return wanted


def enabled_signatures(raw: str | None = None) -> tuple["Signature", ...]:
    chosen = set(selected_pack_ids(raw))
    return tuple(s for pack in all_packs() if pack.id in chosen for s in pack.signatures)


__all__ = ["Pack", "all_packs", "enabled_signatures", "selected_pack_ids"]
