export type Severity = "SEV1" | "SEV2" | "SEV3" | "SEV4";
export type IncidentStatus = "open" | "investigating" | "resolved";

export type IncidentSummary = {
  id: string;
  title: string;
  service: string;
  severity: Severity;
  status: IncidentStatus;
  alert_text: string;
  created_at: string;
  resolved_at: string | null;
  root_cause: string | null;
  time_to_resolve_minutes: number | null;
};

export type TimelineEvent = {
  id: number;
  timestamp: string;
  type: "alert" | "analysis" | "action" | "resolution";
  message: string;
};

export type Suggestion = {
  likely_root_cause: string;
  steps: string[];
  warnings: string[];
  confidence: number;
  based_on: string[];
};

export type IncidentDetail = IncidentSummary & {
  logs: string;
  resolution: string | null;
  failed_attempts: string[];
  notes: string | null;
  last_suggestion: Suggestion | null;
  timeline: TimelineEvent[];
};

export type ResolveResponse = IncidentDetail & {
  memory_saved: boolean;
};

export type PastIncidentBrief = {
  id: string;
  title: string;
  service: string;
  severity: Severity;
  created_at: string;
  root_cause: string | null;
  resolution: string | null;
  failed_attempts: string[];
  time_to_resolve_minutes: number | null;
};

export type MemoryReference = {
  past_incident_id: string;
  summary: string;
  relevance_score: number;
  used_at: string;
  past_incident: PastIncidentBrief | null;
};

export type StatsResponse = {
  total_incidents: number;
  open: number;
  investigating: number;
  resolved: number;
  avg_time_to_resolve_minutes: number | null;
  avg_ttr_with_memory: number | null;
  avg_ttr_without_memory: number | null;
  by_service: Record<string, number>;
  ttr_over_time: { period: string; resolved: number; avg_ttr_minutes: number }[];
  resolutions: {
    id: string;
    title: string;
    service: string;
    resolved_at: string;
    time_to_resolve_minutes: number;
    memory_assisted: boolean;
  }[];
};

export type CreateIncidentRequest = {
  title: string;
  service: string;
  severity: Severity;
  alert_text: string;
  logs: string;
};

export type ResolveIncidentRequest = {
  root_cause: string;
  fix_that_worked: string;
  what_failed: string[];
  notes: string | null;
};

export type MemoryEvent = {
  type: "memory";
  past_incident_id: string;
  title: string;
  service: string;
  summary: string;
  relevance_score: number;
  root_cause: string | null;
  resolution: string | null;
  failed_attempts: string[];
  time_to_resolve_minutes: number | null;
};

export type TokenEvent = { type: "token"; text: string };
export type SuggestionEvent = Suggestion & { type: "suggestion" };
export type DoneEvent = { type: "done"; incident_id: string | null; memories_used: number };
export type ErrorEvent = { type: "error"; code: string; message: string };
export type AnalysisEvent = MemoryEvent | TokenEvent | SuggestionEvent | DoneEvent | ErrorEvent;

export type InvestigationStatus = "idle" | "connecting" | "streaming" | "complete" | "error";
export type InvestigationPhase = "idle" | "scanning" | "recalling" | "synthesizing" | "consensus" | "complete" | "error";

export type InvestigationState = {
  status: InvestigationStatus;
  phase: InvestigationPhase;
  memories: MemoryEvent[];
  reasoning: string;
  suggestion: Suggestion | null;
  done: DoneEvent | null;
  consensus: boolean;
  error: string | null;
};

export type SceneMemory = {
  id: string;
  title: string;
  service: string;
  severity: Severity | null;
  status: IncidentStatus | null;
  createdAt: string | null;
  rootCause: string | null;
  resolution: string | null;
  failedAttempts: string[];
  timeToResolveMinutes: number | null;
  summary: string | null;
  relevance: number | null;
  recalled: boolean;
};

export type SceneCamera = { yaw: number; pitch: number };
export type ScenePoint = { x: number; y: number };
export type SceneNode = {
  memoryId: string;
  ring: 0 | 1 | 2;
  angle: number;
  radius: number;
  depth: number;
  size: number;
  phase: number;
};
