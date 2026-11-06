from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .agent import InvestigationResult
from .remediation import recommend_remediation
from .resolution import ResolutionRecord


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime | None
    description: str
    event_id: str | None = None


class IncidentPostmortem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    timeline: tuple[TimelineEntry, ...]
    root_cause_candidates: tuple[str, ...]
    resolution: ResolutionRecord | None
    follow_up_actions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]


def build_postmortem(result: InvestigationResult, *, resolution: ResolutionRecord | None = None) -> IncidentPostmortem:
    event_timeline: dict[str, TimelineEntry] = {}
    aggregate_timeline: list[TimelineEntry] = []
    for evidence in result.evidence:
        timestamp = evidence.observed_at
        source_timestamp = evidence.attributes.get("timestamp")
        if isinstance(source_timestamp, str):
            try:
                timestamp = datetime.fromisoformat(source_timestamp.replace("Z", "+00:00"))
            except ValueError:
                pass
        if evidence.event_ids:
            for event_id in evidence.event_ids:
                event_timeline.setdefault(event_id, TimelineEntry(timestamp=timestamp,
                                                                   description=evidence.summary,
                                                                   event_id=event_id))
        else:
            aggregate_timeline.append(TimelineEntry(timestamp=evidence.observed_at, description=evidence.summary))
    events = sorted((*event_timeline.values(), *aggregate_timeline),
                    key=lambda item: (item.timestamp is None, item.timestamp, item.event_id or ""))
    if result.hypotheses:
        summary = f"Investigation produced {len(result.hypotheses)} unverified root-cause candidates."
    else:
        summary = "Investigation did not produce a supported root-cause candidate."
    followups = tuple(step for recommendation in recommend_remediation(result.hypotheses)
                      for step in recommendation.steps)
    return IncidentPostmortem(
        incident_id=result.incident_id, summary=summary, timeline=tuple(events),
        root_cause_candidates=tuple(item.title for item in result.hypotheses), resolution=resolution,
        follow_up_actions=followups,
        evidence_ids=tuple(item.evidence_id for item in result.evidence),
        limitations=("Root-cause candidates are not confirmed without verification.",
                     "The timeline includes evidence extraction time when source timestamps are unavailable."),
    )


def render_postmortem_markdown(report, evaluation=None, *, title: str | None = None) -> str:
    """Blameless postmortem in Markdown from an `InvestigationReport` (+ optional `ResolutionEvaluation`).

    Unknown or unverified fields are written as such; nothing is invented to fill a section.
    """
    unknown = "_Unknown (not in the evidence)_"
    leader = report.hypotheses[0] if report.hypotheses else None
    lines = [f"# Postmortem: {title or report.incident_id}", "",
             f"**Incident:** {report.incident_id} · **Service:** {report.service} · **Severity:** {report.severity}  ",
             f"**Investigation status:** {report.status.value} · **Evidence strength:** {report.confidence_label}", "",
             "## Summary", "", report.headline or unknown, ""]
    lines += ["## Impact", "", f"Directly affected: {', '.join(report.blast_radius.get('directly_affected', [])) or unknown}."]
    downstream = report.blast_radius.get("possibly_affected_downstream") or []
    lines += [f"Possibly affected downstream: {', '.join(downstream)}." if downstream else
              "Downstream impact: not determined (no dependency map supplied).", ""]
    lines += ["## Timeline (UTC)", ""]
    if report.timeline:
        for entry in report.timeline[:20]:
            when = entry.at.strftime("%Y-%m-%d %H:%M:%S") if entry.at else "time unknown"
            lines.append(f"- **{when}** — {entry.summary}")
    else:
        lines.append(unknown)
    lines.append("")
    lines += ["## Root cause", ""]
    if evaluation is not None:
        lines += [f"**Confirmed by the responder:** {evaluation.memory_record.root_cause}", "",
                  f"Engine prediction: {evaluation.prediction.verdict.replace('_', ' ')} — {evaluation.prediction.explanation}.", ""]
    elif leader is not None:
        lines += [f"**Leading hypothesis (not verified):** {leader.title} — {leader.description}", ""]
    else:
        lines += [unknown, ""]
    if report.chain.contributing:
        lines += ["**Contributing factors:** " + ", ".join(report.chain.contributing), ""]
    if report.chain.symptoms:
        lines += ["**Downstream symptoms:** " + ", ".join(report.chain.symptoms), ""]
    lines += ["## Hypotheses considered", ""]
    for h in report.hypotheses[:5]:
        lines.append(f"- `{h.id}` **{h.title}** — score {h.score:.2f}, {h.status.value}"
                     + (f"; based on {', '.join(h.based_on_incidents)}" if h.based_on_incidents else ""))
    lines.append("")
    lines += ["## Response", ""]
    if evaluation is not None:
        for attempt in evaluation.attempts:
            lines.append(f"- {attempt.description} → **{attempt.outcome.value}**")
        lines += ["", f"Outcome: **{evaluation.outcome.value}**"
                  + (f" in {evaluation.time_to_resolve_minutes} minutes." if evaluation.time_to_resolve_minutes else "."), ""]
    else:
        lines += ["_Not yet resolved._", ""]
    lines += ["## What went well / what was hard", ""]
    if report.recalled_incidents:
        lines.append(f"- Memory surfaced {len(report.recalled_incidents)} similar past incident(s): "
                     + ", ".join(r.record.incident_id for r in report.recalled_incidents) + ".")
    for lesson in report.lessons:
        if lesson.kind == "avoid_action":
            lines.append(f"- Known dead end: {lesson.message}.")
    if report.degraded:
        lines += [f"- Limitation: {d}." for d in report.degraded]
    lines.append("")
    lines += ["## Action items", ""]
    for step in report.remediation.steps:
        if step.kind.value in {"root_cause_fix", "preventive", "monitoring_improvement"}:
            lines.append(f"- [ ] {step.description}" + (" _(requires approval)_" if step.requires_approval else ""))
    lines += ["", "_This document is blameless: it describes systems and decisions, not individuals._"]
    return "\n".join(lines)
