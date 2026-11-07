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
  confidence_calibrated?: boolean;
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

export type HealthResponse = {
  status: "ok" | "degraded";
  agent_mode: "mock" | "real";
  agent: string;
  database: "ok" | "error";
  llm_providers_configured: string[];
};

export type InvestigationReport = {
  incident_id: string;
  headline: string;
  confidence_label: string;
  top_score: number;
  chain: {
    root_candidates: string[];
    triggers: string[];
    mechanisms: string[];
    contributing: string[];
    symptoms: string[];
    unexplained_symptoms: string[];
    explains: Record<string, string[]>;
  };
  graph: {
    nodes: { id: string; kind: string; label: string; role: string | null; service: string | null; evidence_ids: string[] }[];
    edges: { source: string; target: string; relation: string; basis: "observed" | "known" | "inferred"; rationale: string; lag_seconds: number | null }[];
  };
  hypotheses: {
    id: string; title: string; description: string; score: number; status: string; reasoning: string;
    score_breakdown: { factor: string; delta: number; reason: string }[];
    supporting_evidence_ids: string[]; contradicting_evidence_ids: string[];
    missing_evidence: string[]; verification_steps: string[];
  }[];
  evidence: { evidence_id: string; kind: string; summary: string; source: string; event_ids: string[] }[];
  anomalies: { kind: string; summary: string; severity: string; method: string; service: string | null; evidence_id: string | null }[];
  recalled_incidents: { record: { incident_id: string; title: string; service: string; root_cause: string | null; resolution: string | null; attempts: { description: string; outcome: string }[] }; relevance: number; matching_points: string[]; differences: string[] }[];
  lessons: { kind: string; message: string; incident_ids: string[] }[];
  remediation: { steps: { id: string; kind: string; description: string; reason: string; expected_effect: string; evidence_ids: string[]; requires_approval: boolean; temporary: boolean; verification: string; rollback: string | null }[]; warnings: string[]; blocked_actions: string[] };
  replay: { sequence: number; at: string; phase: string; kind: string; summary: string; inputs: string[]; outputs: string[]; duration_ms: number; status: string }[];
  change_suspects: { change: { id: string; kind: string; title: string; timestamp: string | null; url: string | null }; score: number; reasons: string[]; matched_files: string[] }[];
  degraded: string[];
  service?: string;
  severity?: string;
  status?: string;
  started_at?: string;
  finished_at?: string;
  log_stats?: LogStats;
  timeline?: ReportTimelineEntry[];
  signatures?: SignatureFinding[];
  hypothesis_comparison?: string | null;
  llm_assessment?: LLMAssessment | null;
  debate?: DebateOutcome | null;
  diagnostics?: DiagnosticCall[];
  blast_radius?: Record<string, string[]>;
  origin_frame?: string | null;
  current_version?: string | null;
  memory_backend?: string | null;
  memory_consulted?: boolean;
  llm?: { calls: number; providers: string[]; models: string[]; fallbacks: string[]; errors: string[] };
  phases?: string[];
  calibration?: CalibrationReport;
  resolution_evaluation?: ResolutionEvaluation;
};

export type LogStats = {
  events: number;
  severity_counts: Record<string, number>;
  service_error_counts: Record<string, number>;
  top_error_groups: { fingerprint: string; count: number; services: string[]; exception_type: string | null; event_ids: string[] }[];
  formats: Record<string, number>;
  malformed_lines: number;
  duplicates: number;
  first_event_at: string | null;
  last_event_at: string | null;
};

export type ReportTimelineEntry = {
  at: string | null;
  service: string | null;
  kind: string;
  summary: string;
  evidence_id: string | null;
  event_id: string | null;
};

export type SignatureFinding = {
  id: string;
  label: string;
  category: string;
  role: string;
  count: number;
  services: string[];
  first_seen: string | null;
  snippet: string;
  evidence_id: string | null;
};

export type LLMAssessment = {
  assessments: {
    hypothesis_id: string;
    verdict: string;
    reasoning: string;
    supporting_evidence_ids: string[];
    contradicting_evidence_ids: string[];
    missing_evidence: string[];
  }[];
  new_hypothesis: { title: string; category: string; description: string; supporting_evidence_ids: string[] } | null;
  root_cause_summary: string;
  contributing_factors: string[];
  symptoms: string[];
  distinguishing_evidence: string[];
  uncertainty: string;
};

export type DebateOutcome = {
  stances: { perspective: string; hypothesis_id: string; stance: "support" | "oppose" | "neutral" | string; reason: string; evidence_ids: string[] }[];
  disagreements: string[];
  consensus_hypothesis_id: string | null;
};

export type DiagnosticCall = {
  tool_name: string;
  arguments: Record<string, unknown>;
  purpose: string;
  hypothesis_id: string | null;
  risk: string;
  status: string;
  result_summary: string | null;
  evidence_id: string | null;
};

export type CalibrationReport = {
  samples: number;
  graded: number;
  top1_accuracy: number | null;
  top3_accuracy: number | null;
  brier_score: number | null;
  unresolved: number;
  overconfident_misses: string[];
  bins: { low: number; high: number; count: number; mean_score: number | null; accuracy: number | null }[];
  calibrated: boolean;
  note: string;
  min_samples: number;
  illustrative?: boolean;
};

export type ResolutionEvaluation = {
  incident_id: string;
  outcome: string;
  attempts: { description: string; action_type: string; outcome: string }[];
  prediction: {
    verdict: string;
    predicted: string | null;
    predicted_signature: string | null;
    predicted_score: number | null;
    actual_signatures: string[];
    rank_of_actual?: number | null;
    explanation: string;
  };
  recommended_actions_tried: string[];
  recommended_actions_failed: string[];
  time_to_resolve_minutes: number | null;
  summary: string;
};
