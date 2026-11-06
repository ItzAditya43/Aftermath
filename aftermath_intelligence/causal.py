"""Causal event graph and failure-chain classification.

Edges are typed so the UI and the reasoning layer never confuse them:

* ``observed_precedes``   - A was observed before B (timestamps only).
* ``known_dependency``    - service A depends on service B (supplied topology).
* ``inferred_causes``     - catalog knowledge says A can cause B *and* the timeline allows it.
* ``change_precedes``     - a deploy / config / migration happened before a failure signature.

Only ``inferred_causes`` and ``change_precedes`` express causation, and both stay inferences
until a hypothesis is verified.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from .signatures import BY_ID, Role, SignatureMatch

# How far a cause's first sighting may trail its effect and still be plausible (log skew, sampling).
CLOCK_TOLERANCE = timedelta(seconds=90)


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: str                 # signature | service | change | hypothesis | anomaly
    label: str
    role: str | None = None   # root_candidate | trigger | mechanism | contributing | symptom
    service: str | None = None
    first_seen: str | None = None
    evidence_ids: tuple[str, ...] = ()


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    relation: str
    basis: str                # "observed" | "known" | "inferred"
    rationale: str
    lag_seconds: float | None = None


class CausalGraph(BaseModel):
    """Frontend-renderable graph: plain nodes + edges."""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()


@dataclass(frozen=True)
class FailureChain:
    root_candidates: tuple[str, ...]     # signature ids, most likely first
    triggers: tuple[str, ...]            # change signatures that precede the chain
    mechanisms: tuple[str, ...]
    contributing: tuple[str, ...]
    symptoms: tuple[str, ...]
    explains: dict[str, tuple[str, ...]] = field(default_factory=dict)  # sig -> downstream sigs it explains
    unexplained_symptoms: tuple[str, ...] = ()


def _reachable(start: str, present: set[str]) -> set[str]:
    """Signatures present in this incident that `start` can lead to via catalog edges."""
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        signature = BY_ID.get(current)
        for nxt in signature.causes if signature else ():
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen & present


def analyze_chain(matches: Iterable[SignatureMatch]) -> FailureChain:
    by_id = {m.id: m for m in matches}
    present = set(by_id)
    explains = {sig: tuple(sorted(_reachable(sig, present) - {sig})) for sig in present}
    explained_by: dict[str, set[str]] = {sig: set() for sig in present}
    for cause, effects in explains.items():
        for effect in effects:
            if _plausible_order(by_id[cause], by_id[effect]):
                explained_by[effect].add(cause)

    def role(sig: str) -> Role:
        return by_id[sig].signature.role

    origins = [s for s in present if role(s) == Role.ORIGIN]
    mechanisms = [s for s in present if role(s) == Role.MECHANISM]
    changes = [s for s in present if role(s) == Role.CHANGE]
    contributors = [s for s in present if role(s) == Role.CONTRIBUTOR]
    symptoms = [s for s in present if role(s) == Role.SYMPTOM]

    # An origin explained by nothing else is a root candidate. A mechanism with no origin
    # explaining it becomes a root candidate too ("pool exhausted, holder not yet identified").
    roots = [s for s in origins if not (explained_by[s] & set(origins))]
    roots += [s for s in mechanisms if not (explained_by[s] & set(origins) - {s}) and not _explained_by_mechanism(s, explained_by, mechanisms)]

    def rank(sig: str) -> tuple[int, int, float]:
        match = by_id[sig]
        first = match.first_seen.timestamp() if match.first_seen else float("inf")
        return (0 if role(sig) == Role.ORIGIN else 1, -len(explains.get(sig, ())), first)

    roots = sorted(dict.fromkeys(roots), key=rank)
    earliest_failure = min((by_id[s].first_seen for s in present if by_id[s].first_seen and role(s) != Role.CHANGE),
                           default=None)
    triggers = [c for c in changes
                if by_id[c].first_seen is None or earliest_failure is None
                or by_id[c].first_seen <= earliest_failure + CLOCK_TOLERANCE]
    unexplained = tuple(s for s in symptoms if not explained_by[s])
    return FailureChain(
        root_candidates=tuple(roots), triggers=tuple(sorted(triggers)),
        mechanisms=tuple(sorted(m for m in mechanisms if m not in roots)),
        contributing=tuple(sorted(contributors)), symptoms=tuple(sorted(symptoms)),
        explains={k: v for k, v in explains.items() if v}, unexplained_symptoms=unexplained,
    )


def _explained_by_mechanism(sig: str, explained_by: dict[str, set[str]], mechanisms: list[str]) -> bool:
    return bool(explained_by[sig] & (set(mechanisms) - {sig}))


def _plausible_order(cause: SignatureMatch, effect: SignatureMatch) -> bool:
    if cause.first_seen is None or effect.first_seen is None:
        return True
    return cause.first_seen <= effect.first_seen + CLOCK_TOLERANCE


def build_graph(matches: Iterable[SignatureMatch], chain: FailureChain, *,
                evidence_by_signature: Mapping[str, str] | None = None,
                dependencies: Mapping[str, Iterable[str]] | None = None) -> CausalGraph:
    matches = tuple(matches)
    by_id = {m.id: m for m in matches}
    evidence_by_signature = evidence_by_signature or {}
    roles: dict[str, str] = {}
    for sig in chain.root_candidates:
        roles[sig] = "root_candidate"
    for sig in chain.triggers:
        roles[sig] = "trigger"
    for sig in chain.mechanisms:
        roles.setdefault(sig, "mechanism")
    for sig in chain.contributing:
        roles.setdefault(sig, "contributing")
    for sig in chain.symptoms:
        roles.setdefault(sig, "symptom")

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    services: set[str] = set()
    for match in matches:
        kind = "change" if match.signature.role == Role.CHANGE else "signature"
        nodes.append(GraphNode(
            id=f"sig:{match.id}", kind=kind, label=match.signature.label, role=roles.get(match.id),
            service=match.services[0] if match.services else None,
            first_seen=match.first_seen.isoformat() if match.first_seen else None,
            evidence_ids=tuple(filter(None, [evidence_by_signature.get(match.id)])),
        ))
        for service in match.services:
            services.add(service)
            edges.append(GraphEdge(source=f"sig:{match.id}", target=f"svc:{service}", relation="observed_in",
                                   basis="observed", rationale="signature matched events from this service"))
    for service in sorted(services | set((dependencies or {}).keys())):
        nodes.append(GraphNode(id=f"svc:{service}", kind="service", label=service, service=service))
    for cause, effects in chain.explains.items():
        for effect in effects:
            if cause not in by_id or effect not in by_id or not _plausible_order(by_id[cause], by_id[effect]):
                continue
            lag = _lag(by_id[cause], by_id[effect])
            edges.append(GraphEdge(source=f"sig:{cause}", target=f"sig:{effect}", relation="inferred_causes",
                                   basis="inferred", lag_seconds=lag,
                                   rationale=f"{by_id[cause].signature.label} is a known cause of "
                                             f"{by_id[effect].signature.label} and was observed no later than it"))
    for trigger in chain.triggers:
        for root in chain.root_candidates:
            edges.append(GraphEdge(source=f"sig:{trigger}", target=f"sig:{root}", relation="change_precedes",
                                   basis="inferred", lag_seconds=_lag(by_id[trigger], by_id[root]),
                                   rationale="a change preceded this failure; timing alone does not prove it caused it"))
    ordered = sorted((m for m in matches if m.first_seen), key=lambda m: m.first_seen)
    for left, right in zip(ordered, ordered[1:], strict=False):
        edges.append(GraphEdge(source=f"sig:{left.id}", target=f"sig:{right.id}", relation="observed_precedes",
                               basis="observed", lag_seconds=_lag(left, right),
                               rationale="first sightings in this order"))
    for service, deps in (dependencies or {}).items():
        for dep in deps:
            if not any(n.id == f"svc:{dep}" for n in nodes):
                nodes.append(GraphNode(id=f"svc:{dep}", kind="service", label=dep, service=dep))
            edges.append(GraphEdge(source=f"svc:{service}", target=f"svc:{dep}", relation="known_dependency",
                                   basis="known", rationale="declared service dependency"))
    return CausalGraph(nodes=tuple(nodes), edges=tuple(edges))


def _lag(a: SignatureMatch, b: SignatureMatch) -> float | None:
    if a.first_seen and b.first_seen:
        return round((b.first_seen - a.first_seen).total_seconds(), 3)
    return None


def blast_radius(primary_service: str, matches: Iterable[SignatureMatch],
                 dependencies: Mapping[str, Iterable[str]] | None = None) -> dict[str, list[str]]:
    """Services directly failing vs. those likely affected downstream via declared dependencies."""
    failing = sorted({s for m in matches for s in m.services} | {primary_service})
    dependents: set[str] = set()
    for service, deps in (dependencies or {}).items():
        if set(deps) & set(failing) and service not in failing:
            dependents.add(service)
    return {"directly_affected": failing, "possibly_affected_downstream": sorted(dependents)}


class ChainSummary(BaseModel):
    """Serializable view of `FailureChain` for reports."""

    model_config = ConfigDict(frozen=True)

    root_candidates: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    mechanisms: tuple[str, ...] = ()
    contributing: tuple[str, ...] = ()
    symptoms: tuple[str, ...] = ()
    unexplained_symptoms: tuple[str, ...] = ()
    explains: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @classmethod
    def of(cls, chain: FailureChain) -> "ChainSummary":
        return cls(root_candidates=chain.root_candidates, triggers=chain.triggers, mechanisms=chain.mechanisms,
                   contributing=chain.contributing, symptoms=chain.symptoms,
                   unexplained_symptoms=chain.unexplained_symptoms, explains=chain.explains)
