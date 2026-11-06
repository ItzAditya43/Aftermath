"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
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
  SEV1: "text-[#FACC15] border-[#FACC15]/35 bg-[#FACC15]/[0.08]",
  SEV2: "text-[#A855F7] border-[#A855F7]/35 bg-[#A855F7]/[0.08]",
  SEV3: "text-[#38BDF8] border-[#38BDF8]/35 bg-[#38BDF8]/[0.08]",
  SEV4: "text-[#7A8DB5] border-[#7A8DB5]/35 bg-[#7A8DB5]/[0.08]",
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
  return <div className={`rounded-[14px] border border-[rgba(168,85,247,0.22)] bg-[rgba(13,16,34,0.86)] p-4 shadow-[0_12px_40px_rgba(3,4,11,0.52)] backdrop-blur-xl ${className}`}>{children}</div>;
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
    <form onSubmit={submit} className="space-y-3" aria-label="Create incident">
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Incident title
        <input name="title" required minLength={3} maxLength={200} className="cosmic-input mt-1" placeholder="Checkout requests failing" />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Service
          <input name="service" required pattern="[a-z0-9][a-z0-9._-]*" maxLength={64} className="cosmic-input mt-1" placeholder="payments-api" />
          <span className="mt-1 block normal-case tracking-normal text-[#7A8DB5]">Use lowercase letters, numbers, dots, underscores, or hyphens.</span>
        </label>
        <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Severity
          <select value={severity} onChange={(event) => setSeverity(event.target.value as Severity)} className="cosmic-input mt-1">
            {severities.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Alert context
        <textarea name="alert_text" required maxLength={10000} rows={3} className="cosmic-input mt-1 resize-y" placeholder="What fired, what is affected, and when it started" />
      </label>
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Logs <span className="normal-case tracking-normal">(optional)</span>
        <textarea name="logs" maxLength={200000} rows={2} className="cosmic-input mt-1 resize-y" placeholder="Relevant log lines" />
      </label>
      <div className="flex gap-2 pt-1">
        <button disabled={busy} className="cosmic-primary flex-1">{busy ? <LoaderCircle className="animate-spin" size={14} /> : <Plus size={14} />} Create incident</button>
        <button type="button" onClick={onCancel} className="cosmic-quiet">Cancel</button>
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
    <form onSubmit={submit} className="space-y-3" aria-label="Record incident resolution">
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Root cause
        <textarea name="root_cause" required maxLength={5000} rows={2} defaultValue={suggestion?.likely_root_cause ?? ""} className="cosmic-input mt-1 resize-y" />
      </label>
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Fix that worked
        <textarea name="fix_that_worked" required maxLength={5000} rows={2} defaultValue={suggestion?.steps.join("\n") ?? ""} className="cosmic-input mt-1 resize-y" />
      </label>
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">What failed <span className="normal-case tracking-normal">(one per line)</span>
        <textarea name="what_failed" rows={2} className="cosmic-input mt-1 resize-y" />
      </label>
      <label className="block text-[10px] uppercase tracking-[0.14em] text-[#7A8DB5]">Notes <span className="normal-case tracking-normal">(optional)</span>
        <textarea name="notes" maxLength={10000} rows={2} className="cosmic-input mt-1 resize-y" />
      </label>
      <div className="flex gap-2">
        <button disabled={busy} className="cosmic-primary flex-1">{busy ? <LoaderCircle className="animate-spin" size={14} /> : <Check size={14} />} Save resolution</button>
        <button type="button" onClick={onCancel} className="cosmic-quiet">Cancel</button>
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
    incidents.forEach((item) => byId.set(item.id, {
      id: item.id,
      title: item.title,
      service: item.service,
      severity: item.severity,
      status: item.status,
      createdAt: item.created_at,
      rootCause: item.root_cause,
      resolution: null,
      failedAttempts: [],
      timeToResolveMinutes: item.time_to_resolve_minutes,
      summary: item.alert_text,
      relevance: null,
      recalled: false,
    }));

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
  }, [incidents, incident, activeIncidentId, investigation.memories, memoryRefs]);

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
    : investigation.consensus ? "CONSENSUS EVIDENCE THRESHOLD MET"
    : investigation.status === "connecting" ? "CONNECTING TO INVESTIGATION"
      : investigation.status === "streaming" ? (investigation.phase === "recalling" ? `RECALLING ${investigation.memories.length} INCIDENT${investigation.memories.length === 1 ? "" : "S"}` : "SYNTHESIZING EVIDENCE")
      : investigation.phase === "recalling" ? `RECALLING ${investigation.memories.length} INCIDENT${investigation.memories.length === 1 ? "" : "S"}`
        : investigation.phase === "synthesizing" ? "SYNTHESIZING EVIDENCE"
          : investigation.status === "complete" ? "INVESTIGATION COMPLETE · NO CONSENSUS"
            : investigation.status === "error" ? "STREAM INTERRUPTED"
              : incident?.status === "resolved" ? "RESOLVED · READY TO RE-ANALYZE" : "MEMORY FIELD READY";

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

        <header className="pointer-events-none absolute inset-x-0 top-0 z-30 flex h-16 items-center justify-between border-b border-[rgba(56,189,248,0.12)] bg-[rgba(3,4,11,0.72)] px-4 backdrop-blur-md md:px-7">
          <div className="flex min-w-0 items-center gap-3">
            <div className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-[#7C3AED] to-[#A855F7] text-sm text-white shadow-[0_0_20px_rgba(124,58,237,0.45)]">⌁</div>
            <span className="shrink-0 text-sm font-extrabold tracking-[-0.05em]">AFTER<span className="text-[#38BDF8]">MATH</span></span>
            {selectedSummary && <span className={`hidden rounded-full border px-2.5 py-1 font-mono text-[9px] tracking-[0.13em] sm:inline-flex ${severityStyle[selectedSummary.severity]}`}>{selectedSummary.severity} · {selectedSummary.id}</span>}
          </div>
          <div className="hidden items-center gap-2 rounded-full border border-[rgba(56,189,248,0.2)] bg-[rgba(13,16,34,0.88)] px-4 py-1.5 font-mono text-[9px] tracking-[0.12em] text-[#A6A0BC] backdrop-blur-lg md:flex">
            <span className={`h-1.5 w-1.5 rounded-full ${investigation.consensus ? "bg-[#FACC15] shadow-[0_0_10px_#FACC15]" : streamActive ? "animate-pulse bg-[#38BDF8] shadow-[0_0_8px_#38BDF8]" : "bg-[#7A8DB5]"}`} />
            STATUS · <span className={investigation.consensus ? "text-[#FACC15]" : "text-[#38BDF8]"}>{phaseLabel}</span>
          </div>
          <div className="pointer-events-auto flex items-center gap-2 md:gap-4">
            <div className="hidden items-center gap-4 font-mono text-[9px] tracking-[0.08em] text-[#7A8DB5] lg:flex">
              <span>INCIDENTS <b className="text-[#DDD6FE]">{apiOffline ? "—" : stats?.total_incidents ?? "—"}</b></span>
              <span>OPEN <b className="text-[#DDD6FE]">{apiOffline || !stats ? "—" : stats.open + stats.investigating}</b></span>
              <span>AVG TTR <b className="text-[#DDD6FE]">{apiOffline || stats?.avg_time_to_resolve_minutes == null ? "—" : formatMinutes(stats.avg_time_to_resolve_minutes)}</b></span>
            </div>
            <button type="button" className="cosmic-quiet !px-2.5" onClick={openCreateIncident} disabled={apiOffline} aria-expanded={showCreate}><Plus size={13} /> <span className="hidden sm:inline">New incident</span></button>
          </div>
        </header>

        <nav className="pointer-events-auto absolute inset-x-2 top-[70px] z-30 flex items-center justify-center gap-2 md:hidden" aria-label="Incident panels">
          <button type="button" className="cosmic-quiet min-h-10 flex-1 !justify-start !px-3" aria-expanded={mobilePanel === "incident"} aria-controls="incident-context" onClick={() => setMobilePanel((panel) => panel === "incident" ? null : "incident")}>
            <span className="truncate">{selectedSummary?.id ?? "Incident"}</span><ChevronDown size={13} className={`ml-auto shrink-0 transition-transform ${mobilePanel === "incident" ? "rotate-180" : ""}`} />
          </button>
          <button type="button" className="cosmic-quiet min-h-10 shrink-0 !px-3" aria-expanded={mobilePanel === "recall"} aria-controls="recall-spine" onClick={() => setMobilePanel((panel) => panel === "recall" ? null : "recall")}>
            Recall <span className="font-mono text-[#FACC15]">{recalls.length}</span><ChevronDown size={13} className={`transition-transform ${mobilePanel === "recall" ? "rotate-180" : ""}`} />
          </button>
        </nav>

        <aside ref={incidentPanelRef} id="incident-context" className={`incident-context-panel pointer-events-none absolute left-7 top-[78px] z-20 flex w-[260px] flex-col gap-3 overflow-y-auto overscroll-contain max-lg:left-3 max-lg:top-[74px] max-lg:w-[260px] ${mobilePanel === "incident" ? "max-md:left-2 max-md:right-2 max-md:top-[114px] max-md:z-40 max-md:w-auto" : "max-md:hidden"}`} aria-label="Incident context">
          <FlankCard className="pointer-events-auto">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-[#7A8DB5]">Incident workspace</p>
              <button type="button" onClick={openCreateIncident} disabled={apiOffline} aria-label="Create incident" className="grid min-h-11 min-w-11 place-items-center rounded text-[#A855F7] hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#A855F7] disabled:cursor-not-allowed disabled:opacity-40"><Plus size={14} /></button>
            </div>
            {incidentListAvailability === "offline" ? (
              <div className="py-3">
                <p className="text-sm font-semibold">Can&apos;t reach the API.</p>
                <p className="mt-1 text-[11px] leading-relaxed text-[#7A8DB5]">Incident data and memory statistics are unavailable while disconnected.</p>
                <button type="button" onClick={retryWorkspace} className="cosmic-quiet mt-3 w-full">Retry API</button>
              </div>
            ) : incidentListAvailability === "error" ? (
              <div className="py-3">
                <p className="text-sm font-semibold">Could not load incidents.</p>
                <button type="button" onClick={retryWorkspace} className="cosmic-quiet mt-3 w-full">Retry API</button>
              </div>
            ) : showCreate ? (
              <CreateIncidentForm busy={busyAction} onCancel={() => setShowCreate(false)} onSubmit={onCreateIncident} />
            ) : loadingWorkspace ? (
              <div className="flex items-center gap-2 py-4 text-xs text-[#7A8DB5]"><LoaderCircle size={14} className="animate-spin" />Loading incident memory</div>
            ) : incidentListAvailability === "empty" ? (
              <div className="py-2">
                <p className="text-sm font-semibold">No incidents are available.</p>
                <p className="mt-1 text-[11px] leading-relaxed text-[#7A8DB5]">Create an incident to start an investigation.</p>
                <button type="button" onClick={openCreateIncident} className="cosmic-primary mt-3 w-full"><Plus size={14} /> Create incident</button>
              </div>
            ) : (
              <>
                <label htmlFor="incident-picker" className="sr-only">Select incident</label>
                <div className="relative">
                  <select id="incident-picker" value={activeIncidentId ?? ""} onChange={(event) => { if (event.target.value) selectIncident(event.target.value); }} className="cosmic-input appearance-none pr-8 text-[11px] font-semibold" aria-label="Select incident">
                    {activeIncidentId && !visibleIncidents.some((item) => item.id === activeIncidentId) && <option value={activeIncidentId} disabled>{selectedSummary ? `${selectedSummary.id} · hidden by search` : "Selected incident"}</option>}
                    {!activeIncidentId && <option value="" disabled>{visibleIncidents.length ? "Choose an incident" : "No matching incidents"}</option>}
                    {visibleIncidents.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.title}</option>)}
                  </select>
                  <ChevronDown size={13} className="pointer-events-none absolute right-3 top-3 text-[#7A8DB5]" />
                </div>
                <div className="relative mt-2"><Search size={13} className="absolute left-3 top-3 text-[#A855F7]" /><input type="search" aria-label="Search incidents by ID, title, or service" value={incidentSearch} onChange={(event) => setIncidentSearch(event.target.value)} className="cosmic-input !pl-8 text-[11px]" placeholder="Search incidents" /></div>
                <h1 className="mt-3 line-clamp-3 text-[14px] font-bold leading-snug tracking-[-0.02em]">{incident?.title ?? selectedSummary?.title ?? "Loading incident…"}</h1>
                <p className="mt-1 font-mono text-[10px] text-[#38BDF8]">{incident?.service ?? selectedSummary?.service ?? ""}</p>
                <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[9px] text-[#7A8DB5]">
                  {selectedSummary && <span className={`rounded border px-2 py-1 font-mono ${severityStyle[selectedSummary.severity]}`}>{selectedSummary.severity}</span>}
                  <span className="rounded border border-white/10 px-2 py-1 capitalize">{incident?.status ?? selectedSummary?.status ?? "loading"}</span>
                  <span className="w-full truncate">{formatTimestamp(incident?.created_at ?? selectedSummary?.created_at)}</span>
                </div>
                <div className="mt-3 border-t border-white/[0.08] pt-3">
                  <p className="line-clamp-3 text-[10px] leading-relaxed text-[#A9B8D1]">{incident?.alert_text ?? selectedSummary?.alert_text ?? ""}</p>
                  {loadingIncident && <p className="mt-2 flex items-center gap-1.5 text-[9px] text-[#7A8DB5]"><LoaderCircle size={11} className="animate-spin" />Refreshing incident context</p>}
                </div>
                {(incident?.root_cause || incident?.resolution || incident?.failed_attempts.length || incident?.resolved_at) && <div className="mt-3 rounded-lg border border-[rgba(245,199,106,0.2)] bg-[rgba(245,199,106,0.045)] p-3" aria-live="polite">
                  <p className="font-mono text-[9px] uppercase tracking-wider text-[#FACC15]">Previously saved resolution</p>
                  {incident.resolved_at && <p className="mt-1 font-mono text-[9px] text-[#38BDF8]">Resolved {formatTimestamp(incident.resolved_at)}</p>}
                  {incident.root_cause && <p className="mt-1 text-[10px] leading-relaxed text-[#E8D5A9]">Cause: {incident.root_cause}</p>}
                  {incident.resolution && <p className="mt-1 text-[10px] leading-relaxed text-[#C4D0E6]">Fix: {incident.resolution}</p>}
                  {incident.failed_attempts.length > 0 && <div className="mt-1 text-[9px] leading-relaxed text-[#FEF08A]"><b>Previously failed attempts:</b><ul className="list-disc pl-4">{incident.failed_attempts.map((attempt, index) => <li key={`${index}-${attempt}`}>{attempt}</li>)}</ul></div>}
                </div>}
                {resolutionFeedback?.incidentId === activeIncidentId && <div className={`mt-3 rounded-lg border p-3 text-[10px] leading-relaxed ${resolutionFeedback.memorySaved === true ? "border-[#FACC15]/30 bg-[#FACC15]/[0.06] text-[#FEF08A]" : "border-[#A855F7]/30 bg-[#A855F7]/[0.06] text-[#DDD6FE]"}`} role="status">
                  <p className="font-mono text-[9px] uppercase tracking-wider">Resolution recorded · {resolutionFeedback.incidentId}</p>
                  <p className="mt-1">{resolutionFeedback.memorySaved === true ? "Memory successfully saved." : resolutionFeedback.memorySaved === false ? "Memory save unsuccessful; the incident resolution was recorded." : "The API did not confirm whether memory was saved."}</p>
                </div>}
                <button type="button" onClick={startAnalysis} disabled={!incident || analysisRequestActive} aria-busy={analysisRequestActive} className="cosmic-primary mt-3 w-full disabled:cursor-not-allowed disabled:opacity-55">
                  {analysisRequestActive ? <LoaderCircle size={14} className="animate-spin" /> : <Sparkles size={14} />}
                  {analyzeButtonLabel}
                </button>
                {analysisRequestActive && <p className="mt-1 font-mono text-[9px] text-[#38BDF8]" role="status">Streaming latest analysis for {activeIncidentId}.</p>}
                {investigation.status === "error" && <p className="mt-1 break-words text-[10px] leading-relaxed text-[#FEF08A]" role="alert">Latest analysis failed: {investigation.error}</p>}
                {incident && incident.status !== "resolved" && <button type="button" onClick={() => setShowResolve((value) => !value)} className="cosmic-quiet mt-2 w-full" aria-expanded={showResolve}><ShieldCheck size={13} /> Record resolution</button>}
                {showResolve && <div className="mt-3 border-t border-white/[0.08] pt-3"><ResolveIncidentForm busy={busyAction} suggestion={investigation.suggestion} onCancel={() => setShowResolve(false)} onSubmit={onResolveIncident} /></div>}
                {focusedMemory && <div className="mt-3 rounded-lg border border-[rgba(250,204,21,0.28)] bg-[rgba(250,204,21,0.045)] p-3" aria-live="polite">
                  <div className="flex items-center justify-between gap-2"><span className="font-mono text-[9px] text-[#FACC15]">HISTORICAL MEMORY · LOCKED IN FOCUS · {focusedMemory.id}</span><button type="button" onClick={() => selectMemory(null)} aria-label="Clear selected memory" className="grid min-h-10 min-w-10 place-items-center text-[#DDD6FE] hover:text-white"><X size={14} /></button></div>
                  <p className="mt-1 font-mono text-[8px] text-[#38BDF8]">ACTIVE INCIDENT · {activeIncidentId ?? "NONE"}</p>
                  <p className="mt-1 text-[10px] font-semibold leading-snug">{focusedMemory.title}</p>
                  {focusedMemory.rootCause && <p className="mt-1 text-[9px] text-[#A9B8D1]"><b>Root cause:</b> {focusedMemory.rootCause}</p>}
                  {focusedMemory.failedAttempts.length > 0 && <div className="mt-1 text-[9px] leading-relaxed text-[#FEF08A]"><b>Prior attempts · did not resolve:</b><ul className="list-disc pl-4">{focusedMemory.failedAttempts.map((attempt, index) => <li key={`${index}-${attempt}`}>{attempt}</li>)}</ul></div>}
                  {focusedMemory.resolution && <p className="mt-1 text-[9px] text-[#FEF08A]"><b>Verified fix:</b> {focusedMemory.resolution}</p>}
                </div>}
                {previouslyRecalled.length > 0 && <details className="mt-3 rounded-lg border border-white/[0.08] p-2.5">
                  <summary className="cursor-pointer font-mono text-[9px] uppercase tracking-wider text-[#A6A0BC]">Previously recalled memories · {previouslyRecalled.length}</summary>
                  <ul className="mt-2 max-h-32 space-y-1.5 overflow-y-auto">
                    {previouslyRecalled.map((reference) => <li key={reference.past_incident_id} className="break-words text-[9px] leading-relaxed text-[#9EACC9]">
                      <b className="font-mono text-[#C4B5FD]">{reference.past_incident_id}</b> · {reference.past_incident?.title ?? reference.summary} · {Math.round(reference.relevance_score * 100)}% match
                    </li>)}
                  </ul>
                </details>}
                <details className="mt-3 border-t border-[#A855F7]/20 pt-2">
                  <summary className="cursor-pointer py-2 font-mono text-[9px] tracking-wider text-[#DDD6FE]">Accessible memory index · {sceneMemories.length}</summary>
                  <div className="mt-1 max-h-[28vh] space-y-1 overflow-y-auto" aria-label="Memory selection list">
                    {sceneMemories.map((memory) => <button key={memory.id} type="button" aria-pressed={focusedMemoryId === memory.id} onClick={() => selectMemory(focusedMemoryId === memory.id ? null : memory.id)} className={`flex min-h-10 w-full items-center justify-between gap-2 rounded-md border px-2 text-left text-[10px] ${focusedMemoryId === memory.id ? "border-[#FACC15]/50 bg-[#FACC15]/[0.08] text-[#FEF08A]" : "border-white/[0.06] text-[#DDD6FE] hover:border-[#A855F7]/45"}`}>
                      <span className="min-w-0"><b className="mr-2 font-mono text-[#A855F7]">{memory.id}</b><span className="break-words">{memory.title}</span></span>
                      {memory.relevance != null && <span className="shrink-0 font-mono text-[#FACC15]">{Math.round(memory.relevance * 100)}%</span>}
                    </button>)}
                  </div>
                </details>
              </>
            )}
          </FlankCard>

          <FlankCard className="pointer-events-auto !p-3 max-lg:hidden">
            <div className="flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.14em] text-[#7A8DB5]"><span>Memory bank</span><b className="text-[#38BDF8]">{apiOffline ? "—" : `${sceneMemories.length} incidents`}</b></div>
            <div className="mt-2 h-[3px] overflow-hidden rounded-full bg-[rgba(168,85,247,0.15)]"><div className="h-full bg-gradient-to-r from-[#7C3AED] via-[#A855F7] to-[#FACC15] transition-[width] duration-700" style={{ width: `${sceneMemories.length ? recalls.length / sceneMemories.length * 100 : 0}%` }} /></div>
            <div className="mt-2 flex items-center justify-between text-[9px] text-[#7A8DB5]"><span>{recalls.length} recalled memories</span><span>{investigation.status === "idle" ? "Historical" : "Current run"}</span></div>
            <div className="mt-2 flex flex-wrap gap-[4px]" aria-label={`${recalls.length} recalled memories`}>
              {recalls.slice(0, 32).map((memory) => <span key={memory.id} title={`${memory.id} · ${Math.round((memory.relevance ?? 0) * 100)}% relevance`} className={`h-[5px] w-[5px] rounded-full ${investigation.consensus ? "bg-[#FACC15] shadow-[0_0_7px_#FACC15]" : "bg-[#38BDF8] shadow-[0_0_6px_#38BDF8]"}`} />)}
              {recalls.length === 0 && <span className="text-[9px] text-[#60708E]">Recall events will appear here.</span>}
            </div>
          </FlankCard>
        </aside>

        <aside id="recall-spine" className={`pointer-events-none absolute right-7 top-[78px] z-20 w-[230px] max-h-[calc(100svh-206px)] max-lg:right-3 max-lg:top-[74px] max-lg:w-[230px] ${mobilePanel === "recall" ? "max-md:left-2 max-md:right-2 max-md:top-[114px] max-md:z-40 max-md:w-auto max-md:max-h-[calc(100svh-250px)] max-md:overflow-y-auto max-md:overscroll-contain" : "max-md:hidden"}`} aria-label="Tactical recall spine">
          <FlankCard className="pointer-events-auto max-h-[inherit] overflow-hidden !p-3 sm:!p-4">
            <div className="flex items-center justify-between gap-2">
              <div><p className="font-mono text-[9px] uppercase tracking-[0.15em] text-[#A6A0BC]">{investigation.status === "idle" ? "Previously recalled memories" : "Latest analysis recall"}</p><p className="mt-1 font-mono text-[10px] text-[#FACC15]">{recalls.length} matched incident{recalls.length === 1 ? "" : "s"}</p></div>
              <span className="h-2 w-2 rounded-full bg-[#FACC15] shadow-[0_0_9px_#FACC15]" />
            </div>
            <div className="mt-3 max-h-[min(48vh,390px)] space-y-2 overflow-y-auto pr-1" aria-live="polite">
                {recalls.length === 0 ? <div className="rounded-lg border border-dashed border-white/10 px-3 py-6 text-center text-[10px] leading-relaxed text-[#7383A1]">Recalled incident evidence appears here as the backend sends memory events.</div> : recalls.map((memory) => (
                <button type="button" key={memory.id} onClick={() => selectMemory(focusedMemoryId === memory.id ? null : memory.id)} aria-pressed={focusedMemoryId === memory.id} className={`group w-full rounded-lg border p-2.5 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#FACC15] ${focusedMemoryId === memory.id ? "border-[#FACC15]/55 bg-[#FACC15]/[0.08]" : "border-white/[0.07] bg-[#0D1022]/55 hover:border-[#A855F7]/50"}`}>
                  <span className="flex items-center justify-between gap-1 font-mono text-[9px]"><b className="text-[#DDD6FE]">{memory.id}</b><span className="text-[#FACC15]">{memory.relevance == null ? "MATCH" : `${Math.round(memory.relevance * 100)}%`}</span></span>
                  <span className="mt-1 block line-clamp-2 text-[10px] font-medium leading-snug text-[#D7E1F3]">{memory.title}</span>
                  <span className="mt-1 flex items-center justify-between gap-2 text-[9px] text-[#7A8DB5]"><span className="truncate">{memory.service}</span><span>{formatMinutes(memory.timeToResolveMinutes)}</span></span>
                  <span className="mt-1 block h-[2px] rounded-full bg-white/[0.06]"><i className="block h-full bg-gradient-to-r from-[#7C3AED] via-[#A855F7] to-[#FACC15]" style={{ width: `${Math.max(4, (memory.relevance ?? 0.12) * 100)}%` }} /></span>
                </button>
              ))}
            </div>
          </FlankCard>
          <div className="pointer-events-auto mt-2 flex justify-end gap-2">
            <button type="button" onClick={() => setPaused((value) => !value)} aria-pressed={paused} className="cosmic-quiet !px-2.5" aria-label={paused ? "Resume constellation animation" : "Pause constellation animation"}>{paused ? <Play size={13} /> : <Pause size={13} />}<span className="hidden sm:inline">{paused ? "Resume" : "Pause"}</span></button>
            <button type="button" onClick={() => { setPaused(false); setAnimationKey((value) => value + 1); }} className="cosmic-quiet !px-2.5" aria-label="Replay the current investigation visualization"><RotateCcw size={13} /><span className="hidden sm:inline">Replay</span></button>
          </div>
        </aside>

        <div className="pointer-events-auto absolute right-2 top-[120px] z-30 flex gap-1 md:hidden">
          <button type="button" onClick={() => setPaused((value) => !value)} aria-pressed={paused} className="cosmic-quiet min-h-10 !px-2.5" aria-label={paused ? "Resume constellation animation" : "Pause constellation animation"}>{paused ? <Play size={13} /> : <Pause size={13} />}</button>
          <button type="button" onClick={() => { setPaused(false); setAnimationKey((value) => value + 1); }} className="cosmic-quiet min-h-10 !px-2.5" aria-label="Replay the current investigation visualization"><RotateCcw size={13} /></button>
        </div>

        <div className="pointer-events-none absolute left-1/2 z-10 -translate-x-1/2 text-center max-md:hidden" style={{ top: "calc(44% - 130px)" }}>
          <span className="rounded-full border border-[rgba(168,85,247,0.2)] bg-[rgba(3,4,11,0.72)] px-3 py-1 font-mono text-[8px] uppercase tracking-[0.22em] text-[#A6A0BC] backdrop-blur-sm">Incident singularity</span>
        </div>

        <section className={`cinematic-dock fixed bottom-2 left-1/2 z-30 w-[min(1180px,calc(100%-16px))] -translate-x-1/2 rounded-[14px] border border-[rgba(168,85,247,0.28)] bg-[rgba(13,16,34,0.94)] shadow-[0_24px_80px_rgba(0,0,0,0.62)] backdrop-blur-2xl transition-all duration-300 min-h-[72px] ${dockExpanded ? "dock-expanded" : ""}`} aria-label="Investigation synthesis console">
          {playbookSuggestion ? (
            <>
            <div className="grid grid-cols-1 gap-2 p-3 lg:hidden">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <div className="min-w-0"><div className="font-mono text-[9px] tracking-[0.04em] text-[#FACC15]">{suggestionBadgeLabel} · {suggestionConfidenceLabel}</div><p className="mt-1 line-clamp-1 text-[11px] font-semibold">{playbookSuggestion.likely_root_cause}</p></div>
                <button type="button" className="cosmic-quiet min-h-10 shrink-0 !px-3" aria-expanded={dockExpanded} onClick={() => setDockExpanded((value) => !value)}>{dockExpanded ? "Less" : "Details"}</button>
              </div>
              {dockExpanded && <div className="space-y-3 border-t border-white/10 pt-3">
                <div><p className="mb-2 font-mono text-[9px] uppercase tracking-wider text-[#A855F7]">Suggested response · backend analysis</p><ol className="space-y-2">{playbookSuggestion.steps.map((step, index) => <li key={`${index}-${step}`} className="flex gap-2 text-[11px] leading-relaxed text-[#C4D0E6]"><span className="text-[#FACC15]">0{index + 1}</span><span>{step}</span></li>)}</ol>{playbookSuggestion.steps.length === 0 && <p className="text-[11px] text-[#7A8DB5]">The backend did not return action steps.</p>}</div>
                {playbookSuggestion.warnings.length > 0 && <p className="text-[10px] text-[#FACC15]"><b>Warnings:</b> {playbookSuggestion.warnings.join(" · ")}</p>}
                <p className="font-mono text-[9px] leading-relaxed text-[#9EACC9]">{investigation.memories.length} recalled · Avg TTR with memory: {memoryAssistedAverage == null ? "Not available" : formatMinutes(memoryAssistedAverage)} · without: {memoryFreeAverage == null ? "Not available" : formatMinutes(memoryFreeAverage)}</p>
              </div>}
            </div>
            <div className="hidden min-h-[126px] grid-cols-1 gap-4 p-4 lg:grid lg:grid-cols-[1fr_2.2fr_0.8fr] lg:gap-6 lg:px-6">
              <div className="min-w-0 border-b border-[rgba(250,204,21,0.15)] pb-3 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-5">
                <div className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(250,204,21,0.3)] bg-[rgba(250,204,21,0.09)] px-2.5 py-1 font-mono text-[9px] tracking-[0.08em] text-[#FACC15]"><Sparkles size={11} /> {suggestionBadgeLabel} · {suggestionConfidenceLabel}</div>
                <h2 className="mt-2 break-words text-[14px] font-bold leading-snug text-[#DDD6FE]">{playbookSuggestion.likely_root_cause}</h2>
              </div>
              <div className="flex min-w-0 flex-col justify-center gap-2 lg:px-2">
                <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[#A6A0BC]">Latest analysis · suggested response</p>
                <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {playbookSuggestion.steps.map((step, index) => <li key={`${index}-${step}`} className="flex min-w-0 gap-2 break-words text-[12px] leading-relaxed text-[#DDD6FE]"><span className="mt-px shrink-0 font-mono text-[10px] text-[#38BDF8]">{String(index + 1).padStart(2, "0")}</span><span className="min-w-0 break-words">{step}</span></li>)}
                  {playbookSuggestion.steps.length === 0 && <li className="text-[12px] text-[#A6A0BC]">The backend did not return action steps.</li>}
                </ol>
                {playbookSuggestion.warnings.length > 0 && <p className="break-words whitespace-normal text-[11px] leading-relaxed text-[#FEF08A]"><b>Warnings:</b> {playbookSuggestion.warnings.join(" · ")}</p>}
              </div>
              <div className="flex min-w-0 flex-col justify-center gap-2 border-t border-[rgba(56,189,248,0.14)] pt-3 font-mono text-[10px] leading-relaxed text-[#A6A0BC] lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
                <span>HISTORICAL RECURRENCE <b className="text-[#FACC15]">{investigation.memories.length} incidents</b></span>
                <span>AVG TTR WITH MEMORY <b className="text-[12px] text-[#DDD6FE]">{memoryAssistedAverage == null ? "Not available" : formatMinutes(memoryAssistedAverage)}</b></span>
                <span>AVG TTR WITHOUT <b className="text-[12px] text-[#DDD6FE]">{memoryFreeAverage == null ? "Not available" : formatMinutes(memoryFreeAverage)}</b></span>
              </div>
            </div>
            </>
          ) : (
            <div className="flex min-h-[72px] items-center gap-4 px-4 py-3 md:px-6">
              <div className="hidden shrink-0 items-center gap-2 font-mono text-[9px] uppercase tracking-[0.15em] text-[#38BDF8] sm:flex"><span className={`h-1.5 w-1.5 rounded-full ${streamActive ? "animate-pulse bg-[#38BDF8]" : "bg-[#38BDF8]"}`} />AI NEURAL SYNTHESIS · LATEST ANALYSIS</div>
              <div className="min-w-0 flex-1">
                <p className="mb-1 font-mono text-[8px] uppercase tracking-[0.16em] text-[#7A8DB5] sm:hidden">{phaseLabel}</p>
                <p className="max-h-[5rem] overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-[#C6D4EC]" aria-live={investigation.status === "error" ? "assertive" : "polite"} role={investigation.status === "error" ? "alert" : undefined}>
                  {investigation.status === "error"
                    ? [investigation.error, investigation.reasoning].filter(Boolean).join("\n\n")
                    : investigation.reasoning || investigation.suggestion?.likely_root_cause || (investigation.status === "complete" ? "Analysis completed, but the returned evidence did not meet the consensus threshold." : streamActive ? "Streaming the latest analysis and recalled evidence." : incident?.status === "resolved" ? `Incident ${activeIncidentId} is resolved. Re-analyze to request a new investigation.` : loadingIncident ? "Loading the selected incident context." : activeIncidentId ? "Select Analyze incident to stream backend reasoning and recalled evidence." : "Create or select an incident to begin.")}
                </p>
                {investigation.status === "error" && <button type="button" onClick={startAnalysis} className="mt-1 font-mono text-[9px] text-[#FACC15] underline underline-offset-2">Restart the analysis stream</button>}
                {investigation.status === "complete" && !investigation.consensus && investigation.suggestion && <p className="mt-1 font-mono text-[8px] text-[#FACC15]">Suggestion received · supporting evidence was insufficient for consensus.</p>}
              </div>
              <div className="hidden shrink-0 text-right font-mono text-[8px] tracking-[0.1em] text-[#657A9F] md:block">{incident?.status === "resolved" ? "INCIDENT RESOLVED" : investigation.status === "idle" ? "AWAITING INVESTIGATION" : investigation.status.toUpperCase()}</div>
            </div>
          )}
        </section>

        {(apiError || statsError) && <div className="pointer-events-auto fixed left-1/2 top-[76px] z-40 flex w-[min(600px,calc(100%-24px))] -translate-x-1/2 items-start gap-2 rounded-lg border border-[#FACC15]/30 bg-[rgba(13,16,34,0.97)] px-3 py-2.5 text-[10px] text-[#FEF08A] shadow-xl backdrop-blur-xl" role="alert">
          <AlertCircle size={14} className="mt-0.5 shrink-0 text-[#FACC15]" /><p className="min-w-0 flex-1 leading-relaxed">{apiError ?? statsError}</p><button type="button" onClick={retryWorkspace} className="shrink-0 underline underline-offset-2">Retry API</button>
        </div>}

        {investigation.status === "complete" && !investigation.consensus && <div className="pointer-events-none fixed left-1/2 top-[68px] z-20 -translate-x-1/2 rounded-full border border-[rgba(168,85,247,0.3)] bg-[rgba(13,16,34,0.94)] px-3 py-1 font-mono text-[8px] tracking-[0.12em] text-[#A855F7] max-md:hidden">ANALYSIS COMPLETE · CONSENSUS NOT ESTABLISHED</div>}

        {loadingWorkspace && <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-[rgba(5,8,23,0.28)]" aria-live="polite"><div className="flex items-center gap-3 rounded-full border border-[rgba(56,189,248,0.25)] bg-[rgba(11,16,32,0.9)] px-5 py-3 font-mono text-[10px] tracking-[0.16em] text-[#A9B8D1] backdrop-blur-xl"><LoaderCircle size={15} className="animate-spin text-[#38BDF8]" />CONNECTING TO AFTERMATH API</div></div>}

        <div className="pointer-events-none fixed bottom-5 right-7 z-20 hidden items-center gap-2 font-mono text-[8px] uppercase tracking-[0.14em] text-[#536584] lg:flex"><Radio size={11} className={streamActive ? "animate-pulse text-[#38BDF8]" : "text-[#536584]"} /> Backend-backed incident memory <span className="mx-1 text-[#27364D]">·</span><Command size={10} /> Space pause <span className="text-[#27364D]">·</span> R replay <CircleHelp size={11} /></div>
      </section>
    </main>
  );
}
