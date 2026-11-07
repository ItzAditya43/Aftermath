"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  Database,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { SeverityBadge, StatusPill } from "@/components/common/Status";
import ConstellationCanvas from "@/components/constellation/ConstellationCanvas";
import ResolveIncidentModal from "@/components/incidents/ResolveIncidentModal";
import {
  getIncident,
  getIncidentMemories,
  getIncidents,
  getHealth,
  streamIncidentAnalysis,
} from "@/lib/api-client";
import {
  applyAnalysisEvent,
  beginInvestigation,
  hasConsensusEvidence,
  hasSavedConsensusEvidence,
  initialInvestigationState,
  toMemoryEvent,
} from "@/lib/investigation-state";
import type {
  AnalysisEvent,
  IncidentDetail,
  IncidentSummary,
  HealthResponse,
  InvestigationState,
  MemoryReference,
  ResolveResponse,
  SceneMemory,
} from "@/lib/memory-types";

export default function InteractiveHeroPreview() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [memoryRefs, setMemoryRefs] = useState<MemoryReference[]>([]);
  const [investigation, setInvestigation] = useState<InvestigationState>(
    initialInvestigationState
  );
  const [loading, setLoading] = useState(true);
  const [incidentListError, setIncidentListError] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [showResolveModal, setShowResolveModal] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(false);

  // Canvas animation
  const [focusedMemoryId, setFocusedMemoryId] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [animationKey, setAnimationKey] = useState(0);
  const [investigationKey, setInvestigationKey] = useState(0);

  const streamAbortRef = useRef<AbortController | null>(null);
  const investigationRunStateRef = useRef<InvestigationState>(initialInvestigationState);

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);

  // Load incidents list
  useEffect(() => {
    let active = true;
    getHealth().then((value) => { if (active) setHealth(value); }).catch(() => { if (active) setHealth(null); });
    getIncidents()
      .then((data) => {
        if (!active) return;
        setIncidents(data);
        if (data.length > 0) {
          // Use an incident returned by the API; never assume a demo ID exists.
          const target = data.find((i) => i.status !== "resolved") ?? data[0];
          setSelectedId(target.id);
        } else {
          setLoading(false);
        }
      })
      .catch(() => { if (active) { setIncidentListError(true); setLoading(false); } });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;

    Promise.all([
      getIncident(selectedId),
      getIncidentMemories(selectedId).catch(() => []),
    ])
      .then(([detail, mems]) => {
        if (!active) return;
        setIncident(detail);
        setMemoryRefs(mems);

        if (detail.last_suggestion) {
          const memEvents = mems.map(toMemoryEvent);
          const hasConsensus = hasSavedConsensusEvidence(
            detail.last_suggestion,
            memEvents
          );
          const hydrated: InvestigationState = {
            status: "complete",
            phase: "complete",
            memories: memEvents,
            reasoning: "Analysis loaded from institutional memory.",
            suggestion: detail.last_suggestion,
            done: {
              type: "done",
              incident_id: detail.id,
              memories_used: mems.length,
            },
            consensus: hasConsensus,
            error: null,
          };
          investigationRunStateRef.current = hydrated;
          setInvestigation(hydrated);
        } else {
          investigationRunStateRef.current = initialInvestigationState;
          setInvestigation(initialInvestigationState);
        }
        setLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [selectedId]);

  // Handle analysis
  const handleStartAnalysis = async () => {
    if (analyzing || !incident) return;
    setAnalyzing(true);

    if (streamAbortRef.current) {
      streamAbortRef.current.abort();
    }
    const controller = new AbortController();
    streamAbortRef.current = controller;

    const initial = beginInvestigation();
    investigationRunStateRef.current = initial;
    setInvestigation(initial);
    setInvestigationKey((k) => k + 1);

    const onEvent = (event: AnalysisEvent) => {
      investigationRunStateRef.current = applyAnalysisEvent(
        investigationRunStateRef.current,
        event
      );
      setInvestigation({ ...investigationRunStateRef.current });
    };

    try {
      await streamIncidentAnalysis(incident.id, controller.signal, onEvent);
      const finalState = investigationRunStateRef.current;
      const consensus = hasConsensusEvidence(finalState, incident);
      investigationRunStateRef.current = { ...finalState, consensus };
      setInvestigation(investigationRunStateRef.current);

      const [freshDetail, freshMems] = await Promise.all([
        getIncident(incident.id),
        getIncidentMemories(incident.id).catch(() => []),
      ]);
      setIncident(freshDetail);
      setMemoryRefs(freshMems);
    } catch {
      // Aborted or error
    } finally {
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
        setAnalyzing(false);
      }
    }
  };

  // Convert real database incidents and memories into SceneMemory[]
  const canvasMemories = useMemo<SceneMemory[]>(() => {
    const memoryMap = new Map<string, SceneMemory>();

    // 1. Populate all real background incidents from the SQLite database
    for (const inc of incidents) {
      if (inc.id === selectedId) continue;
      memoryMap.set(inc.id, {
        id: inc.id,
        title: inc.title,
        service: inc.service,
        severity: inc.severity,
        status: inc.status,
        createdAt: inc.created_at,
        rootCause: inc.root_cause,
        resolution: null,
        failedAttempts: [],
        timeToResolveMinutes: inc.time_to_resolve_minutes,
        summary: inc.alert_text,
        relevance: null,
        recalled: false,
      });
    }

    // 2. Mark recalled past incidents returned by vector search
    for (const mem of memoryRefs) {
      memoryMap.set(mem.past_incident_id, {
        id: mem.past_incident_id,
        title: mem.past_incident?.title ?? mem.summary,
        service: mem.past_incident?.service ?? incident?.service ?? "unknown",
        severity: mem.past_incident?.severity ?? null,
        status: "resolved",
        createdAt: mem.past_incident?.created_at ?? null,
        rootCause: mem.past_incident?.root_cause ?? null,
        resolution: mem.past_incident?.resolution ?? null,
        failedAttempts: mem.past_incident?.failed_attempts ?? [],
        timeToResolveMinutes: mem.past_incident?.time_to_resolve_minutes ?? null,
        summary: mem.summary,
        relevance: mem.relevance_score,
        recalled: true,
      });
    }

    for (const mem of investigation.memories) {
      memoryMap.set(mem.past_incident_id, {
        id: mem.past_incident_id,
        title: mem.title,
        service: mem.service,
        severity: null,
        status: "resolved",
        createdAt: null,
        rootCause: mem.root_cause,
        resolution: mem.resolution,
        failedAttempts: mem.failed_attempts,
        timeToResolveMinutes: mem.time_to_resolve_minutes,
        summary: mem.summary,
        relevance: mem.relevance_score,
        recalled: true,
      });
    }

    return Array.from(memoryMap.values());
  }, [incidents, selectedId, memoryRefs, investigation.memories, incident?.service]);

  const activeSuggestion =
    investigation.suggestion ?? incident?.last_suggestion ?? null;

  const seeded = /^INC-10(?:0[1-9]|1[0-9]|20)$/.test(selectedId) ? "Seeded sample · " : "";
  const modeLabel = health
    ? health.agent_mode === "mock"
      ? `${seeded}Demo mode · simulated analysis`
      : (health.llm_providers_configured ?? []).length
        ? `${seeded}Real engine · ${(health.llm_providers_configured ?? []).join(", ")}`
        : `${seeded}Real engine · no AI provider, rule-based analysis`
    : "Backend status unavailable";

  return (
    <div className="overflow-hidden rounded-[10px] border border-line bg-surface shadow-[var(--shadow-raised)]">
      {/* Title bar: which incident, which engine */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-line bg-raise px-4 py-3 sm:px-5">
        <span className="label">Live investigation</span>
        <span className="pill" role="status" aria-live="polite">{modeLabel}</span>
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <label htmlFor="preview-incident-select" className="text-xs text-ink-3">
            Sample incident
          </label>
          <select
            id="preview-incident-select"
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="field !min-h-[34px] max-w-[min(420px,60vw)] !py-1 text-[13px]"
            aria-label="Select sample incident"
          >
            {incidents.slice(0, 8).map((inc) => (
              <option key={inc.id} value={inc.id}>
                {inc.id} · {inc.title.slice(0, 56)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="p-4 sm:p-6">
        {/* Incident summary row */}
        <div className="flex flex-col gap-4 border-b border-line pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[13px] font-medium">{incident?.id ?? selectedId}</span>
              {incident?.severity && <SeverityBadge severity={incident.severity} />}
              {incident?.status && <StatusPill status={incident.status} />}
              <span className="svc">{incident?.service ?? "—"}</span>
            </div>
            <h3 className="display mt-2 line-clamp-2 text-[26px] leading-[1.12] sm:text-[30px]">
              {incident?.title ?? (loading ? "Loading incident…" : "No incidents available")}
            </h3>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleStartAnalysis}
              disabled={analyzing || loading || !incident}
              className="btn btn-primary btn-sm"
            >
              {analyzing ? (
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Sparkles className="h-4 w-4" aria-hidden="true" />
              )}
              {activeSuggestion ? "Re-analyze" : "Analyze incident"}
            </button>
            {incident?.status !== "resolved" && (
              <button type="button" onClick={() => setShowResolveModal(true)} disabled={!incident} className="btn btn-quiet btn-sm">
                <Check className="h-4 w-4" aria-hidden="true" />
                Record resolution
              </button>
            )}
            <button
              type="button"
              onClick={() => setDetailsExpanded(!detailsExpanded)}
              className="btn btn-quiet btn-sm"
              aria-expanded={detailsExpanded}
            >
              Alert
              <ChevronDown className={`h-4 w-4 transition-transform ${detailsExpanded ? "rotate-180" : ""}`} aria-hidden="true" />
            </button>
          </div>
        </div>

        {detailsExpanded && incident && (
          <div className="code-block mt-4 max-h-40 overflow-y-auto text-[12.5px] text-lily-ink">{incident.alert_text}</div>
        )}

        {!loading && !incident ? (
          <div className="mt-5 rounded-lg border border-dashed border-line-strong p-8 text-center" role={incidentListError ? "alert" : undefined}>
            <p className="text-sm text-ink">{incidentListError ? "Incident preview unavailable" : "There are no incidents to preview yet."}</p>
            {incidentListError ? (
              <p className="mt-2 text-sm text-ink-3">The incident API could not be reached. Check that the backend is running, then reload.</p>
            ) : (
              <Link href="/incidents?create=true" className="btn btn-primary btn-sm mt-4">
                Create your first incident
              </Link>
            )}
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
            {/* Memory topology, always on a dark instrument panel */}
            <div className="min-w-0">
              <div className="flex items-center justify-between pb-2">
                <span className="label flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full bg-gold-mark" aria-hidden="true" />
                  Memory topology
                </span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setPaused(!paused)}
                    className="btn btn-quiet btn-sm btn-icon"
                    aria-label={paused ? "Resume visualization" : "Pause visualization"}
                  >
                    {paused ? <Play className="h-4 w-4" aria-hidden="true" /> : <Pause className="h-4 w-4" aria-hidden="true" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setPaused(false);
                      setAnimationKey((k) => k + 1);
                    }}
                    className="btn btn-quiet btn-sm btn-icon"
                    aria-label="Replay animation"
                  >
                    <RotateCcw className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </div>
              <div className="relative h-72 w-full overflow-hidden rounded-lg border border-[#232328] bg-black sm:h-80 lg:h-[350px]">
                <ConstellationCanvas
                  memories={canvasMemories}
                  activeIncidentId={selectedId}
                  selectedId={focusedMemoryId}
                  onSelect={setFocusedMemoryId}
                  active={analyzing || investigation.status === "streaming"}
                  consensus={investigation.consensus}
                  animationKey={animationKey}
                  investigationKey={investigationKey}
                  paused={paused}
                  reducedMotion={reducedMotion}
                />
                <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 font-mono text-[10.5px] text-[#8c8c95]">
                  <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#e0193f]" />This incident</span>
                  <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#ffbf57]" />Recalled</span>
                  <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#5f5f68]" />History</span>
                </div>
              </div>
            </div>

            {/* Suggested response */}
            <div className="flex min-w-0 flex-col gap-3">
              <div className="rounded-lg border border-line p-4 sm:p-5">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
                  <span className="text-sm font-medium">Suggested response</span>
                  {activeSuggestion && (
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="pill font-mono">
                        {activeSuggestion.confidence_calibrated === true
                          ? `Calibrated ${Math.round(activeSuggestion.confidence * 100)}%`
                          : `Ranking score ${activeSuggestion.confidence.toFixed(2)}`}
                      </span>
                      {investigation.consensus && (
                        <span className="pill st-resolved">
                          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                          Consensus
                        </span>
                      )}
                    </div>
                  )}
                </div>

                {activeSuggestion ? (
                  <div className="mt-4 space-y-4">
                    <div>
                      <span className="label text-lily-ink">Likely root cause</span>
                      <p className="display mt-1.5 text-[22px] leading-[1.25]">{activeSuggestion.likely_root_cause}</p>
                    </div>
                    {activeSuggestion.steps.length > 0 && (
                      <div>
                        <span className="label">Recommended steps</span>
                        <ol className="mt-2 space-y-1.5 text-[13.5px] text-ink-2">
                          {activeSuggestion.steps.slice(0, 3).map((s, idx) => (
                            <li key={idx} className="grid grid-cols-[22px_minmax(0,1fr)] gap-1">
                              <span className="font-mono text-xs text-ink-4">{String(idx + 1).padStart(2, "0")}</span>
                              <span className="line-clamp-2">{s}</span>
                            </li>
                          ))}
                        </ol>
                      </div>
                    )}
                    {activeSuggestion.warnings.length > 0 && (
                      <div className="callout-avoid">
                        <AlertTriangle className="mt-0.5 h-4 w-4 text-lily-ink" aria-hidden="true" />
                        <span className="line-clamp-3">
                          <strong>Caution.</strong> {activeSuggestion.warnings[0]}
                        </span>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="py-8 text-center text-sm text-ink-3">
                    {analyzing ? (
                      <span className="inline-flex items-center gap-2">
                        <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                        Reading evidence and recalling past incidents…
                      </span>
                    ) : (
                      "Analyze this incident with the configured engine and saved incident history."
                    )}
                  </div>
                )}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gold-wash px-4 py-2.5 text-[13px]">
                <span className="flex items-center gap-2 text-ink-2">
                  <Database className="h-4 w-4 text-gold" aria-hidden="true" />
                  Recalled from memory: <strong className="font-medium text-gold">{memoryRefs.length} related {memoryRefs.length === 1 ? "incident" : "incidents"}</strong>
                </span>
                <Link href={`/incidents/${incident?.id ?? selectedId}`} className="inline-flex min-h-[36px] items-center gap-1 font-medium text-ink hover:underline">
                  Open full workspace
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                </Link>
              </div>
            </div>
          </div>
        )}
      </div>

      <ResolveIncidentModal
        isOpen={showResolveModal}
        incidentId={incident?.id ?? selectedId}
        incidentTitle={incident?.title ?? "Sample incident"}
        suggestion={activeSuggestion}
        onClose={() => setShowResolveModal(false)}
        onResolved={(res: ResolveResponse) => {
          setIncident(res);
        }}
      />
    </div>
  );
}
