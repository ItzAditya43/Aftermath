import type {
  AnalysisEvent,
  IncidentDetail,
  InvestigationState,
  MemoryEvent,
} from "./memory-types";

export const CONSENSUS_CONFIDENCE_THRESHOLD = 0.8;

export const initialInvestigationState: InvestigationState = {
  status: "idle",
  phase: "idle",
  memories: [],
  reasoning: "",
  suggestion: null,
  done: null,
  consensus: false,
  error: null,
};

export function beginInvestigation(): InvestigationState {
  return {
    ...initialInvestigationState,
    status: "connecting",
    phase: "scanning",
  };
}

export function applyAnalysisEvent(state: InvestigationState, event: AnalysisEvent): InvestigationState {
  switch (event.type) {
    case "memory": {
      const memories = state.memories.some((memory) => memory.past_incident_id === event.past_incident_id)
        ? state.memories
        : [...state.memories, event].sort((a, b) => b.relevance_score - a.relevance_score);
      return { ...state, status: "streaming", phase: "recalling", memories };
    }
    case "token":
      return { ...state, status: "streaming", phase: state.memories.length ? "synthesizing" : "scanning", reasoning: state.reasoning + event.text };
    case "suggestion":
      return { ...state, status: "streaming", phase: "synthesizing", suggestion: event };
    case "done":
      return { ...state, status: "complete", phase: "complete", done: event, error: null };
    case "error":
      return { ...state, status: "error", phase: "error", error: event.message || "The investigation agent returned an error." };
  }
}

export function failInvestigation(state: InvestigationState, message: string): InvestigationState {
  return { ...state, status: "error", phase: "error", error: message };
}

/** Consensus is derived only from a completed and internally consistent backend result. */
export function hasConsensusEvidence(state: InvestigationState, incident: IncidentDetail): boolean {
  const { done, suggestion, memories } = state;
  if (state.status !== "complete" || !done || !suggestion) return false;
  if (done.incident_id !== incident.id || done.memories_used !== memories.length) return false;
  if (suggestion.confidence < CONSENSUS_CONFIDENCE_THRESHOLD) return false;

  const recalledIds = new Set(memories.map((memory) => memory.past_incident_id));
  const supportedMemories = new Set(suggestion.based_on.filter((id) => recalledIds.has(id)));
  return supportedMemories.size >= 2;
}

export function toMemoryEvent(reference: {
  past_incident_id: string;
  summary: string;
  relevance_score: number;
  past_incident: {
    title: string;
    service: string;
    root_cause: string | null;
    resolution: string | null;
    failed_attempts: string[];
    time_to_resolve_minutes: number | null;
  } | null;
}): MemoryEvent {
  const incident = reference.past_incident;
  return {
    type: "memory",
    past_incident_id: reference.past_incident_id,
    title: incident?.title ?? reference.past_incident_id,
    service: incident?.service ?? "Unknown service",
    summary: reference.summary,
    relevance_score: reference.relevance_score,
    root_cause: incident?.root_cause ?? null,
    resolution: incident?.resolution ?? null,
    failed_attempts: incident?.failed_attempts ?? [],
    time_to_resolve_minutes: incident?.time_to_resolve_minutes ?? null,
  };
}
