"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { AlertCircle, Check, ChevronDown, CircleHelp, Command, LoaderCircle, Pause, Play, Plus, Radio, RotateCcw, Search, ShieldCheck, Sparkles, X } from "lucide-react";
import ConstellationCanvas from "@/components/constellation/ConstellationCanvas";
import ReticleCursor from "./ReticleCursor";
import {
  createIncident,
  getIncident,
  getIncidentMemories,
  getIncidents,
  getStats,
  resolveIncident,
  streamIncidentAnalysis,
  isApiOfflineError,
} from "@/lib/api-client";
import {
  applyAnalysisEvent,
  beginInvestigation,
  failInvestigation,
  hasConsensusEvidence,
  hasSavedConsensusEvidence,
  getIncidentListAvailability,
  initialInvestigationState,
  toMemoryEvent,
} from "@/lib/investigation-state";
import type {
  CreateIncidentRequest,
  IncidentDetail,
  IncidentSummary,
  InvestigationState,
  MemoryEvent,
  MemoryReference,
  ResolveIncidentRequest,
  SceneMemory,
  Severity,
  StatsResponse,
} from "@/lib/memory-types";

const severities: Severity[] = ["SEV1", "SEV2", "SEV3", "SEV4"];
type MobilePanel = "incident" | "recall" | null;
const severityStyle: Record<Severity, string> = {
  SEV1: "text-[#22D3EE] border-[#22D3EE]/40 bg-[#22D3EE]/[0.12]",
  SEV2: "text-[#72B6BB] border-[#72B6BB]/40 bg-[#72B6BB]/[0.12]",
  SEV3: "text-[#38BDF8] border-[#38BDF8]/40 bg-[#38BDF8]/[0.12]",
  SEV4: "text-[#94A3B8] border-[#94A3B8]/40 bg-[#94A3B8]/[0.12]",
};

function formatMinutes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.round(value)}m`;
  return `${Math.floor(value / 60)}h ${Math.round(value % 60)}m`;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function FlankCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-[14px] border border-[rgba(168,85,247,0.25)] bg-[rgba(14,19,38,0.92)] p-4 shadow-[0_12px_40px_rgba(3,4,11,0.52)] backdrop-blur-xl ${className}`}>{children}</div>;
}

function CreateIncidentForm({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (data: CreateIncidentRequest) => Promise<void>;
}) {
  const [severity, setSeverity] = useState<Severity>("SEV2");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await onSubmit({
      title: String(form.get("title") ?? "").trim(),
      service: String(form.get("service") ?? "").trim(),
      severity,
      alert_text: String(form.get("alert_text") ?? "").trim(),
      logs: String(form.get("logs") ?? "").trim(),
    });
  };

  return (
    <form onSubmit={submit} className="space-y-3.5" aria-label="Create incident">
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        Incident title
        <input
          name="title"
          required
          minLength={3}
          maxLength={200}
          className="cosmic-input mt-1.5"
          placeholder="e.g. Checkout service failing 502 Bad Gateway"
        />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
          Service
          <input
            name="service"
            required
            pattern="[a-z0-9][a-z0-9.\-]*"
            maxLength={64}
            className="cosmic-input mt-1.5"
            placeholder="payments-api"
            title="Service name must start with a lowercase letter or digit and contain only lowercase letters, digits, dots, underscores, or hyphens (e.g. payments-api)"
          />
          <span className="mt-1 block text-xs normal-case tracking-normal text-slate-400">
            Lowercase letters, numbers, dots, underscores, or hyphens.
          </span>
        </label>
        <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
          Severity
          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value as Severity)}
            className="cosmic-input mt-1.5"
            aria-label="Incident severity level"
          >
            {severities.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        Alert context
        <textarea
          name="alert_text"
          required
          maxLength={10000}
          rows={3}
          className="cosmic-input mt-1.5 resize-y text-xs"
          placeholder="Describe what fired, impacted services, and when it started..."
        />
      </label>
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        Logs <span className="text-xs font-normal normal-case tracking-normal text-slate-400">(optional)</span>
        <textarea
          name="logs"
          maxLength={200000}
          rows={2}
          className="cosmic-input mt-1.5 resize-y font-mono text-xs"
          placeholder="Paste relevant stack trace or log lines..."
        />
      </label>
      <div className="flex gap-2 pt-1">
        <button disabled={busy} className="cosmic-primary flex-1 min-h-[44px]">
          {busy ? <LoaderCircle className="animate-spin" size={16} /> : <Plus size={16} />} Create incident
        </button>
        <button type="button" onClick={onCancel} className="cosmic-quiet min-h-[44px]">Cancel</button>
      </div>
    </form>
  );
}

function ResolveIncidentForm({
  busy,
  suggestion,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  suggestion: InvestigationState["suggestion"];
  onCancel: () => void;
  onSubmit: (data: ResolveIncidentRequest) => Promise<void>;
}) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await onSubmit({
      root_cause: String(form.get("root_cause") ?? "").trim(),
      fix_that_worked: String(form.get("fix_that_worked") ?? "").trim(),
      what_failed: String(form.get("what_failed") ?? "").split("\n").map((line) => line.trim()).filter(Boolean),
      notes: String(form.get("notes") ?? "").trim() || null,
    });
  };
  return (
    <form onSubmit={submit} className="space-y-3.5" aria-label="Record incident resolution">
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        Root cause
        <textarea
          name="root_cause"
          required
          maxLength={5000}
          rows={2}
          defaultValue={suggestion?.likely_root_cause ?? ""}
          className="cosmic-input mt-1.5 resize-y text-xs"
          placeholder="What fundamentally caused this outage?"
        />
      </label>
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        Fix that worked
        <textarea
          name="fix_that_worked"
          required
          maxLength={5000}
          rows={2}
          defaultValue={suggestion?.steps.join("\n") ?? ""}
          className="cosmic-input mt-1.5 resize-y text-xs"
          placeholder="Steps taken that successfully resolved the incident..."
        />
      </label>
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        What failed <span className="text-xs font-normal normal-case tracking-normal text-slate-400">(one per line)</span>
        <textarea
          name="what_failed"
          rows={2}
          className="cosmic-input mt-1.5 resize-y text-xs"
          placeholder="Attempts or rollouts that did not resolve the issue..."
        />
      </label>
      <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
        Notes <span className="text-xs font-normal normal-case tracking-normal text-slate-400">(optional)</span>
        <textarea
          name="notes"
          maxLength={10000}
          rows={2}
          className="cosmic-input mt-1.5 resize-y text-xs"
          placeholder="Additional follow-ups or post-incident action items..."
        />
      </label>
      <div className="flex gap-2">
        <button disabled={busy} className="cosmic-primary flex-1 min-h-[44px]">
          {busy ? <LoaderCircle className="animate-spin" size={16} /> : <Check size={16} />} Save resolution
        </button>
        <button type="button" onClick={onCancel} className="cosmic-quiet min-h-[44px]">Cancel</button>
      </div>
    </form>
  );
}

export default function AftermathExperience() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [activeIncidentId, setActiveIncidentId] = useState<string | null>(null);
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [memoryRefs, setMemoryRefs] = useState<MemoryReference[]>([]);
  const [previousMemoryRefs, setPreviousMemoryRefs] = useState<Record<string, MemoryReference[]>>({});
  const [investigation, setInvestigation] = useState<InvestigationState>(initialInvestigationState);
  const [focusedMemoryId, setFocusedMemoryId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [loadingWorkspace, setLoadingWorkspace] = useState(true);
  const [loadingIncident, setLoadingIncident] = useState(false);
  const [analysisRequestActive, setAnalysisRequestActive] = useState(false);
  const [busyAction, setBusyAction] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [incidentListError, setIncidentListError] = useState<string | null>(null);
  const [apiOffline, setApiOffline] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // Replay is an explicit visual reset; investigation attempts reset only the
  // recall event choreography, never the one-time constellation introduction.
  const [animationKey, setAnimationKey] = useState(0);
  const [investigationKey, setInvestigationKey] = useState(0);
  const [paused, setPaused] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showResolve, setShowResolve] = useState(false);
  const [incidentSearch, setIncidentSearch] = useState("");
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>(null);
  const [dockExpanded, setDockExpanded] = useState(false);
  const [resolutionFeedback, setResolutionFeedback] = useState<{ incidentId: string; memorySaved: boolean | null } | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const analysisSnapshotRef = useRef<{ incidentId: string; state: InvestigationState } | null>(null);
  const runIdRef = useRef(0);
  const investigationRunStateRef = useRef<InvestigationState>(initialInvestigationState);
  const investigationCacheRef = useRef<Record<string, InvestigationState>>({});
  const incidentRef = useRef<IncidentDetail | null>(null);
  const activeIncidentIdRef = useRef<string | null>(null);
  const incidentPanelRef = useRef<HTMLElement | null>(null);

  useEffect(() => { incidentRef.current = incident; }, [incident]);
  useEffect(() => { activeIncidentIdRef.current = activeIncidentId; }, [activeIncidentId]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([getIncidents(controller.signal), getStats(controller.signal)]).then(([incidentResult, statsResult]) => {
      if (controller.signal.aborted) return;
      if (incidentResult.status === "fulfilled") {
        setIncidents(incidentResult.value);
        setIncidentListError(null);
        const currentId = activeIncidentIdRef.current;
        const nextSelected = currentId && incidentResult.value.some((item) => item.id === currentId)
          ? currentId
          : (incidentResult.value.find((item) => item.status !== "resolved") ?? incidentResult.value[0])?.id ?? null;
        if (nextSelected !== currentId) {
          const currentRun = investigationRunStateRef.current;
          if (currentId && (currentRun.status === "complete" || currentRun.status === "error")) {
            investigationCacheRef.current[currentId] = currentRun;
          } else if (currentId) {
            delete investigationCacheRef.current[currentId];
          }
          if (analysisSnapshotRef.current?.incidentId === currentId) analysisSnapshotRef.current = null;
          streamAbortRef.current?.abort();
          runIdRef.current += 1;
          setAnalysisRequestActive(false);
          activeIncidentIdRef.current = nextSelected;
          incidentRef.current = null;
          investigationRunStateRef.current = investigationCacheRef.current[nextSelected ?? ""] ?? initialInvestigationState;
          setIncident(null);
          setMemoryRefs([]);
          setInvestigation(investigationRunStateRef.current);
          setFocusedMemoryId(null);
          setLoadingIncident(Boolean(nextSelected));
        }
        setActiveIncidentId(nextSelected);
      } else {
        const message = incidentResult.reason instanceof Error ? incidentResult.reason.message : "Could not load incidents.";
        setIncidentListError(message);
        setApiError(message);
      }
      if (statsResult.status === "fulfilled") setStats(statsResult.value);
      else setStatsError(statsResult.reason instanceof Error ? statsResult.reason.message : "Could not load statistics.");
      const successfulRequest = incidentResult.status === "fulfilled" || statsResult.status === "fulfilled";
      setApiOffline(successfulRequest ? false : isApiOfflineError(incidentResult.reason) || isApiOfflineError(statsResult.reason));
      setLoadingWorkspace(false);
    });
    return () => controller.abort();
  }, [reloadKey]);

  useEffect(() => {
    if (!activeIncidentId) return;
    const controller = new AbortController();
    Promise.allSettled([
      getIncident(activeIncidentId, controller.signal),
      getIncidentMemories(activeIncidentId, controller.signal),
    ]).then(([incidentResult, memoriesResult]) => {
      if (controller.signal.aborted || activeIncidentIdRef.current !== activeIncidentId) return;
      if (incidentResult.status === "fulfilled") {
        incidentRef.current = incidentResult.value;
        setIncident(incidentResult.value);
        const cachedInvestigation = investigationCacheRef.current[activeIncidentId];
        if (cachedInvestigation) {
          investigationRunStateRef.current = cachedInvestigation;
          setInvestigation(cachedInvestigation);
        } else {
          const restoredInvestigation: InvestigationState = incidentResult.value.last_suggestion
            ? {
              ...initialInvestigationState,
              status: "idle",
              phase: "idle",
              memories: memoriesResult.status === "fulfilled" ? memoriesResult.value.map(toMemoryEvent) : [],
              suggestion: incidentResult.value.last_suggestion,
              consensus: memoriesResult.status === "fulfilled"
                ? hasSavedConsensusEvidence(incidentResult.value.last_suggestion, memoriesResult.value.map(toMemoryEvent))
                : false,
            }
            : initialInvestigationState;
          investigationCacheRef.current[activeIncidentId] = restoredInvestigation;
          investigationRunStateRef.current = restoredInvestigation;
          setInvestigation(restoredInvestigation);
        }
      }
      else setApiError(incidentResult.reason instanceof Error ? incidentResult.reason.message : "Could not load incident details.");
      if (memoriesResult.status === "fulfilled") setMemoryRefs(memoriesResult.value);
      else if (incidentResult.status === "fulfilled") setApiError(memoriesResult.reason instanceof Error ? memoriesResult.reason.message : "Could not load incident memories.");
      const successfulRequest = incidentResult.status === "fulfilled" || memoriesResult.status === "fulfilled";
      setApiOffline(successfulRequest ? false : isApiOfflineError(incidentResult.reason) || isApiOfflineError(memoriesResult.reason));
      setLoadingIncident(false);
    });
    return () => controller.abort();
  }, [activeIncidentId, reloadKey]);

  useEffect(() => () => {
    streamAbortRef.current?.abort();
    runIdRef.current += 1;
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "")) return;
      if (event.code === "KeyR") {
        setPaused(false);
        setAnimationKey((value) => value + 1);
      } else if (event.code === "Space" && (target === document.body || target === document.documentElement)) {
        event.preventDefault();
        setPaused((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const sceneMemories = useMemo(() => {
    const byId = new Map<string, SceneMemory>();

    if (incident && activeIncidentId) {
      const existing = byId.get(incident.id);
      byId.set(incident.id, {
        id: incident.id,
        title: incident.title,
        service: incident.service,
        severity: incident.severity,
        status: incident.status,
        createdAt: incident.created_at,
        rootCause: incident.root_cause,
        resolution: incident.resolution,
        failedAttempts: incident.failed_attempts ?? [],
        timeToResolveMinutes: incident.time_to_resolve_minutes,
        summary: incident.alert_text || existing?.summary || null,
        relevance: existing?.relevance ?? null,
        recalled: existing?.recalled ?? false,
      });
    }

    const recalled = new Map<string, MemoryEvent>();
    memoryRefs.forEach((reference) => {
      const event = toMemoryEvent(reference);
      recalled.set(event.past_incident_id, event);
      const prior = reference.past_incident;
      if (prior) byId.set(prior.id, {
        id: prior.id,
        title: prior.title,
        service: prior.service,
        severity: prior.severity,
        status: "resolved",
        createdAt: prior.created_at,
        rootCause: prior.root_cause,
        resolution: prior.resolution,
        failedAttempts: prior.failed_attempts ?? [],
        timeToResolveMinutes: prior.time_to_resolve_minutes,
        summary: reference.summary,
        relevance: reference.relevance_score,
        recalled: true,
      });
    });
    investigation.memories.forEach((event) => recalled.set(event.past_incident_id, event));

    for (const event of recalled.values()) {
      const existing = byId.get(event.past_incident_id);
      byId.set(event.past_incident_id, {
        id: event.past_incident_id,
        title: event.title || existing?.title || event.past_incident_id,
        service: event.service || existing?.service || "Unknown service",
        severity: existing?.severity ?? null,
        status: existing?.status ?? null,
        createdAt: existing?.createdAt ?? null,
        rootCause: event.root_cause ?? existing?.rootCause ?? null,
        resolution: event.resolution ?? existing?.resolution ?? null,
        failedAttempts: event.failed_attempts ?? existing?.failedAttempts ?? [],
        timeToResolveMinutes: event.time_to_resolve_minutes ?? existing?.timeToResolveMinutes ?? null,
        summary: event.summary,
        relevance: event.relevance_score,
        recalled: true,
      });
    }
    return Array.from(byId.values());
  }, [incident, activeIncidentId, investigation.memories, memoryRefs]);

  const visibleIncidents = useMemo(() => {
    const query = incidentSearch.trim().toLowerCase();
    if (!query) return incidents;
    return incidents.filter((item) => `${item.id} ${item.title} ${item.service}`.toLowerCase().includes(query));
  }, [incidents, incidentSearch]);
  const selectedSummary = incidents.find((item) => item.id === activeIncidentId) ?? null;
  const recalls = sceneMemories.filter((memory) => memory.recalled).sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0));
  const focusedMemory = sceneMemories.find((memory) => memory.id === focusedMemoryId) ?? null;
  const previouslyRecalled = activeIncidentId ? previousMemoryRefs[activeIncidentId] ?? [] : [];
  const streamActive = investigation.status === "connecting" || investigation.status === "streaming";

  const openCreateIncident = () => {
    if (apiOffline) return;
    setShowCreate(true);
    setShowResolve(false);
    setMobilePanel("incident");
    requestAnimationFrame(() => incidentPanelRef.current?.scrollTo({ top: 0, behavior: "smooth" }));
  };

  const selectMemory = (id: string | null) => {
    setFocusedMemoryId(id);
    if (id) setMobilePanel(null);
  };

  const selectIncident = (id: string) => {
    if (id === activeIncidentId) return;
    const currentRun = investigationRunStateRef.current;
    if (activeIncidentId && (currentRun.status === "complete" || currentRun.status === "error")) {
      investigationCacheRef.current[activeIncidentId] = currentRun;
    } else if (activeIncidentId) {
      delete investigationCacheRef.current[activeIncidentId];
    }
    if (analysisSnapshotRef.current?.incidentId === activeIncidentId) analysisSnapshotRef.current = null;
    streamAbortRef.current?.abort();
    runIdRef.current += 1;
    setAnalysisRequestActive(false);
    activeIncidentIdRef.current = id;
    incidentRef.current = null;
    investigationRunStateRef.current = investigationCacheRef.current[id] ?? initialInvestigationState;
    setIncident(null);
    setMemoryRefs([]);
    setInvestigation(investigationRunStateRef.current);
    setFocusedMemoryId(null);
    setLoadingIncident(true);
    setApiError(null);
    setResolutionFeedback(null);
    setActiveIncidentId(id);
    setShowResolve(false);
    setShowCreate(false);
  };

  const startAnalysis = useCallback(async () => {
    if (!activeIncidentId) {
      setApiError("Select an incident before starting an investigation.");
      return;
    }
    const incidentId = activeIncidentId;
    const activeIncident = incidentRef.current;
    if (!activeIncident || activeIncident.id !== incidentId) {
      setApiError(`Incident ${incidentId} is still loading. Wait for its context to finish, then retry.`);
      return;
    }
    if (streamAbortRef.current && !streamAbortRef.current.signal.aborted) return;
    const controller = new AbortController();
    streamAbortRef.current = controller;
    const runId = ++runIdRef.current;
    const previousInvestigation = investigationRunStateRef.current;
    analysisSnapshotRef.current = { incidentId, state: previousInvestigation };
    const isCurrentRun = () => !controller.signal.aborted
      && runId === runIdRef.current
      && activeIncidentIdRef.current === incidentId;
    investigationRunStateRef.current = beginInvestigation();
    investigationCacheRef.current[incidentId] = investigationRunStateRef.current;
    setAnalysisRequestActive(true);
    setInvestigation(investigationRunStateRef.current);
    if (memoryRefs.length > 0) {
      setPreviousMemoryRefs((current) => ({ ...current, [incidentId]: memoryRefs }));
    }
    setMemoryRefs([]);
    setApiError(null);
    setPaused(false);
    setInvestigationKey((value) => value + 1);

    try {
      await streamIncidentAnalysis(incidentId, controller.signal, (event) => {
        if (!isCurrentRun()) return;
        investigationRunStateRef.current = applyAnalysisEvent(investigationRunStateRef.current, event);
        if (event.type === "done") {
          const activeIncident = incidentRef.current;
          investigationRunStateRef.current = {
            ...investigationRunStateRef.current,
            consensus: activeIncident?.id === incidentId ? hasConsensusEvidence(investigationRunStateRef.current, activeIncident) : false,
          };
        }
        investigationCacheRef.current[incidentId] = investigationRunStateRef.current;
        setInvestigation(investigationRunStateRef.current);
      });
      if (!isCurrentRun()) return;
      setApiOffline(false);
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
        setAnalysisRequestActive(false);
      }
      analysisSnapshotRef.current = null;
      setLoadingIncident(true);
      const [detailResult, memoryResult, incidentListResult, statsResult] = await Promise.allSettled([
        getIncident(incidentId, controller.signal),
        getIncidentMemories(incidentId, controller.signal),
        getIncidents(controller.signal),
        getStats(controller.signal),
      ]);
      if (!isCurrentRun()) return;
      setLoadingIncident(false);
      const refreshErrors: string[] = [];
      if (detailResult.status === "fulfilled") {
        const previousDetail = incidentRef.current?.id === incidentId ? incidentRef.current : null;
        const refreshedDetail: IncidentDetail = {
          ...detailResult.value,
          status: previousDetail?.status === "resolved" ? "resolved" : detailResult.value.status,
          resolved_at: detailResult.value.resolved_at ?? previousDetail?.resolved_at ?? null,
          root_cause: detailResult.value.root_cause ?? previousDetail?.root_cause ?? null,
          resolution: detailResult.value.resolution ?? previousDetail?.resolution ?? null,
          failed_attempts: detailResult.value.failed_attempts.length > 0
            ? detailResult.value.failed_attempts
            : previousDetail?.failed_attempts ?? [],
        };
        incidentRef.current = refreshedDetail;
        setIncident(refreshedDetail);
      } else refreshErrors.push(detailResult.reason instanceof Error ? `Incident details: ${detailResult.reason.message}` : "Incident details could not refresh.");
      if (memoryResult.status === "fulfilled") setMemoryRefs(memoryResult.value);
      else refreshErrors.push(memoryResult.reason instanceof Error ? `Historical memories: ${memoryResult.reason.message}` : "Historical memories could not refresh.");
      if (incidentListResult.status === "fulfilled") { setIncidents(incidentListResult.value); setIncidentListError(null); }
      else {
        const message = incidentListResult.reason instanceof Error ? incidentListResult.reason.message : "Incident status could not refresh.";
        setIncidentListError(message);
        refreshErrors.push(`Incident status: ${message}`);
      }
      if (statsResult.status === "fulfilled") { setStats(statsResult.value); setStatsError(null); }
      else setStatsError(statsResult.reason instanceof Error ? statsResult.reason.message : "Statistics could not be refreshed.");
      if ([detailResult, memoryResult, incidentListResult, statsResult].some((result) => result.status === "fulfilled")) setApiOffline(false);
      else if ([detailResult, memoryResult, incidentListResult, statsResult].some((result) => result.status === "rejected" && isApiOfflineError(result.reason))) setApiOffline(true);
      if (refreshErrors.length) setApiError(`Investigation completed, but some data could not refresh: ${refreshErrors.join(" · ")}`);
    } catch (error) {
      if (!isCurrentRun()) return;
      if (isApiOfflineError(error)) setApiOffline(true);
      const message = error instanceof Error ? error.message : "The investigation stream ended unexpectedly.";
      investigationRunStateRef.current = failInvestigation(investigationRunStateRef.current, message, previousInvestigation);
      investigationCacheRef.current[incidentId] = investigationRunStateRef.current;
      setInvestigation(investigationRunStateRef.current);
    } finally {
      if (analysisSnapshotRef.current?.incidentId === incidentId) analysisSnapshotRef.current = null;
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
        setAnalysisRequestActive(false);
      }
      if (isCurrentRun()) setLoadingIncident(false);
    }
  }, [activeIncidentId, memoryRefs]);

  const onCreateIncident = async (data: CreateIncidentRequest) => {
    setBusyAction(true);
    setApiError(null);
    try {
      const created = await createIncident(data);
      setApiOffline(false);
      const previousSnapshot = analysisSnapshotRef.current;
      if (previousSnapshot) delete investigationCacheRef.current[previousSnapshot.incidentId];
      analysisSnapshotRef.current = null;
      streamAbortRef.current?.abort();
      runIdRef.current += 1;
      setAnalysisRequestActive(false);
      activeIncidentIdRef.current = created.id;
      incidentRef.current = created;
      investigationRunStateRef.current = initialInvestigationState;
      investigationCacheRef.current[created.id] = initialInvestigationState;
      setMemoryRefs([]);
      setInvestigation(initialInvestigationState);
      setFocusedMemoryId(null);
      setResolutionFeedback(null);
      setIncident(created);
      setActiveIncidentId(created.id);
      setLoadingIncident(true);
      setLoadingWorkspace(true);
      setShowCreate(false);
      setReloadKey((value) => value + 1);
    } catch (error) {
      if (isApiOfflineError(error)) setApiOffline(true);
      setApiError(error instanceof Error ? error.message : "Could not create the incident.");
    } finally {
      setBusyAction(false);
    }
  };

  const onResolveIncident = async (data: ResolveIncidentRequest) => {
    if (!activeIncidentId) return;
    const resolvedId = activeIncidentId;
    setBusyAction(true);
    setApiError(null);
    try {
      const activeStream = streamAbortRef.current;
      const cancelingActiveStream = activeStream !== null && !activeStream.signal.aborted;
      const previousInvestigation = analysisSnapshotRef.current?.incidentId === resolvedId
        ? analysisSnapshotRef.current.state
        : investigationRunStateRef.current;
      activeStream?.abort();
      runIdRef.current += 1;
      setAnalysisRequestActive(false);
      if (cancelingActiveStream) {
        investigationRunStateRef.current = failInvestigation(
          investigationRunStateRef.current,
          "The investigation was stopped because the resolution was submitted.",
          previousInvestigation,
        );
        investigationCacheRef.current[resolvedId] = investigationRunStateRef.current;
        setInvestigation(investigationRunStateRef.current);
      }
      analysisSnapshotRef.current = null;
      const resolved = await resolveIncident(resolvedId, data);
      setApiOffline(false);
      setResolutionFeedback({ incidentId: resolvedId, memorySaved: typeof resolved.memory_saved === "boolean" ? resolved.memory_saved : null });
      if (activeIncidentIdRef.current === resolvedId) {
        incidentRef.current = resolved;
        setIncident(resolved);
      }
      setShowResolve(false);
      if (activeIncidentIdRef.current === resolvedId) setLoadingIncident(true);
      const [listResult, detailResult, referencesResult, statsResult] = await Promise.allSettled([
        getIncidents(), getIncident(resolvedId), getIncidentMemories(resolvedId), getStats(),
      ]);
      const refreshErrors: string[] = [];
      if (listResult.status === "fulfilled") { setIncidents(listResult.value); setIncidentListError(null); }
      else {
        const message = listResult.reason instanceof Error ? listResult.reason.message : "Incident status could not refresh.";
        setIncidentListError(message);
        refreshErrors.push(`Incident status: ${message}`);
      }
      if (activeIncidentIdRef.current === resolvedId) {
        if (detailResult.status === "fulfilled") { incidentRef.current = detailResult.value; setIncident(detailResult.value); }
        else refreshErrors.push(detailResult.reason instanceof Error ? `Incident details: ${detailResult.reason.message}` : "Incident details could not refresh.");
        if (referencesResult.status === "fulfilled") setMemoryRefs(referencesResult.value);
        else refreshErrors.push(referencesResult.reason instanceof Error ? `Historical memories: ${referencesResult.reason.message}` : "Historical memories could not refresh.");
      }
      if (statsResult.status === "fulfilled") { setStats(statsResult.value); setStatsError(null); }
      else setStatsError(statsResult.reason instanceof Error ? statsResult.reason.message : "Statistics could not be refreshed.");
      if ([listResult, detailResult, referencesResult, statsResult].some((result) => result.status === "fulfilled")) setApiOffline(false);
      else if ([listResult, detailResult, referencesResult, statsResult].some((result) => result.status === "rejected" && isApiOfflineError(result.reason))) setApiOffline(true);
      if (refreshErrors.length) setApiError(`Resolution was recorded, but some data could not refresh: ${refreshErrors.join(" · ")}`);
    } catch (error) {
      if (isApiOfflineError(error)) setApiOffline(true);
      setApiError(error instanceof Error ? error.message : "Could not save this resolution.");
    } finally {
      if (activeIncidentIdRef.current === resolvedId) setLoadingIncident(false);
      setBusyAction(false);
    }
  };

  const phaseLabel = apiOffline ? "API OFFLINE"
    : loadingIncident ? "LOADING INCIDENT CONTEXT"
    : investigation.consensus ? "CONSENSUS EVIDENCE REACHED"
    : investigation.status === "connecting" ? "CONNECTING TO INVESTIGATION"
    : investigation.status === "streaming" ? (investigation.phase === "recalling" ? `RECALLING ${investigation.memories.length} INCIDENT${investigation.memories.length === 1 ? "" : "S"}` : "SYNTHESIZING EVIDENCE")
    : investigation.phase === "recalling" ? `RECALLING ${investigation.memories.length} INCIDENT${investigation.memories.length === 1 ? "" : "S"}`
    : investigation.phase === "synthesizing" ? "SYNTHESIZING EVIDENCE"
    : investigation.status === "complete" ? "INVESTIGATION COMPLETE · NO CONSENSUS"
    : investigation.status === "error" ? "STREAM INTERRUPTED"
    : incident?.status === "resolved" ? "RESOLVED · READY TO RE-ANALYZE" : "READY TO INVESTIGATE";

  const playbookSuggestion = investigation.suggestion;
  const suggestionConfidenceLabel = playbookSuggestion?.confidence_calibrated === true
    ? `${Math.round(playbookSuggestion.confidence * 100)}%`
    : playbookSuggestion ? `rank ${playbookSuggestion.confidence.toFixed(2)}` : "";
  const suggestionBadgeLabel = investigation.consensus
    ? "CONSENSUS EVIDENCE"
    : playbookSuggestion?.confidence_calibrated === true
      ? "SUGGESTION · NOT CONSENSUS"
      : "SUGGESTION · RANK SCORE";
  const analyzeButtonLabel = analysisRequestActive
    ? "Investigation running"
    : incident?.status === "open"
      ? "Analyze incident"
      : "Re-analyze";
  const memoryAssistedAverage = apiOffline ? null : stats?.avg_ttr_with_memory;
  const memoryFreeAverage = apiOffline ? null : stats?.avg_ttr_without_memory;
  const incidentListAvailability = getIncidentListAvailability(loadingWorkspace, incidentListError, apiOffline, incidents.length);

  const retryWorkspace = () => {
    setLoadingWorkspace(true);
    setApiError(null);
    setStatsError(null);
    setReloadKey((value) => value + 1);
  };

  return (
    <main className="cosmic-shell relative min-h-[100svh] overflow-hidden text-[#DDD6FE]">
      <section className="relative h-[100svh] w-full overflow-hidden" aria-label="Aftermath incident intelligence">
        <div className="absolute inset-0 z-0">
          <ConstellationCanvas
            memories={sceneMemories}
            activeIncidentId={activeIncidentId}
            selectedId={focusedMemoryId}
            onSelect={selectMemory}
            onHover={(id) => { setHoveredNodeId(id); if (id) setMobilePanel(null); }}
            active={streamActive}
            consensus={investigation.consensus}
            animationKey={animationKey}
            investigationKey={investigationKey}
            paused={paused}
            reducedMotion={reducedMotion}
          />
        </div>
        <ReticleCursor nodeHovered={Boolean(hoveredNodeId)} />
        <div className="cosmic-vignette pointer-events-none absolute inset-0 z-[1]" />
        <div className="cosmic-grain pointer-events-none absolute inset-0 z-[2]" />

        <header className="pointer-events-none absolute inset-x-0 top-0 z-30 flex h-16 items-center justify-between border-b border-[rgba(56,189,248,0.15)] bg-[rgba(6,8,19,0.85)] px-4 backdrop-blur-md md:px-7">
          <div className="pointer-events-auto flex min-w-0 items-center gap-4">
            <Link href="/" className="inline-flex min-h-[44px] items-center gap-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 rounded-lg py-1">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-[#245D64] to-[#4B9298] text-sm font-bold text-white shadow-[0_0_16px_rgba(124,58,237,0.4)]" aria-hidden="true">⌁</div>
              <span className="shrink-0 text-sm font-extrabold tracking-tight text-white">AFTER<span className="text-[#38BDF8]">MATH</span></span>
            </Link>
            <nav aria-label="Main navigation" className="hidden lg:flex items-center gap-1 ml-2">
              <Link
                href="/dashboard"
                className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:bg-slate-800/60"
              >
                Dashboard
              </Link>
              <Link
                href="/incidents"
                className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:bg-slate-800/60"
              >
                Incidents
              </Link>
              <Link
                href="/memories"
                className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:bg-slate-800/60"
              >
                Memories
              </Link>
              <Link
                href="/analytics"
                className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:bg-slate-800/60"
              >
                Analytics
              </Link>
            </nav>
            {selectedSummary && <span className={`hidden rounded-full border px-2.5 py-1 font-mono text-xs font-semibold tracking-wider 2xl:inline-flex ${severityStyle[selectedSummary.severity]}`}>{selectedSummary.severity} · {selectedSummary.id}</span>}
          </div>
          <div className="hidden items-center gap-2 rounded-full border border-[rgba(56,189,248,0.25)] bg-[rgba(14,19,38,0.92)] px-4 py-1.5 font-mono text-xs tracking-wider text-slate-300 backdrop-blur-lg md:flex">
            <span className={`h-2 w-2 rounded-full ${investigation.consensus ? "bg-[#22D3EE] shadow-[0_0_8px_#22D3EE]" : streamActive ? "animate-pulse bg-[#38BDF8] shadow-[0_0_8px_#38BDF8]" : "bg-slate-400"}`} />
            STATUS · <span className={investigation.consensus ? "text-[#22D3EE]" : "text-[#38BDF8]"}>{phaseLabel}</span>
          </div>
          <div className="pointer-events-auto flex items-center gap-2 md:gap-4">
            <div className="hidden items-center gap-4 text-xs font-medium tracking-wide text-slate-300 lg:flex">
              <span>INCIDENTS <b className="font-mono text-slate-100">{apiOffline ? "—" : stats?.total_incidents ?? "—"}</b></span>
              <span>OPEN <b className="font-mono text-slate-100">{apiOffline || !stats ? "—" : stats.open + stats.investigating}</b></span>
              <span>AVG TTR <b className="font-mono text-slate-100">{apiOffline || stats?.avg_time_to_resolve_minutes == null ? "—" : formatMinutes(stats.avg_time_to_resolve_minutes)}</b></span>
            </div>
            <button
              type="button"
              className="cosmic-quiet min-h-[44px] min-w-[44px] !px-3.5 text-xs font-semibold"
              onClick={openCreateIncident}
              disabled={apiOffline}
              aria-expanded={showCreate}
              aria-label="Create incident"
            >
              <Plus size={16} aria-hidden="true" />
              <span className="hidden sm:inline">New incident</span>
            </button>
          </div>
        </header>

        <nav className="pointer-events-auto absolute inset-x-2 top-[70px] z-30 flex items-center justify-center gap-2 md:hidden" aria-label="Incident panels">
          <button
            type="button"
            className="cosmic-quiet min-h-[44px] flex-1 !justify-start !px-3 text-xs font-medium"
            aria-expanded={mobilePanel === "incident"}
            aria-controls="incident-context"
            onClick={() => setMobilePanel((panel) => panel === "incident" ? null : "incident")}
          >
            <span className="truncate">{selectedSummary?.id ? `${selectedSummary.id} · ${selectedSummary.title}` : "Incident Details"}</span>
            <ChevronDown size={14} className={`ml-auto shrink-0 transition-transform ${mobilePanel === "incident" ? "rotate-180" : ""}`} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="cosmic-quiet min-h-[44px] shrink-0 !px-3.5 text-xs font-medium"
            aria-expanded={mobilePanel === "recall"}
            aria-controls="recall-spine"
            onClick={() => setMobilePanel((panel) => panel === "recall" ? null : "recall")}
          >
            Recalled <span className="font-mono font-bold text-[#22D3EE]">{recalls.length}</span>
            <ChevronDown size={14} className={`ml-1 transition-transform ${mobilePanel === "recall" ? "rotate-180" : ""}`} aria-hidden="true" />
          </button>
        </nav>

        <aside
          ref={incidentPanelRef}
          id="incident-context"
          tabIndex={0}
          role="region"
          aria-label="Incident context panel"
          className={`incident-context-panel pointer-events-none absolute left-6 top-[76px] z-20 flex w-[280px] flex-col gap-3 overflow-y-auto overscroll-contain max-lg:left-3 max-lg:top-[74px] max-lg:w-[270px] ${mobilePanel === "incident" ? "max-md:left-2 max-md:right-2 max-md:top-[122px] max-md:z-40 max-md:w-auto max-md:max-h-[calc(100svh-140px)]" : "max-md:hidden"}`}
        >
          <FlankCard className="pointer-events-auto">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Incident workspace</p>
              <button
                type="button"
                onClick={openCreateIncident}
                disabled={apiOffline}
                aria-label="Create incident"
                className="grid min-h-[44px] min-w-[44px] place-items-center rounded-lg text-teal-300 hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Plus size={16} aria-hidden="true" />
              </button>
            </div>
            {incidentListAvailability === "offline" ? (
              <div className="py-3">
                <p className="text-sm font-semibold text-slate-100">Can&apos;t reach the API.</p>
                <p className="mt-1 text-xs leading-relaxed text-slate-300">Incident data and memory statistics are unavailable while disconnected.</p>
                <button type="button" onClick={retryWorkspace} className="cosmic-quiet mt-3 w-full min-h-[44px] text-xs">Retry API</button>
              </div>
            ) : incidentListAvailability === "error" ? (
              <div className="py-3">
                <p className="text-sm font-semibold text-slate-100">Could not load incidents.</p>
                <button type="button" onClick={retryWorkspace} className="cosmic-quiet mt-3 w-full min-h-[44px] text-xs">Retry API</button>
              </div>
            ) : showCreate ? (
              <CreateIncidentForm busy={busyAction} onCancel={() => setShowCreate(false)} onSubmit={onCreateIncident} />
            ) : loadingWorkspace ? (
              <div className="flex items-center gap-2 py-4 text-xs text-slate-300"><LoaderCircle size={16} className="animate-spin text-sky-400" />Loading incident memory</div>
            ) : incidentListAvailability === "empty" ? (
              <div className="py-2">
                <p className="text-sm font-semibold text-slate-100">No incidents found.</p>
                <p className="mt-1 text-xs leading-relaxed text-slate-300">Create an incident to start an investigation.</p>
                <button type="button" onClick={openCreateIncident} className="cosmic-primary mt-3 w-full min-h-[44px] text-xs"><Plus size={16} /> Create incident</button>
              </div>
            ) : (
              <>
                <label htmlFor="incident-picker" className="sr-only">Select incident</label>
                <div className="relative">
                  <select
                    id="incident-picker"
                    value={activeIncidentId ?? ""}
                    onChange={(event) => { if (event.target.value) selectIncident(event.target.value); }}
                    className="cosmic-input appearance-none pr-8 text-xs font-semibold min-h-[44px]"
                    aria-label="Select incident"
                  >
                    {activeIncidentId && !visibleIncidents.some((item) => item.id === activeIncidentId) && (
                      <option value={activeIncidentId} disabled>{selectedSummary ? `${selectedSummary.id} · hidden by search` : "Selected incident"}</option>
                    )}
                    {!activeIncidentId && <option value="" disabled>{visibleIncidents.length ? "Choose an incident" : "No matching incidents"}</option>}
                    {visibleIncidents.map((item) => (
                      <option key={item.id} value={item.id}>{item.id} · {item.title}</option>
                    ))}
                  </select>
                  <ChevronDown size={14} className="pointer-events-none absolute right-3 top-3.5 text-slate-400" aria-hidden="true" />
                </div>
                <div className="relative mt-2">
                  <Search size={14} className="absolute left-3 top-3.5 text-teal-300" aria-hidden="true" />
                  <input
                    type="search"
                    aria-label="Search incidents by ID, title, or service"
                    value={incidentSearch}
                    onChange={(event) => setIncidentSearch(event.target.value)}
                    className="cosmic-input !pl-9 text-xs min-h-[44px]"
                    placeholder="Search incidents by title, service..."
                  />
                </div>
                <h1 className="mt-3 line-clamp-3 text-sm font-bold leading-snug tracking-tight text-slate-100">
                  {incident?.title ?? selectedSummary?.title ?? "Loading incident…"}
                </h1>
                <p className="mt-1 font-mono text-xs font-medium text-sky-400">
                  {incident?.service ?? selectedSummary?.service ?? ""}
                </p>
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                  {selectedSummary && <span className={`rounded border px-2.5 py-1 font-mono font-semibold ${severityStyle[selectedSummary.severity]}`}>{selectedSummary.severity}</span>}
                  <span className="rounded border border-white/10 px-2.5 py-1 capitalize text-slate-200">{incident?.status ?? selectedSummary?.status ?? "loading"}</span>
                  <span className="w-full truncate text-slate-400">{formatTimestamp(incident?.created_at ?? selectedSummary?.created_at)}</span>
                </div>

                {/* PRIMARY ACTIONS BAR: Placed prominently right below incident summary (Issue #13) */}
                <div className="mt-3.5 space-y-2">
                  <button
                    type="button"
                    onClick={startAnalysis}
                    disabled={!incident || analysisRequestActive}
                    aria-busy={analysisRequestActive}
                    className="cosmic-primary w-full min-h-[44px] text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {analysisRequestActive ? <LoaderCircle size={16} className="animate-spin" /> : <Sparkles size={16} />}
                    {analyzeButtonLabel}
                  </button>
                  {analysisRequestActive && <p className="mt-1 font-mono text-xs text-sky-400" role="status">Streaming latest analysis for {activeIncidentId}.</p>}
                  {investigation.status === "error" && <p className="mt-1 break-words text-xs leading-relaxed text-amber-300" role="alert">Latest analysis failed: {investigation.error}</p>}
                  {incident && incident.status !== "resolved" && (
                    <button
                      type="button"
                      onClick={() => setShowResolve((value) => !value)}
                      className="cosmic-quiet w-full min-h-[44px] text-xs font-semibold"
                      aria-expanded={showResolve}
                    >
                      <ShieldCheck size={15} /> Record resolution
                    </button>
                  )}
                </div>

                {/* SCROLLABLE DETAILS REGION */}
                <div tabIndex={0} role="region" aria-label="Incident details" className="mt-3 space-y-3 border-t border-white/[0.08] pt-3">
                  {showResolve && (
                    <div className="rounded-lg border border-teal-600/30 bg-slate-900/20 p-3">
                      <ResolveIncidentForm
                        busy={busyAction}
                        suggestion={investigation.suggestion}
                        onCancel={() => setShowResolve(false)}
                        onSubmit={onResolveIncident}
                      />
                    </div>
                  )}

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Alert context</p>
                    <p className="line-clamp-4 text-xs leading-relaxed text-slate-200">{incident?.alert_text ?? selectedSummary?.alert_text ?? ""}</p>
                    {loadingIncident && <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-400"><LoaderCircle size={14} className="animate-spin text-sky-400" />Refreshing incident context</p>}
                  </div>

                  {(incident?.root_cause || incident?.resolution || incident?.failed_attempts.length || incident?.resolved_at) && (
                    <div className="rounded-lg border border-[rgba(245,199,106,0.25)] bg-[rgba(245,199,106,0.06)] p-3 text-xs leading-relaxed" aria-live="polite">
                      <p className="font-mono text-xs font-semibold uppercase tracking-wider text-[#22D3EE]">Saved resolution</p>
                      {incident.resolved_at && <p className="mt-1 font-mono text-xs text-sky-400">Resolved {formatTimestamp(incident.resolved_at)}</p>}
                      {incident.root_cause && <p className="mt-1 text-xs text-[#C4B5FD]"><b>Root cause:</b> {incident.root_cause}</p>}
                      {incident.resolution && <p className="mt-1 text-xs text-[#CBD5E1]"><b>Fix:</b> {incident.resolution}</p>}
                      {incident.failed_attempts.length > 0 && (
                        <div className="mt-1 text-xs text-[#A5F3FC]">
                          <b>Failed attempts:</b>
                          <ul className="mt-0.5 list-disc pl-4 space-y-0.5">{incident.failed_attempts.map((attempt, index) => <li key={`${index}-${attempt}`}>{attempt}</li>)}</ul>
                        </div>
                      )}
                    </div>
                  )}

                  {resolutionFeedback?.incidentId === activeIncidentId && (
                    <div className={`rounded-lg border p-3 text-xs leading-relaxed ${resolutionFeedback.memorySaved === true ? "border-[#22D3EE]/30 bg-[#22D3EE]/[0.08] text-[#A5F3FC]" : "border-[#4B9298]/30 bg-[#4B9298]/[0.08] text-slate-100"}`} role="status">
                      <p className="font-mono text-xs font-semibold uppercase tracking-wider">Resolution recorded · {resolutionFeedback.incidentId}</p>
                      <p className="mt-1">{resolutionFeedback.memorySaved === true ? "Memory successfully saved to incident bank." : resolutionFeedback.memorySaved === false ? "Memory save unsuccessful; the incident resolution was recorded." : "The API did not confirm whether memory was saved."}</p>
                    </div>
                  )}

                  {focusedMemory && (
                    <div className="rounded-lg border border-[rgba(34,211,238,0.35)] bg-[rgba(34,211,238,0.06)] p-3 text-xs" aria-live="polite">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs font-semibold text-[#22D3EE]">HISTORICAL MEMORY · {focusedMemory.id}</span>
                        <button type="button" onClick={() => selectMemory(null)} aria-label="Clear selected memory" className="grid min-h-[44px] min-w-[44px] place-items-center text-slate-300 hover:text-white"><X size={16} /></button>
                      </div>
                      <p className="mt-1 font-mono text-xs text-sky-400">ACTIVE INCIDENT · {activeIncidentId ?? "NONE"}</p>
                      <p className="mt-1 text-xs font-semibold leading-snug text-slate-100">{focusedMemory.title}</p>
                      {focusedMemory.rootCause && <p className="mt-1 text-xs text-slate-200"><b>Root cause:</b> {focusedMemory.rootCause}</p>}
                      {focusedMemory.failedAttempts.length > 0 && (
                        <div className="mt-1 text-xs leading-relaxed text-[#A5F3FC]">
                          <b>Prior attempts:</b>
                          <ul className="mt-0.5 list-disc pl-4 space-y-0.5">{focusedMemory.failedAttempts.map((attempt, index) => <li key={`${index}-${attempt}`}>{attempt}</li>)}</ul>
                        </div>
                      )}
                      {focusedMemory.resolution && <p className="mt-1 text-xs text-[#A5F3FC]"><b>Verified fix:</b> {focusedMemory.resolution}</p>}
                    </div>
                  )}

                  {previouslyRecalled.length > 0 && (
                    <details className="rounded-lg border border-white/[0.08] p-2.5 text-xs">
                      <summary className="cursor-pointer font-mono text-xs uppercase tracking-wider text-slate-300 py-1">Previously recalled memories · {previouslyRecalled.length}</summary>
                      <ul tabIndex={0} role="region" aria-label="Previously recalled memories" className="mt-2 max-h-32 space-y-1.5 overflow-y-auto">
                        {previouslyRecalled.map((reference) => (
                          <li key={reference.past_incident_id} className="break-words text-xs leading-relaxed text-slate-300">
                            <b className="font-mono text-teal-200">{reference.past_incident_id}</b> · {reference.past_incident?.title ?? reference.summary} · ranking score {reference.relevance_score.toFixed(2)}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  <details className="border-t border-[#4B9298]/20 pt-2 text-xs">
                    <summary className="cursor-pointer py-1.5 font-mono text-xs tracking-wider text-slate-200 font-medium">Accessible memory index · {sceneMemories.length}</summary>
                    <div tabIndex={0} role="region" aria-label="Memory selection list" className="mt-1 max-h-44 space-y-1 overflow-y-auto">
                      {sceneMemories.map((memory) => (
                        <button
                          key={memory.id}
                          type="button"
                          aria-pressed={focusedMemoryId === memory.id}
                          onClick={() => selectMemory(focusedMemoryId === memory.id ? null : memory.id)}
                          className={`flex min-h-[44px] w-full items-center justify-between gap-2 rounded-md border px-2.5 text-left text-xs ${focusedMemoryId === memory.id ? "border-[#22D3EE]/60 bg-[#22D3EE]/[0.1] text-[#A5F3FC]" : "border-white/[0.08] text-slate-200 hover:border-[#4B9298]/50 hover:bg-white/[0.02]"}`}
                        >
                          <span className="min-w-0"><b className="mr-2 font-mono text-teal-300">{memory.id}</b><span className="break-words">{memory.title}</span></span>
                          {memory.relevance != null && <span className="shrink-0 font-mono text-[#22D3EE] font-semibold">Score {memory.relevance.toFixed(2)}</span>}
                        </button>
                      ))}
                    </div>
                  </details>
                </div>
              </>
            )}
          </FlankCard>

          {/* MEMORY BANK CARD: Responsive and accessible on all viewport sizes (Issue #19) */}
          <FlankCard className="pointer-events-auto !p-3.5">
            <div className="flex items-center justify-between font-mono text-xs uppercase tracking-wider text-slate-300">
              <span className="font-semibold">Memory bank</span>
              <b className="text-sky-400">{apiOffline ? "—" : `${sceneMemories.length} incidents`}</b>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[rgba(168,85,247,0.2)]">
              <div
                className="h-full bg-gradient-to-r from-[#245D64] via-[#4B9298] to-[#22D3EE] transition-[width] duration-700"
                style={{ width: `${sceneMemories.length ? (recalls.length / sceneMemories.length) * 100 : 0}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-slate-300">
              <span>{recalls.length} recalled</span>
              <span>{investigation.status === "idle" ? "Historical index" : "Current analysis"}</span>
            </div>
            <div
              tabIndex={0}
              role="region"
              aria-label="Memory bank active memories"
              className="mt-2.5 max-h-28 overflow-y-auto space-y-1"
            >
              {recalls.length === 0 ? (
                <p className="py-2 text-xs text-slate-400">Recalled memories will be indexed here during analysis.</p>
              ) : (
                recalls.map((memory) => (
                  <button
                    key={memory.id}
                    type="button"
                    onClick={() => selectMemory(focusedMemoryId === memory.id ? null : memory.id)}
                    className="flex w-full min-h-[44px] items-center justify-between gap-1.5 rounded px-2.5 py-1.5 text-left text-xs hover:bg-white/5 transition"
                  >
                    <span className="font-mono text-teal-200 font-semibold">{memory.id}</span>
                    <span className="truncate text-slate-200 text-xs flex-1">{memory.title}</span>
                    <span className="font-mono text-[#22D3EE] text-xs font-bold">Score {(memory.relevance ?? 0).toFixed(2)}</span>
                  </button>
                ))
              )}
            </div>
          </FlankCard>
        </aside>

        <aside
          id="recall-spine"
          tabIndex={0}
          role="region"
          aria-label="Recalled incident matches panel"
          className={`pointer-events-none absolute right-6 top-[76px] z-20 w-[250px] max-h-[calc(100svh-96px)] max-lg:right-3 max-lg:top-[74px] max-lg:w-[240px] ${mobilePanel === "recall" ? "max-md:left-2 max-md:right-2 max-md:top-[122px] max-md:z-40 max-md:w-auto max-md:max-h-[calc(100svh-140px)] max-md:overflow-y-auto max-md:overscroll-contain" : "max-md:hidden"}`}
        >
          <FlankCard className="pointer-events-auto max-h-[inherit] overflow-hidden !p-3.5 sm:!p-4">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                  {investigation.status === "idle" ? "Recalled memories" : "Analysis recall"}
                </p>
                <p className="mt-1 font-mono text-xs font-bold text-[#22D3EE]">
                  {recalls.length} matched incident{recalls.length === 1 ? "" : "s"}
                </p>
              </div>
              <span className="h-2.5 w-2.5 rounded-full bg-[#22D3EE] shadow-[0_0_8px_#22D3EE]" />
            </div>
            <div
              tabIndex={0}
              role="region"
              aria-label="Recalled incident matches"
              className="mt-3 max-h-[min(52vh,420px)] space-y-2 overflow-y-auto pr-1"
              aria-live="polite"
            >
              {recalls.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-3 py-6 text-center text-xs leading-relaxed text-slate-400">
                  When you run an investigation, similar past outages from memory will be recalled here with their verified root causes and fixes.
                </div>
              ) : (
                recalls.map((memory) => (
                  <button
                    type="button"
                    key={memory.id}
                    onClick={() => selectMemory(focusedMemoryId === memory.id ? null : memory.id)}
                    aria-pressed={focusedMemoryId === memory.id}
                    className={`group w-full min-h-[44px] rounded-lg border p-2.5 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#22D3EE] ${focusedMemoryId === memory.id ? "border-[#22D3EE]/60 bg-[#22D3EE]/[0.1]" : "border-white/[0.08] bg-[#0E1326]/60 hover:border-[#4B9298]/50 hover:bg-[#0E1326]/90"}`}
                  >
                    <span className="flex items-center justify-between gap-1 font-mono text-xs">
                      <b className="text-slate-100">{memory.id}</b>
                      <span className="text-[#22D3EE] font-semibold">{memory.relevance == null ? "Recalled" : `Score ${memory.relevance.toFixed(2)}`}</span>
                    </span>
                    <span className="mt-1 block line-clamp-2 text-xs font-medium leading-snug text-slate-200">{memory.title}</span>
                    <span className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-400">
                      <span className="truncate">{memory.service}</span>
                      <span className="font-mono">{formatMinutes(memory.timeToResolveMinutes)}</span>
                    </span>
                    <span className="mt-1.5 block h-[3px] rounded-full bg-white/[0.08]">
                      <i className="block h-full rounded-full bg-gradient-to-r from-[#245D64] via-[#4B9298] to-[#22D3EE]" style={{ width: `${Math.max(6, (memory.relevance ?? 0.12) * 100)}%` }} />
                    </span>
                  </button>
                ))
              )}
            </div>
          </FlankCard>
          <div className="pointer-events-auto mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setPaused((value) => !value)}
              aria-pressed={paused}
              className="cosmic-quiet min-h-[44px] min-w-[44px] !px-3 text-xs"
              aria-label={paused ? "Resume constellation animation" : "Pause constellation animation"}
            >
              {paused ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
              <span className="hidden sm:inline">{paused ? "Resume" : "Pause"}</span>
            </button>
            <button
              type="button"
              onClick={() => { setPaused(false); setAnimationKey((value) => value + 1); }}
              className="cosmic-quiet min-h-[44px] min-w-[44px] !px-3 text-xs"
              aria-label="Replay current investigation visualization"
            >
              <RotateCcw size={15} aria-hidden="true" />
              <span className="hidden sm:inline">Replay</span>
            </button>
          </div>
        </aside>

        <div className="pointer-events-auto absolute right-2 top-[120px] z-30 flex gap-1.5 md:hidden">
          <button
            type="button"
            onClick={() => setPaused((value) => !value)}
            aria-pressed={paused}
            className="cosmic-quiet min-h-[44px] min-w-[44px] !px-2.5"
            aria-label={paused ? "Resume constellation animation" : "Pause constellation animation"}
          >
            {paused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}
          </button>
          <button
            type="button"
            onClick={() => { setPaused(false); setAnimationKey((value) => value + 1); }}
            className="cosmic-quiet min-h-[44px] min-w-[44px] !px-2.5"
            aria-label="Replay current investigation visualization"
          >
            <RotateCcw size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="pointer-events-none absolute left-1/2 z-10 -translate-x-1/2 text-center max-md:hidden" style={{ top: "calc(44% - 130px)" }}>
          <span className="rounded-full border border-[rgba(168,85,247,0.25)] bg-[rgba(6,8,19,0.8)] px-3.5 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-300 backdrop-blur-sm">
            Active Incident Focus
          </span>
        </div>

        <section
          tabIndex={0}
          className={`cinematic-dock fixed bottom-2 left-1/2 z-30 -translate-x-1/2 rounded-[14px] border border-[rgba(168,85,247,0.32)] bg-[rgba(14,19,38,0.95)] shadow-[0_20px_60px_rgba(0,0,0,0.65)] backdrop-blur-2xl transition-all duration-300 min-h-[72px] w-[calc(100%-16px)] lg:w-[min(840px,calc(100%-600px))] 2xl:w-[min(1180px,calc(100%-600px))] ${dockExpanded ? "dock-expanded" : ""}`}
          aria-label="Investigation synthesis console"
        >
          {playbookSuggestion ? (
            <>
              {/* Mobile / Tablet View */}
              <div className="grid grid-cols-1 gap-2.5 p-3.5 lg:hidden">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-mono text-xs font-semibold tracking-wider text-[#22D3EE]">
                      {suggestionBadgeLabel} · {suggestionConfidenceLabel}
                    </div>
                    <p className="mt-1 line-clamp-1 text-xs font-bold text-slate-100">
                      {playbookSuggestion.likely_root_cause}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="cosmic-quiet min-h-[44px] min-w-[44px] shrink-0 !px-3.5 text-xs font-semibold"
                    aria-expanded={dockExpanded}
                    onClick={() => setDockExpanded((value) => !value)}
                  >
                    {dockExpanded ? "Less" : "Details"}
                  </button>
                </div>
                {dockExpanded && (
                  <div tabIndex={0} role="region" aria-label="Investigation suggested response" className="space-y-3 border-t border-white/10 pt-3 max-h-60 overflow-y-auto">
                    <div>
                      <p className="mb-2 font-mono text-xs uppercase tracking-wider text-teal-200 font-semibold">Suggested response steps</p>
                      <ol className="space-y-2">
                        {playbookSuggestion.steps.map((step, index) => (
                          <li key={`${index}-${step}`} className="flex gap-2 text-xs leading-relaxed text-slate-200">
                            <span className="font-mono text-[#22D3EE] font-bold">0{index + 1}</span>
                            <span>{step}</span>
                          </li>
                        ))}
                      </ol>
                      {playbookSuggestion.steps.length === 0 && <p className="text-xs text-slate-400">The agent did not return action steps.</p>}
                    </div>
                    {playbookSuggestion.warnings.length > 0 && (
                      <p className="text-xs text-amber-300"><b>Warnings:</b> {playbookSuggestion.warnings.join(" · ")}</p>
                    )}
                    <p className="font-mono text-xs leading-relaxed text-slate-400">
                      {investigation.memories.length} recalled · Avg TTR with memory: {memoryAssistedAverage == null ? "Not available" : formatMinutes(memoryAssistedAverage)} · without: {memoryFreeAverage == null ? "Not available" : formatMinutes(memoryFreeAverage)}
                    </p>
                  </div>
                )}
              </div>

              {/* Desktop / Laptop View */}
              <div className="hidden min-h-[126px] grid-cols-1 gap-4 p-4 lg:grid lg:grid-cols-[1fr_2fr_0.9fr] lg:gap-6 lg:px-6">
                <div className="min-w-0 border-b border-[rgba(34,211,238,0.2)] pb-3 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-5">
                  <div className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(34,211,238,0.35)] bg-[rgba(34,211,238,0.1)] px-2.5 py-1 font-mono text-xs font-semibold tracking-wide text-[#22D3EE]">
                    <Sparkles size={13} aria-hidden="true" /> {suggestionBadgeLabel} · {suggestionConfidenceLabel}
                  </div>
                  <h2 className="mt-2.5 break-words text-sm font-bold leading-snug text-slate-100">
                    {playbookSuggestion.likely_root_cause}
                  </h2>
                </div>
                <div className="flex min-w-0 flex-col justify-center gap-2 lg:px-2">
                  <p className="font-mono text-xs uppercase tracking-wider text-slate-300 font-semibold">Suggested response steps</p>
                  <ol className="grid gap-2 sm:grid-cols-2">
                    {playbookSuggestion.steps.map((step, index) => (
                      <li key={`${index}-${step}`} className="flex min-w-0 gap-2 break-words text-xs leading-relaxed text-slate-200">
                        <span className="mt-px shrink-0 font-mono text-xs text-sky-400 font-bold">{String(index + 1).padStart(2, "0")}</span>
                        <span className="min-w-0 break-words">{step}</span>
                      </li>
                    ))}
                    {playbookSuggestion.steps.length === 0 && <li className="text-xs text-slate-400">The agent did not return action steps.</li>}
                  </ol>
                  {playbookSuggestion.warnings.length > 0 && (
                    <p className="break-words whitespace-normal text-xs leading-relaxed text-amber-300 font-medium"><b>Warnings:</b> {playbookSuggestion.warnings.join(" · ")}</p>
                  )}
                </div>
                <div className="flex min-w-0 flex-col justify-center gap-2 border-t border-[rgba(56,189,248,0.15)] pt-3 font-mono text-xs leading-relaxed text-slate-300 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
                  <span>HISTORICAL RECURRENCE <b className="text-[#22D3EE]">{investigation.memories.length} incidents</b></span>
                  <span>AVG TTR WITH MEMORY <b className="text-xs text-slate-100 font-bold">{memoryAssistedAverage == null ? "Not available" : formatMinutes(memoryAssistedAverage)}</b></span>
                  <span>AVG TTR WITHOUT <b className="text-xs text-slate-100 font-bold">{memoryFreeAverage == null ? "Not available" : formatMinutes(memoryFreeAverage)}</b></span>
                </div>
              </div>
            </>
          ) : (
            <div className="flex min-h-[72px] items-center gap-4 px-4 py-3 md:px-6">
              <div className="hidden shrink-0 items-center gap-2 font-mono text-xs uppercase tracking-wider text-sky-400 font-semibold sm:flex">
                <span className={`h-2 w-2 rounded-full ${streamActive ? "animate-pulse bg-sky-400" : "bg-sky-400"}`} />
                INVESTIGATION SYNTHESIS
              </div>
              <div className="min-w-0 flex-1">
                <p className="mb-1 font-mono text-xs uppercase tracking-wider text-slate-400 sm:hidden">{phaseLabel}</p>
                <div
                  tabIndex={0}
                  role="region"
                  aria-label="Investigation status reasoning"
                  className="max-h-20 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-200"
                  aria-live={investigation.status === "error" ? "assertive" : "polite"}
                >
                  {investigation.status === "error"
                    ? [investigation.error, investigation.reasoning].filter(Boolean).join("\n\n")
                    : investigation.reasoning || (streamActive
                      ? "Streaming latest investigation reasoning and recalling matching incident memories..."
                      : incident?.status === "resolved"
                        ? `Incident ${activeIncidentId} is resolved. Click Re-analyze to trigger a new investigation.`
                        : loadingIncident
                          ? "Loading selected incident context..."
                          : activeIncidentId
                            ? "Click Analyze incident to search past incident memory and stream AI root-cause diagnosis."
                            : "Select an incident or click 'New incident' to begin investigation.")}
                </div>
                {investigation.status === "error" && (
                  <button type="button" onClick={startAnalysis} className="mt-1 font-mono text-xs text-[#22D3EE] underline underline-offset-2">
                    Restart the analysis stream
                  </button>
                )}
                {investigation.status === "complete" && !investigation.consensus && investigation.suggestion && (
                  <p className="mt-1 font-mono text-xs text-[#22D3EE]">Suggestion received · supporting evidence was below multi-incident consensus threshold.</p>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  className="cosmic-quiet min-h-[44px] min-w-[44px] !px-3.5 text-xs font-semibold lg:hidden"
                  aria-expanded={dockExpanded}
                  onClick={() => setDockExpanded((v) => !v)}
                >
                  {dockExpanded ? "Less" : "Details"}
                </button>
                <div className="hidden shrink-0 text-right font-mono text-xs tracking-wider text-slate-400 md:block">
                  {incident?.status === "resolved" ? "INCIDENT RESOLVED" : investigation.status === "idle" ? "READY TO INVESTIGATE" : investigation.status.toUpperCase()}
                </div>
              </div>
            </div>
          )}
          {/* First-Run Onboarding Workflow Helper (Issue #16) */}
          {dockExpanded && !playbookSuggestion && (
            <div className="border-t border-white/10 p-3.5 space-y-2 lg:hidden" role="region" aria-label="Incident response workflow guide">
              <p className="text-xs font-bold uppercase tracking-wider text-teal-200">Aftermath Investigation Workflow</p>
              <div className="space-y-1.5 text-xs text-slate-300">
                <p><b className="text-sky-400">1. Select or Create Incident:</b> Pick an incident or submit alert context and logs.</p>
                <p><b className="text-sky-400">2. Analyze Incident:</b> Run the AI agent to search past memory and diagnose root causes.</p>
                <p><b className="text-sky-400">3. Record Resolution:</b> Document what worked to persist knowledge back into memory.</p>
              </div>
            </div>
          )}
        </section>

        {(apiError || statsError) && (
          <div className="pointer-events-auto fixed left-1/2 top-[76px] z-40 flex w-[min(600px,calc(100%-24px))] -translate-x-1/2 items-start gap-2 rounded-lg border border-[#22D3EE]/40 bg-[rgba(14,19,38,0.98)] px-3.5 py-3 text-xs text-[#A5F3FC] shadow-xl backdrop-blur-xl" role="alert">
            <AlertCircle size={16} className="mt-0.5 shrink-0 text-[#22D3EE]" />
            <p className="min-w-0 flex-1 leading-relaxed">{apiError ?? statsError}</p>
            <button type="button" onClick={retryWorkspace} className="shrink-0 font-semibold underline underline-offset-2">Retry API</button>
          </div>
        )}

        {investigation.status === "complete" && !investigation.consensus && (
          <div className="pointer-events-none fixed left-1/2 top-[68px] z-20 -translate-x-1/2 rounded-full border border-[rgba(168,85,247,0.3)] bg-[rgba(14,19,38,0.95)] px-3.5 py-1.5 font-mono text-xs tracking-wider text-teal-200 max-md:hidden">
            ANALYSIS COMPLETE · SINGLE-INCIDENT MATCH
          </div>
        )}

        {loadingWorkspace && (
          <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-[rgba(5,8,23,0.3)]" aria-live="polite">
            <div className="flex items-center gap-3 rounded-full border border-[rgba(56,189,248,0.25)] bg-[rgba(14,19,38,0.92)] px-5 py-3 font-mono text-xs tracking-wider text-slate-200 backdrop-blur-xl">
              <LoaderCircle size={16} className="animate-spin text-[#38BDF8]" />CONNECTING TO AFTERMATH API
            </div>
          </div>
        )}

        <div className="pointer-events-none fixed bottom-5 right-7 z-20 hidden items-center gap-2 font-mono text-xs uppercase tracking-wider text-slate-400 lg:flex">
          <Radio size={14} className={streamActive ? "animate-pulse text-[#38BDF8]" : "text-slate-400"} /> Incident Memory Engine <span className="mx-1 text-slate-600">·</span><Command size={13} /> Space pause <span className="text-slate-600">·</span> R replay <CircleHelp size={14} />
        </div>
      </section>
    </main>
  );
}
