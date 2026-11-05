from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from .evidence import Evidence
from .hypotheses import RootCauseHypothesis
from .providers import LLMResponse, ProviderRouter
from .tools import RiskLevel, ToolSpec, validate_tool_arguments


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    arguments: dict[str, str | int | float | bool | None]
    purpose: str
    evidence_gap: str
    risk: RiskLevel
    read_only: bool
    requires_approval: bool


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requests: tuple[DiagnosticRequest, ...]
    unresolved_questions: tuple[str, ...]


def plan_diagnostics(hypotheses: tuple[RootCauseHypothesis, ...] | list[RootCauseHypothesis],
                     tools: tuple[ToolSpec, ...] | list[ToolSpec], *,
                     excluded_tools: set[str] | None = None) -> InvestigationPlan:
    """Match evidence gaps only to declared connector tools, by capability text."""
    excluded = excluded_tools or set()
    requests: list[DiagnosticRequest] = []
    questions: list[str] = []
    for hypothesis in hypotheses:
        keywords = _keywords(hypothesis.hypothesis_id, hypothesis.title, hypothesis.explanation)
        candidates = [tool for tool in tools if tool.name not in excluded and _matches(tool, keywords)]
        if not candidates:
            questions.append(f"{hypothesis.title}: obtain diagnostic evidence to distinguish this explanation.")
            continue
        spec = next((item for item in candidates if item.read_only and item.risk == RiskLevel.READ_ONLY), candidates[0])
        purpose = f"Check the hypothesis: {hypothesis.title}."
        requests.append(DiagnosticRequest(
            tool_name=spec.name, arguments={}, purpose=purpose,
            evidence_gap=f"No direct diagnostic result currently tests {hypothesis.title}.",
            risk=spec.risk, read_only=spec.read_only,
            requires_approval=spec.requires_approval or not spec.read_only or spec.risk != RiskLevel.READ_ONLY,
        ))
    unique: dict[str, DiagnosticRequest] = {}
    for request in requests:
        unique.setdefault(request.tool_name, request)
    return InvestigationPlan(requests=tuple(unique.values()), unresolved_questions=tuple(questions))


def plan_with_llm(hypotheses: tuple[RootCauseHypothesis, ...] | list[RootCauseHypothesis],
                  evidence: tuple[Evidence, ...] | list[Evidence], tools: tuple[ToolSpec, ...] | list[ToolSpec],
                  router: ProviderRouter) -> tuple[InvestigationPlan, LLMResponse]:
    """Request additional read-only diagnostics with strict registry validation."""
    specs = tuple(tools)
    if not specs:
        raise ValueError("LLM planning requires connector-declared tools")
    payload_in = {
        "hypotheses": [{"id": item.hypothesis_id, "title": item.title,
                        "evidence": list(item.supporting_evidence_ids)} for item in hypotheses[:8]],
        "evidence": [{"id": item.evidence_id, "summary": item.summary[:180]} for item in evidence[:40]],
        "tools": [{"name": item.name, "description": item.description,
                   "allowed_parameters": item.allowed_parameters,
                   "required_parameters": item.required_parameters,
                   "read_only": item.read_only, "risk": item.risk.value}
                  for item in specs],
    }
    result, response = router.complete_json(
        json.dumps(payload_in, ensure_ascii=False),
        system=("Plan the next read-only diagnostics for incident hypotheses. Return JSON with a requests array; "
                "each request has tool_name, arguments, purpose, evidence_gap. Use only listed tools and parameters. "
                "Do not invent tools, shell commands, credentials, or state-changing actions. Prefer the fewest useful checks."),
        max_output_tokens=700, temperature=0.1,
    )
    raw_requests = result.get("requests", [])
    if not isinstance(raw_requests, list):
        return InvestigationPlan(requests=(), unresolved_questions=()), response
    by_name = {item.name: item for item in specs}
    requests: list[DiagnosticRequest] = []
    questions: list[str] = []
    for raw in raw_requests[:8]:
        if not isinstance(raw, dict):
            continue
        name = raw.get("tool_name")
        spec = by_name.get(name) if isinstance(name, str) else None
        args = raw.get("arguments", {})
        if not spec or not isinstance(args, dict) or not spec.read_only or spec.risk != RiskLevel.READ_ONLY:
            continue
        try:
            validate_tool_arguments(spec, args)
        except ValueError:
            continue
        if any(value is not None and type(value) not in {str, int, float, bool} for value in args.values()):
            continue
        requests.append(DiagnosticRequest(
            tool_name=spec.name, arguments=args,
            purpose=str(raw.get("purpose", "Gather incident evidence."))[:240],
            evidence_gap=str(raw.get("evidence_gap", "A hypothesis needs further testing."))[:240],
            risk=spec.risk, read_only=True, requires_approval=spec.requires_approval,
        ))
    unique = {request.tool_name: request for request in requests}
    if not unique:
        questions.append("No valid read-only diagnostic plan was returned by the model.")
    return InvestigationPlan(requests=tuple(unique.values()), unresolved_questions=tuple(questions)), response


def _keywords(*texts: str) -> set[str]:
    joined = " ".join(texts).lower()
    categories = set()
    if any(word in joined for word in ("database", "sql", "deadlock", "connection")):
        categories |= {"database", "db", "sql", "dependency", "logs", "metrics"}
    if any(word in joined for word in ("deployment", "release", "regression")):
        categories |= {"deployment", "release", "github", "commit", "workflow"}
    if any(word in joined for word in ("resource", "memory", "cpu", "disk", "capacity")):
        categories |= {"container", "docker", "metrics", "resource", "health"}
    if any(word in joined for word in ("dependency", "network", "timeout", "upstream")):
        categories |= {"dependency", "network", "service", "health", "logs"}
    return categories


def _matches(tool: ToolSpec, keywords: set[str]) -> bool:
    text = f"{tool.name} {tool.description}".lower()
    return bool(keywords and any(word in text for word in keywords))
