"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, Check, LoaderCircle, ShieldCheck, Sparkles } from "lucide-react";
import { SeverityBadge, StatusPill } from "@/components/common/Status";
import InvestigationReportPanel from "@/components/investigation/InvestigationReportPanel";
import InvestigationGraph from "@/components/investigation/InvestigationGraph";
import ResolveIncidentModal from "@/components/incidents/ResolveIncidentModal";
import {
  getIncident,
  getIncidentInvestigation,
  getIncidentMemories,
  streamIncidentAnalysis,
} from "@/lib/api-client";
import {
  applyAnalysisEvent,
  beginInvestigation,
  failInvestigation,
  hasConsensusEvidence,
  hasSavedConsensusEvidence,
  initialInvestigationState,
  toMemoryEvent,
} from "@/lib/investigation-state";
import type {
  AnalysisEvent,
  IncidentDetail,
  InvestigationState,
  MemoryReference,
  ResolveResponse,
  InvestigationReport,
} from "@/lib/memory-types";

function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null || isNaN(minutes)) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = Math.floor(minutes / 60);
  const rem = Math.round(minutes % 60);
  return rem > 0 ? `${hours}h ${rem}m` : `${hours}h`;
}

function formatTimestamp(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

interface InvestigationWorkspaceProps {
  incidentId: string;
}

export default function InvestigationWorkspace({
  incidentId,
}: InvestigationWorkspaceProps) {
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [memoryRefs, setMemoryRefs] = useState<MemoryReference[]>([]);
  const [investigationReport, setInvestigationReport] = useState<InvestigationReport | null>(null);
  const [investigation, setInvestigation] = useState<InvestigationState>(
    initialInvestigationState
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [showResolveModal, setShowResolveModal] = useState(false);

  const streamAbortRef = useRef<AbortController | null>(null);
  const investigationRunStateRef = useRef<InvestigationState>(initialInvestigationState);

  // Load incident details and memories
  const loadIncidentData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const detailData = await getIncident(incidentId);
      const [memsData, reportData] = await Promise.all([
        getIncidentMemories(incidentId).catch(() => []),
        detailData.last_suggestion ? getIncidentInvestigation(incidentId).catch(() => null) : null,
      ]);
      setIncident(detailData);
      setMemoryRefs(memsData);
      setInvestigationReport(reportData);

      if (detailData.last_suggestion) {
        const memEvents = memsData.map(toMemoryEvent);
        const hasConsensus = hasSavedConsensusEvidence(
          detailData.last_suggestion,
          memEvents
        );
        const hydrated: InvestigationState = {
          status: "complete",
          phase: "complete",
          memories: memEvents,
          reasoning: "Analysis retrieved from historical telemetry store.",
          suggestion: detailData.last_suggestion,
          done: {
            type: "done",
            incident_id: detailData.id,
            memories_used: memsData.length,
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
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load incident intelligence."
      );
    } finally {
      setLoading(false);
    }
  }, [incidentId]);

  useEffect(() => {
    let active = true;
    getIncident(incidentId)
      .then(async (detailData) => {
        const [memsData, reportData] = await Promise.all([
          getIncidentMemories(incidentId).catch(() => []),
          detailData.last_suggestion ? getIncidentInvestigation(incidentId).catch(() => null) : null,
        ]);
        if (!active) return;
        setIncident(detailData);
        setMemoryRefs(memsData);
        setInvestigationReport(reportData);

        if (detailData.last_suggestion) {
          const memEvents = memsData.map(toMemoryEvent);
          const hasConsensus = hasSavedConsensusEvidence(
            detailData.last_suggestion,
            memEvents
          );
          const hydrated: InvestigationState = {
            status: "complete",
            phase: "complete",
            memories: memEvents,
            reasoning: "Analysis retrieved from historical telemetry store.",
            suggestion: detailData.last_suggestion,
            done: {
              type: "done",
              incident_id: detailData.id,
              memories_used: memsData.length,
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
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load incident intelligence."
        );
        setLoading(false);
      });

    return () => {
      active = false;
      if (streamAbortRef.current) {
        streamAbortRef.current.abort();
      }
    };
  }, [incidentId]);

  // Start real-time SSE stream analysis
  const handleStartAnalysis = async () => {
    if (analyzing || !incident) return;
    setAnalyzing(true);
    setError(null);

    if (streamAbortRef.current) {
      streamAbortRef.current.abort();
    }
    const controller = new AbortController();
    streamAbortRef.current = controller;

    const initial = beginInvestigation();
    investigationRunStateRef.current = initial;
    setInvestigation(initial);

    const onEvent = (event: AnalysisEvent) => {
      investigationRunStateRef.current = applyAnalysisEvent(
        investigationRunStateRef.current,
        event
      );
      setInvestigation({ ...investigationRunStateRef.current });
    };

    try {
      await streamIncidentAnalysis(incident.id, controller.signal, onEvent);

      // Verify consensus
      const finalState = investigationRunStateRef.current;
      const consensus = hasConsensusEvidence(finalState, incident);
      investigationRunStateRef.current = { ...finalState, consensus };
      setInvestigation(investigationRunStateRef.current);

      // Refresh incident details and memory references
      const freshDetail = await getIncident(incident.id);
      const [freshMems, freshReport] = await Promise.all([
        getIncidentMemories(incident.id).catch(() => []),
        freshDetail.last_suggestion ? getIncidentInvestigation(incident.id).catch(() => null) : null,
      ]);
      setIncident(freshDetail);
      setMemoryRefs(freshMems);
      setInvestigationReport(freshReport);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof Error
          ? err.message
          : "The investigation stream ended unexpectedly.";
      investigationRunStateRef.current = failInvestigation(
        investigationRunStateRef.current,
        message,
        investigation
      );
      setInvestigation({ ...investigationRunStateRef.current });
      setError(message);
    } finally {
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
        setAnalyzing(false);
      }
    }
  };

  const activeSuggestion =
    investigation.suggestion ?? incident?.last_suggestion ?? null;

  if (loading && !incident) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center gap-3 p-8 text-center text-[13px] text-ink-3">
        <LoaderCircle className="h-5 w-5 animate-spin" aria-hidden="true" />
        Loading incident and recalled memories…
      </div>
    );
  }

  if (error && !incident) {
    return (
      <div className="mx-auto max-w-xl py-16 text-center">
        <p className="display text-[32px]">Incident not found</p>
        <p className="mt-2 text-[13.5px] text-ink-3">{error}</p>
        <Link href="/incidents" className="btn btn-quiet mt-6">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to incidents
        </Link>
      </div>
    );
  }

  if (!incident) return null;

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="mb-3">
        <nav aria-label="Breadcrumb" className="mb-3.5 flex items-center gap-2 text-[13px] text-ink-3">
          <Link href="/incidents" className="hover:text-ink" aria-label="Back to incident list">
            Incidents
          </Link>
          <span aria-hidden="true">/</span>
          <span className="font-mono">{incident.id}</span>
        </nav>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="display max-w-[26ch] text-[clamp(30px,3.6vw,44px)] leading-[1.05] tracking-[-0.02em]">{incident.title}</h1>
            <div className="mt-3.5 flex flex-wrap items-center gap-2">
              <SeverityBadge severity={incident.severity} long />
              <StatusPill status={incident.status} />
              <span className="font-mono text-[12.5px] text-ink-3">
                {incident.service} · opened {formatTimestamp(incident.created_at)}
                {incident.resolved_at && ` · resolved ${formatTimestamp(incident.resolved_at)}`}
                {incident.time_to_resolve_minutes != null && ` · ${formatMinutes(incident.time_to_resolve_minutes)}`}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleStartAnalysis}
              disabled={analyzing}
              className={`btn btn-sm ${incident.status === "resolved" || activeSuggestion ? "btn-quiet" : "btn-primary"}`}
            >
              {analyzing ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />}
              {activeSuggestion ? "Re-analyze" : "Analyze incident"}
            </button>
            {incident.status !== "resolved" && (
              <button type="button" onClick={() => setShowResolveModal(true)} className={`btn btn-sm ${activeSuggestion ? "btn-primary" : "btn-quiet"}`}>
                <Check className="h-4 w-4" aria-hidden="true" />
                Record resolution
              </button>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div role="alert" className="callout-error">
          {error}
        </div>
      )}

      {/* Likely root cause, or what happens next */}
      {activeSuggestion ? (
        <section aria-label="Investigation results" className="panel p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="label !text-lily-ink">Likely root cause</span>
            <div className="flex flex-wrap items-center gap-2">
              <span className="pill font-mono">
                {activeSuggestion.confidence_calibrated === true
                  ? `Calibrated confidence ${Math.round(activeSuggestion.confidence * 100)}%`
                  : `Ranking score ${activeSuggestion.confidence.toFixed(2)}`}
              </span>
              {investigation.consensus && (
                <span className="pill st-resolved">
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                  Consensus
                </span>
              )}
            </div>
          </div>
          <p className="display mt-3 text-[clamp(22px,2.6vw,30px)] leading-[1.22]">{activeSuggestion.likely_root_cause}</p>
          {activeSuggestion.based_on.length > 0 && (
            <p className="mt-3 text-[13px] text-ink-3">
              Based on:{" "}
              {activeSuggestion.based_on.map((id, idx) => (
                <span key={id}>
                  {idx > 0 && ", "}
                  {/^INC-\d+$/.test(id) ? (
                    <Link href={`/incidents/${id}`} className="font-mono text-gold hover:underline">
                      {id}
                    </Link>
                  ) : (
                    id
                  )}
                </span>
              ))}
            </p>
          )}
          {!activeSuggestion.confidence_calibrated && (
            <p className="mt-2 text-[12.5px] text-ink-4">The score ranks explanations against each other. It is not yet a calibrated probability.</p>
          )}
        </section>
      ) : analyzing ? (
        <section className="panel flex items-center gap-3 p-5 text-[13.5px] text-ink-2">
          <LoaderCircle className="h-5 w-5 animate-spin text-gold" aria-hidden="true" />
          Reading the evidence and recalling past incidents…
          {investigation.memories.length > 0 && (
            <span className="pill pill-memory ml-auto">{investigation.memories.length} recalled so far</span>
          )}
        </section>
      ) : (
        <section aria-label="Investigation status" className="rounded-[10px] border border-dashed border-line-strong p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="display text-[24px]">Not analyzed yet</p>
              <p className="mt-1 max-w-[60ch] text-[13.5px] text-ink-3">
                Analyze to compare this incident&apos;s evidence against saved incidents and rank the likely causes.
              </p>
            </div>
            <button type="button" onClick={handleStartAnalysis} className="btn btn-primary btn-sm">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
              Analyze incident
            </button>
          </div>
        </section>
      )}

      {/* Recommended steps (the full report repeats these with more detail) */}
      {!investigationReport && activeSuggestion && (activeSuggestion.steps.length > 0 || activeSuggestion.warnings.length > 0) && (
        <section aria-label="Recommended remediation and warnings" className="panel p-5">
          <span className="label">Recommended steps</span>
          {activeSuggestion.steps.length > 0 && (
            <ol className="mt-3 grid gap-2 sm:grid-cols-2">
              {activeSuggestion.steps.map((step, idx) => (
                <li key={idx} className="grid grid-cols-[26px_minmax(0,1fr)] gap-1 rounded-md border border-line p-3 text-[13.5px] text-ink-2">
                  <span className="font-mono text-[12px] text-ink-4">{String(idx + 1).padStart(2, "0")}</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          )}
          {activeSuggestion.warnings.length > 0 && (
            <div className="callout-avoid mt-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 text-lily-ink" aria-hidden="true" />
              <div>
                <strong>Cautions, including what failed before.</strong>
                <ul className="mt-1 list-disc pl-5">
                  {activeSuggestion.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>
      )}

      {/* Evidence and memory, side by side */}
      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-label="Diagnostic telemetry and raw alert" className="panel">
          <div className="panel-h">
            <h2>Alert and logs</h2>
            <span className="label">Evidence</span>
          </div>
          <div className="space-y-3 p-[18px]">
            <div
              className="rounded-md bg-lily-wash p-3 font-mono text-[12.5px] leading-relaxed text-lily-ink"
              tabIndex={0}
              role="region"
              aria-label="Alert description"
            >
              {incident.alert_text}
            </div>
            <div className="code-block max-h-72 overflow-auto whitespace-pre" tabIndex={0} role="region" aria-label="Diagnostic logs">
              {incident.logs || "No logs were provided with this incident."}
            </div>
            {incident.timeline.length > 0 && (
              <ol className="m-0 list-none border-t border-line p-0 pt-3">
                {incident.timeline.map((event) => (
                  <li key={event.id} className="grid grid-cols-[64px_12px_minmax(0,1fr)] gap-3 py-1.5 text-[13px]">
                    <span className="font-mono text-[12px] text-ink-3">
                      {new Date(event.timestamp).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })}
                    </span>
                    <span
                      className={`mt-1.5 h-2 w-2 rounded-full ${
                        event.type === "alert" ? "bg-lily" : event.type === "analysis" ? "bg-gold-mark" : event.type === "resolution" ? "bg-ok" : "bg-ink-4"
                      }`}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 text-ink-2">{event.message}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </section>

        <section aria-label="Similar past incidents" className="panel" style={{ background: "linear-gradient(var(--gold-wash), var(--surface) 40%)" }}>
          <div className="panel-h">
            <h2 className="text-gold">Similar past incidents</h2>
            <span className="font-mono text-[12px] text-gold">{memoryRefs.length}</span>
          </div>
          <div className="max-h-[520px] overflow-y-auto" tabIndex={0} role="region" aria-label="Recalled memory list">
            {memoryRefs.length === 0 ? (
              <p className="px-6 py-10 text-center text-[13px] text-ink-3">
                Nothing recalled yet. Analyze the incident to search saved incident history.
              </p>
            ) : (
              <ul className="m-0 list-none p-0">
                {memoryRefs.map((mem) => (
                  <li key={mem.past_incident_id} className="border-b border-line px-[18px] py-3.5 last:border-b-0">
                    <div className="flex items-baseline justify-between gap-2">
                      <Link href={`/incidents/${mem.past_incident_id}`} className="font-mono text-[12.5px] font-medium text-gold hover:underline">
                        {mem.past_incident_id}
                      </Link>
                      <span className="font-mono text-[11.5px] text-ink-3">score {mem.relevance_score.toFixed(2)}</span>
                    </div>
                    <p className="mt-1 text-[13.5px] font-medium">{mem.past_incident?.title ?? mem.summary}</p>
                    {mem.past_incident?.root_cause && (
                      <p className="mt-1.5 text-[13px] text-ink-2">
                        <b className="font-medium text-ink">Root cause:</b> {mem.past_incident.root_cause}
                      </p>
                    )}
                    {mem.past_incident?.resolution && (
                      <p className="mt-1 text-[13px] text-ink-2">
                        <b className="font-medium text-ok">Fix that worked:</b> {mem.past_incident.resolution}
                      </p>
                    )}
                    {mem.past_incident && mem.past_incident.failed_attempts.length > 0 && (
                      <p className="mt-1 text-[13px] text-lily-ink">Tried and failed: {mem.past_incident.failed_attempts.join(" · ")}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      {/* Full investigation report from the core engine, or the structural graph */}
      {investigationReport ? (
        <InvestigationReportPanel report={investigationReport} incident={incident} memoryRefs={memoryRefs} activeSuggestion={activeSuggestion} />
      ) : (
        <InvestigationGraph incident={incident} memoryRefs={memoryRefs} activeSuggestion={activeSuggestion} report={null} defaultOpenMobile={false} />
      )}

      {incident.status === "resolved" && (
        <section aria-label="Confirmed resolution" className="panel p-5 sm:p-6" style={{ borderColor: "color-mix(in srgb, var(--ok) 45%, var(--line))" }}>
          <span className="label !text-ok">Confirmed resolution</span>
          <div className="mt-4 grid gap-5 sm:grid-cols-2">
            <div>
              <h3 className="label text-[10.5px]">Root cause</h3>
              <p className="mt-1.5 text-[14px]">{incident.root_cause ?? "Not recorded"}</p>
            </div>
            <div>
              <h3 className="label text-[10.5px]">Fix that worked</h3>
              <p className="mt-1.5 text-[14px]">{incident.resolution ?? "Not recorded"}</p>
            </div>
          </div>
          {incident.failed_attempts && incident.failed_attempts.length > 0 && (
            <div className="mt-4">
              <h3 className="label text-[10.5px]">What failed</h3>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[14px] text-lily-ink">
                {incident.failed_attempts.map((f, idx) => (
                  <li key={idx}>{f}</li>
                ))}
              </ul>
            </div>
          )}
          {incident.notes && <p className="display mt-4 border-t border-line pt-3 text-[19px] italic text-gold">&ldquo;{incident.notes}&rdquo;</p>}
        </section>
      )}

      <ResolveIncidentModal
        isOpen={showResolveModal}
        incidentId={incident.id}
        incidentTitle={incident.title}
        suggestion={activeSuggestion}
        onClose={() => setShowResolveModal(false)}
        onResolved={(res: ResolveResponse) => {
          setIncident(res);
          loadIncidentData();
        }}
      />
    </div>
  );
}
