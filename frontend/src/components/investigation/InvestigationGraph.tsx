"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import {
  ExternalLink,
  Network,
  RotateCcw,
  ZoomIn,
  ZoomOut,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import type {
  IncidentDetail,
  InvestigationReport,
  MemoryReference,
  Suggestion,
} from "@/lib/memory-types";

export type GraphNodeKind =
  | "incident"
  | "service"
  | "evidence"
  | "historical"
  | "hypothesis"
  | "remediation"
  | "change"
  | "anomaly"
  | "signature";

export type GraphEdgeBasis = "observed" | "known" | "inferred" | "historical";

export interface UnifiedGraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  role?: string | null;
  service?: string | null;
  severity?: string | null;
  status?: string | null;
  similarity?: number | null;
  confidence?: number | null;
  isCalibrated?: boolean;
  rootCause?: string | null;
  resolution?: string | null;
  failedAttempts?: string[];
  details?: string | null;
  evidenceIds?: string[];
}

export interface UnifiedGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  basis: GraphEdgeBasis;
  rationale: string;
}

export interface InvestigationGraphProps {
  incident: IncidentDetail;
  memoryRefs?: MemoryReference[];
  activeSuggestion?: Suggestion | null;
  report?: InvestigationReport | null;
  onSelectMemory?: (pastIncidentId: string) => void;
  className?: string;
  defaultOpenMobile?: boolean;
}

// Colours are theme tokens: observed evidence in ink, shared infrastructure in grey,
// inferred links in red, and memory recall in gold.
const EDGE_COLORS: Record<GraphEdgeBasis, string> = {
  observed: "var(--ink-2)",
  known: "var(--ink-4)",
  inferred: "var(--lily)",
  historical: "var(--gold-mark)",
};

const EDGE_LABELS: Record<GraphEdgeBasis, string> = {
  observed: "Observed",
  known: "Shared infrastructure",
  inferred: "Inferred",
  historical: "Memory recall",
};

const NODE_KIND_STYLES: Record<
  GraphNodeKind,
  { fill: string; stroke: string; glyph: string; badge: string }
> = {
  incident: { fill: "var(--lily-wash)", stroke: "var(--lily)", glyph: "var(--lily)", badge: "Active incident" },
  service: { fill: "var(--surface)", stroke: "var(--ink)", glyph: "var(--ink)", badge: "Service" },
  evidence: { fill: "var(--surface)", stroke: "var(--ink-3)", glyph: "var(--ink-3)", badge: "Observed evidence" },
  historical: { fill: "var(--gold-wash)", stroke: "var(--gold-mark)", glyph: "var(--gold-mark)", badge: "Past memory" },
  hypothesis: { fill: "var(--surface)", stroke: "var(--lily)", glyph: "var(--lily)", badge: "Hypothesis" },
  remediation: { fill: "var(--ok-wash)", stroke: "var(--ok)", glyph: "var(--ok)", badge: "Remediation" },
  change: { fill: "var(--surface)", stroke: "var(--ink-2)", glyph: "var(--ink-2)", badge: "Suspect change" },
  anomaly: { fill: "var(--lily-wash)", stroke: "var(--lily-ink)", glyph: "var(--lily-ink)", badge: "Anomaly" },
  signature: { fill: "var(--surface)", stroke: "var(--ink-4)", glyph: "var(--ink-4)", badge: "Signature" },
};

export default function InvestigationGraph({
  incident,
  memoryRefs = [],
  activeSuggestion = null,
  report = null,
  className = "",
  defaultOpenMobile = false,
}: InvestigationGraphProps) {
  const [selectedId, setSelectedId] = useState<string | null>(incident.id);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [graphOpen, setGraphOpen] = useState(() => {
    if (typeof window !== "undefined") {
      const isMobile = window.matchMedia("(max-width: 767px)").matches;
      return isMobile ? defaultOpenMobile : true;
    }
    return true;
  });
  const [visibleKinds, setVisibleKinds] = useState<GraphNodeKind[]>([
    "incident",
    "service",
    "evidence",
    "historical",
    "hypothesis",
    "remediation",
    "change",
    "anomaly",
    "signature",
  ]);

  // Build Unified Nodes and Edges
  const { nodes, edges } = useMemo(() => {
    // If report is available and contains nodes, adapt it while ensuring incident/service are integrated
    if (report && report.graph && report.graph.nodes && report.graph.nodes.length > 0) {
      const reportNodes: UnifiedGraphNode[] = report.graph.nodes.map((n) => {
        let kind: GraphNodeKind = "hypothesis";
        if (n.kind === "service") kind = "service";
        else if (n.kind === "change") kind = "change";
        else if (n.kind === "anomaly") kind = "anomaly";
        else if (n.kind === "signature") kind = "signature";
        else if (n.kind === "evidence") kind = "evidence";

        return {
          id: n.id,
          kind,
          label: n.label,
          role: n.role,
          service: n.service,
          evidenceIds: n.evidence_ids,
        };
      });

      // Ensure active incident node is included
      const hasActive = reportNodes.some((n) => n.id === incident.id);
      if (!hasActive) {
        reportNodes.unshift({
          id: incident.id,
          kind: "incident",
          label: `${incident.id}: ${incident.title}`,
          role: "active_focus",
          service: incident.service,
          severity: incident.severity,
          status: incident.status,
        });
      }

      // Ensure historical memories from report.recalled_incidents or memoryRefs
      const memories = report.recalled_incidents?.length
        ? report.recalled_incidents.map((r) => ({
            past_incident_id: r.record.incident_id,
            summary: r.record.title,
            relevance_score: r.relevance,
            used_at: new Date().toISOString(),
            past_incident: {
              id: r.record.incident_id,
              title: r.record.title,
              service: r.record.service,
              severity: "SEV2" as const,
              created_at: "",
              root_cause: r.record.root_cause,
              resolution: r.record.resolution,
              failed_attempts: r.record.attempts?.map((a) => a.description) ?? [],
              time_to_resolve_minutes: null,
            },
          }))
        : memoryRefs;

      memories.forEach((mem) => {
        const memId = `past-${mem.past_incident_id}`;
        if (!reportNodes.some((n) => n.id === memId)) {
          reportNodes.push({
            id: memId,
            kind: "historical",
            label: `${mem.past_incident_id}: ${(mem.past_incident?.title ?? mem.summary).slice(0, 36)}`,
            role: "recalled_memory",
            service: mem.past_incident?.service,
            similarity: mem.relevance_score,
            rootCause: mem.past_incident?.root_cause,
            resolution: mem.past_incident?.resolution,
            failedAttempts: mem.past_incident?.failed_attempts,
          });
        }
      });

      const reportEdges: UnifiedGraphEdge[] = report.graph.edges.map((e, idx) => ({
        id: `report-edge-${idx}`,
        source: e.source,
        target: e.target,
        relation: e.relation,
        basis: e.basis,
        rationale: e.rationale,
      }));

      // Connect historical nodes to incident if not already connected
      memories.forEach((mem) => {
        const memId = `past-${mem.past_incident_id}`;
        if (!reportEdges.some((e) => e.source === memId || e.target === memId)) {
          reportEdges.push({
            id: `edge-hist-${mem.past_incident_id}`,
            source: memId,
            target: incident.id,
            relation: "recalled_precedent",
            basis: "historical",
            rationale: `Vector memory match (${(mem.relevance_score * 100).toFixed(0)}% similarity) retrieved from institutional post-mortem store.`,
          });
        }
      });

      return { nodes: reportNodes, edges: reportEdges };
    }

    // Default: Construct genuine semantic graph from real active metadata
    const nodeList: UnifiedGraphNode[] = [];
    const edgeList: UnifiedGraphEdge[] = [];

    // 1. Active Incident Node (Center)
    nodeList.push({
      id: incident.id,
      kind: "incident",
      label: `${incident.id}: ${incident.title}`,
      role: "active_focus",
      service: incident.service,
      severity: incident.severity,
      status: incident.status,
      details: incident.alert_text,
    });

    // 2. Affected Primary Service Node (Observed)
    if (incident.service) {
      const serviceNodeId = `service-${incident.service}`;
      nodeList.push({
        id: serviceNodeId,
        kind: "service",
        label: `Service: ${incident.service}`,
        role: "affected_domain",
        service: incident.service,
      });

      edgeList.push({
        id: "edge-service-incident",
        source: serviceNodeId,
        target: incident.id,
        relation: "impacts_service",
        basis: "observed",
        rationale: `Primary telemetry and alert indicate degradation in service '${incident.service}'.`,
      });
    }

    // 3. Observed Alert Telemetry Node
    if (incident.alert_text) {
      const alertNodeId = `evidence-alert`;
      nodeList.push({
        id: alertNodeId,
        kind: "evidence",
        label: "Observed Alert Telemetry",
        role: "alert_payload",
        service: incident.service,
        details: incident.alert_text,
      });

      edgeList.push({
        id: "edge-alert-incident",
        source: alertNodeId,
        target: incident.id,
        relation: "triggered_by",
        basis: "observed",
        rationale: "Initial alert payload and diagnostic log excerpt received at incident registration.",
      });
    }

    // 4. Candidate Hypothesis Node (if analysis or suggestion exists)
    const suggestion = activeSuggestion ?? incident.last_suggestion;
    if (suggestion) {
      const hypNodeId = "hypothesis-root-cause";
      nodeList.push({
        id: hypNodeId,
        kind: "hypothesis",
        label: `Candidate Cause: ${suggestion.likely_root_cause.slice(0, 40)}${suggestion.likely_root_cause.length > 40 ? "..." : ""}`,
        role: "candidate_root_cause",
        service: incident.service,
        confidence: suggestion.confidence,
        isCalibrated: suggestion.confidence_calibrated,
        details: suggestion.likely_root_cause,
      });

      edgeList.push({
        id: "edge-incident-hypothesis",
        source: incident.id,
        target: hypNodeId,
        relation: "explains_symptoms",
        basis: "inferred",
        rationale: `Inferred explanation derived from symptom pattern matching${suggestion.confidence_calibrated ? ` (Calibrated confidence: ${(suggestion.confidence * 100).toFixed(0)}%)` : ""}.`,
      });

      // 5. Remediation Plan Node
      if (suggestion.steps && suggestion.steps.length > 0) {
        const remNodeId = "remediation-plan";
        nodeList.push({
          id: remNodeId,
          kind: "remediation",
          label: `Remediation Plan (${suggestion.steps.length} steps)`,
          role: "recovery_workflow",
          details: suggestion.steps.join("\n"),
        });

        edgeList.push({
          id: "edge-hypothesis-remediation",
          source: hypNodeId,
          target: remNodeId,
          relation: "prescribes_action",
          basis: "inferred",
          rationale: "Recommended recovery steps designed to neutralize candidate root cause.",
        });
      }
    }

    // 6. Recalled Historical Incidents from Vector Memory
    memoryRefs.forEach((mem) => {
      const memId = `past-${mem.past_incident_id}`;
      const memTitle = mem.past_incident?.title ?? mem.summary;
      nodeList.push({
        id: memId,
        kind: "historical",
        label: `${mem.past_incident_id}: ${memTitle.slice(0, 36)}${memTitle.length > 36 ? "..." : ""}`,
        role: "recalled_precedent",
        service: mem.past_incident?.service,
        similarity: mem.relevance_score,
        rootCause: mem.past_incident?.root_cause,
        resolution: mem.past_incident?.resolution,
        failedAttempts: mem.past_incident?.failed_attempts,
      });

      edgeList.push({
        id: `edge-hist-${mem.past_incident_id}`,
        source: memId,
        target: incident.id,
        relation: "similar_precedent",
        basis: "historical",
        rationale: `Institutional memory recall with ${(mem.relevance_score * 100).toFixed(0)}% semantic vector similarity to active incident.`,
      });

      // If past incident shares the exact same service, add known infrastructure relation
      if (
        incident.service &&
        mem.past_incident?.service &&
        mem.past_incident.service.toLowerCase() === incident.service.toLowerCase()
      ) {
        edgeList.push({
          id: `edge-service-hist-${mem.past_incident_id}`,
          source: `service-${incident.service}`,
          target: memId,
          relation: "shared_service",
          basis: "known",
          rationale: `Both active incident and ${mem.past_incident_id} originate within the '${incident.service}' service architecture.`,
        });
      }
    });

    return { nodes: nodeList, edges: edgeList };
  }, [incident, memoryRefs, activeSuggestion, report]);

  // Compute Layout Coordinates (4-Tier Semantic Columns)
  // Col 1: Observed Telemetry Evidence & Primary Services (X=140)
  // Col 2: Active Incident Focus (X=400)
  // Col 3: Inferred Hypotheses & Remediation (X=670)
  // Col 4: Recalled Historical Precedents (X=900)
  const positions = useMemo(() => {
    const col1 = nodes.filter((n) => ["service", "evidence", "signature"].includes(n.kind));
    const col2 = nodes.filter((n) => n.kind === "incident");
    const col3 = nodes.filter((n) => ["hypothesis", "remediation", "anomaly", "change"].includes(n.kind));
    const col4 = nodes.filter((n) => n.kind === "historical");

    const map = new Map<string, { x: number; y: number }>();

    const placeColumn = (columnNodes: UnifiedGraphNode[], x: number) => {
      const count = columnNodes.length;
      if (count === 0) return;
      if (count === 1) {
        map.set(columnNodes[0].id, { x, y: 280 });
        return;
      }
      const availableHeight = 440;
      const step = Math.min(110, availableHeight / (count - 1));
      const startY = 280 - ((count - 1) * step) / 2;
      columnNodes.forEach((node, idx) => {
        map.set(node.id, { x, y: startY + idx * step });
      });
    };

    placeColumn(col1, 140);
    placeColumn(col2, 400);
    placeColumn(col3, 670);
    placeColumn(col4, 900);

    return map;
  }, [nodes]);

  // Connected Relationships Calculation for Highlighting
  const activeFocusId = hoveredId ?? selectedId;
  const connectedNodeIds = useMemo(() => {
    if (!activeFocusId) return new Set(nodes.map((n) => n.id));
    const ids = new Set<string>([activeFocusId]);
    edges.forEach((e) => {
      if (e.source === activeFocusId) ids.add(e.target);
      if (e.target === activeFocusId) ids.add(e.source);
    });
    return ids;
  }, [activeFocusId, edges, nodes]);

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null,
    [nodes, selectedId]
  );

  const selectedNodeEdges = useMemo(() => {
    if (!selectedId) return [];
    return edges.filter((e) => e.source === selectedId || e.target === selectedId);
  }, [selectedId, edges]);

  // Distinct Filter Kinds Present in Current Data
  const availableKinds = useMemo(() => {
    const set = new Set<GraphNodeKind>();
    nodes.forEach((n) => set.add(n.kind));
    return Array.from(set);
  }, [nodes]);

  return (
    <section aria-labelledby="investigation-graph-heading" className={`panel p-5 ${className}`}>
      {/* Header & controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
        <div className="min-w-0">
          <span className="label">Causal graph</span>
          <h2 id="investigation-graph-heading" className="display mt-1 text-[26px] leading-tight">
            Investigation topology
          </h2>
          <p className="mt-1 text-[13px] text-ink-3">
            {report
              ? "Failure chain from the full investigation: evidence, causes, the plan, and recalled incidents."
              : "Built from this incident's metadata: the alert, the affected service, and recalled memories."}
          </p>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setGraphOpen((open) => !open)}
            className="btn btn-quiet btn-sm md:hidden"
            aria-expanded={graphOpen}
            aria-controls="investigation-graph-body"
          >
            {graphOpen ? "Hide graph" : `Show graph (${nodes.length} nodes)`}
            {graphOpen ? <ChevronUp className="h-4 w-4" aria-hidden="true" /> : <ChevronDown className="h-4 w-4" aria-hidden="true" />}
          </button>
          <div className="hidden items-center gap-1.5 sm:flex">
            <button type="button" onClick={() => setScale((v) => Math.max(0.65, v - 0.15))} className="btn btn-quiet btn-sm btn-icon" aria-label="Zoom out graph">
              <ZoomOut className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => {
                setScale(1);
                setSelectedId(incident.id);
              }}
              className="btn btn-quiet btn-sm"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Reset
            </button>
            <button type="button" onClick={() => setScale((v) => Math.min(1.5, v + 0.15))} className="btn btn-quiet btn-sm btn-icon" aria-label="Zoom in graph">
              <ZoomIn className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <div id="investigation-graph-body" className={graphOpen ? "mt-4 space-y-4" : "mt-4 hidden space-y-4 md:block"}>
        {!report && (
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-raise px-3.5 py-2.5 text-[13px] text-ink-2">
            <span className="h-1.5 w-1.5 rounded-full bg-gold-mark" aria-hidden="true" />
            <span>
              <strong className="font-medium text-ink">Structural graph.</strong> Shows the alert, the service, and {memoryRefs.length} recalled{" "}
              {memoryRefs.length === 1 ? "memory" : "memories"}. Run a full investigation for the causal chain.
            </span>
          </div>
        )}

        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="sr-only">Toggle visible graph entity kinds</legend>
          <span className="label mr-1">Show</span>
          {availableKinds.map((kind) => {
            const isVisible = visibleKinds.includes(kind);
            const style = NODE_KIND_STYLES[kind] ?? NODE_KIND_STYLES.hypothesis;
            return (
              <button
                key={kind}
                type="button"
                onClick={() => setVisibleKinds((prev) => (prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind]))}
                className={`inline-flex min-h-[34px] items-center gap-2 rounded-full border px-3 text-[12.5px] transition-colors ${
                  isVisible ? "border-line-strong text-ink" : "border-line text-ink-4 line-through"
                }`}
                aria-pressed={isVisible}
              >
                <span className="h-2 w-2 rounded-full" style={{ background: isVisible ? style.stroke : "var(--line-strong)" }} aria-hidden="true" />
                {style.badge}
              </button>
            );
          })}
        </fieldset>

        <div className="grid gap-4">
          {/* SVG viewport */}
          <div
            className="relative overflow-auto rounded-lg border border-line bg-sunk p-1"
            tabIndex={0}
            role="region"
            aria-label={`Interactive investigation graph containing ${nodes.length} nodes and ${edges.length} relationships`}
          >
            <svg viewBox="0 0 1040 560" className="mx-auto block h-auto select-none" style={{ width: `${Math.round(scale * 100)}%`, minWidth: Math.round(720 * scale) }}>
              <defs>
                {(["observed", "known", "inferred", "historical"] as GraphEdgeBasis[]).map((basis) => (
                  <marker key={basis} id={`arrow-${basis}`} markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">
                    <path d="M0,1 L9,5 L0,9 z" style={{ fill: EDGE_COLORS[basis] }} />
                  </marker>
                ))}
                <pattern id="graph-grid" width="40" height="40" patternUnits="userSpaceOnUse">
                  <path d="M 40 0 L 0 0 0 40" style={{ fill: "none", stroke: "var(--line)" }} strokeWidth="1" opacity="0.6" />
                </pattern>
              </defs>

              <rect width="1040" height="560" style={{ fill: "url(#graph-grid)" }} />

              {/* Column headings and dividers */}
              <g className="pointer-events-none font-mono text-[11px] uppercase tracking-[0.12em]" style={{ fill: "var(--ink-4)" }}>
                <text x="140" y="32" textAnchor="middle">1 · Evidence &amp; service</text>
                <text x="400" y="32" textAnchor="middle">2 · Active incident</text>
                <text x="670" y="32" textAnchor="middle">3 · Cause &amp; plan</text>
                <text x="900" y="32" textAnchor="middle" style={{ fill: "var(--gold)" }}>4 · Memory</text>
                {[270, 535, 785].map((x) => (
                  <line key={x} x1={x} y1="20" x2={x} y2="540" style={{ stroke: "var(--line-strong)" }} strokeDasharray="3 6" opacity="0.6" />
                ))}
              </g>

              {/* Edges */}
              {edges.map((edge) => {
                const from = positions.get(edge.source);
                const to = positions.get(edge.target);
                if (!from || !to) return null;
                const sourceNode = nodes.find((n) => n.id === edge.source);
                const targetNode = nodes.find((n) => n.id === edge.target);
                if (!sourceNode || !targetNode || !visibleKinds.includes(sourceNode.kind) || !visibleKinds.includes(targetNode.kind)) {
                  return null;
                }
                const isConnected = !activeFocusId || edge.source === activeFocusId || edge.target === activeFocusId;
                const edgeColor = EDGE_COLORS[edge.basis] ?? "var(--ink-3)";
                const isDashed = edge.basis === "inferred";
                const isDotted = edge.basis === "historical";
                const dx = to.x - from.x;
                return (
                  <g key={edge.id} opacity={isConnected ? 1 : 0.14} className="pointer-events-none transition-opacity duration-200">
                    <path
                      d={`M ${from.x} ${from.y} C ${from.x + dx * 0.45} ${from.y}, ${from.x + dx * 0.55} ${to.y}, ${to.x} ${to.y}`}
                      style={{ fill: "none", stroke: edgeColor }}
                      strokeWidth={isConnected && activeFocusId ? 2.2 : 1.4}
                      strokeDasharray={isDashed ? "6 4" : isDotted ? "1.5 4.5" : undefined}
                      strokeLinecap="round"
                      markerEnd={`url(#arrow-${edge.basis})`}
                    />
                  </g>
                );
              })}

              {/* Nodes */}
              {nodes.map((node) => {
                const point = positions.get(node.id);
                if (!point || !visibleKinds.includes(node.kind)) return null;
                const isSelected = selectedId === node.id;
                const isHovered = hoveredId === node.id;
                const isConnected = connectedNodeIds.has(node.id);
                const style = NODE_KIND_STYLES[node.kind] ?? NODE_KIND_STYLES.hypothesis;
                const radius = node.kind === "incident" ? 18 : 13;
                const labelLeft = node.kind === "historical";
                const labelX = point.x + (labelLeft ? -radius - 8 : radius + 8);
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`${style.badge}: ${node.label}`}
                    aria-pressed={isSelected}
                    onClick={() => setSelectedId(isSelected ? null : node.id)}
                    onMouseEnter={() => setHoveredId(node.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSelectedId(isSelected ? null : node.id);
                      }
                    }}
                    opacity={isConnected ? 1 : 0.18}
                    className="cursor-pointer transition-opacity duration-200 focus:outline-none"
                  >
                    {(isSelected || isHovered) && (
                      <circle cx={point.x} cy={point.y} r={radius + 8} style={{ fill: "none", stroke: style.stroke }} strokeWidth="1.4" strokeDasharray="4 3" />
                    )}
                    <circle
                      cx={point.x}
                      cy={point.y}
                      r={radius}
                      style={{ fill: style.fill, stroke: isSelected ? "var(--ink)" : style.stroke }}
                      strokeWidth={isSelected ? 2.6 : 1.8}
                    />
                    {node.kind === "incident" && <circle cx={point.x} cy={point.y} r="5.5" style={{ fill: style.glyph }} />}
                    {(node.kind === "service" || node.kind === "signature" || node.kind === "change") && (
                      <rect x={point.x - 4} y={point.y - 4} width="8" height="8" rx="1" style={{ fill: style.glyph }} />
                    )}
                    {node.kind === "historical" && (
                      <polygon points={`${point.x},${point.y - 5} ${point.x + 5},${point.y + 4} ${point.x - 5},${point.y + 4}`} style={{ fill: style.glyph }} />
                    )}
                    {(node.kind === "hypothesis" || node.kind === "remediation") && (
                      <circle cx={point.x} cy={point.y} r="4" style={{ fill: style.glyph }} />
                    )}
                    {(node.kind === "evidence" || node.kind === "anomaly") && (
                      <rect
                        x={point.x - 4}
                        y={point.y - 4}
                        width="8"
                        height="8"
                        style={{ fill: style.glyph }}
                        transform={`rotate(45 ${point.x} ${point.y})`}
                      />
                    )}
                    <text
                      x={labelX}
                      y={point.y + 4}
                      textAnchor={labelLeft ? "end" : "start"}
                      fontSize="12.5"
                      fontWeight={isSelected ? 600 : 500}
                      className="pointer-events-none"
                      style={{ fill: isSelected ? "var(--ink)" : "var(--ink-2)", paintOrder: "stroke", stroke: "var(--sunk)", strokeWidth: 4, strokeLinejoin: "round" }}
                    >
                      {node.label.length > 28 ? `${node.label.slice(0, 26)}…` : node.label}
                    </text>
                    {node.similarity != null && (
                      <text
                        x={labelX}
                        y={point.y + 18}
                        textAnchor={labelLeft ? "end" : "start"}
                        fontSize="10.5"
                        className="pointer-events-none font-mono"
                        style={{ fill: "var(--gold)" }}
                      >
                        {(node.similarity * 100).toFixed(0)}% match
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>
          </div>

          {/* Node inspector */}
          <aside aria-live="polite" className="grid min-w-0 gap-4 rounded-lg border border-line p-4 lg:grid-cols-[minmax(0,1fr)_260px] lg:gap-6">
            <div className="min-w-0">
              <div className="flex items-center justify-between gap-2 border-b border-line pb-3">
                <span className="label">Node inspector</span>
                {selectedNode && (
                  <span
                    className="pill"
                    style={{
                      color: NODE_KIND_STYLES[selectedNode.kind]?.stroke,
                      background: NODE_KIND_STYLES[selectedNode.kind]?.fill,
                      borderColor: "transparent",
                    }}
                  >
                    {NODE_KIND_STYLES[selectedNode.kind]?.badge ?? selectedNode.kind}
                  </span>
                )}
              </div>

              {selectedNode ? (
                <div className="mt-4 space-y-3 text-[13px] leading-relaxed text-ink-2">
                  <h3 className="display text-[20px] leading-snug text-ink">{selectedNode.label}</h3>
                  {selectedNode.service && (
                    <p>
                      <span className="text-ink-3">Service</span> <span className="svc">{selectedNode.service}</span>
                    </p>
                  )}
                  {selectedNode.severity && (
                    <p className="flex items-center gap-2">
                      <span className="text-ink-3">Severity</span> <span className={`sev sev-${selectedNode.severity}`}>{selectedNode.severity}</span>
                    </p>
                  )}
                  {selectedNode.similarity != null && (
                    <div className="rounded-md bg-gold-wash p-2.5">
                      <span className="font-medium text-gold">Memory match {(selectedNode.similarity * 100).toFixed(1)}%</span>
                      <p className="mt-1 text-[12px] text-ink-3">How closely this past incident matches the current one.</p>
                    </div>
                  )}
                  {selectedNode.confidence != null && (
                    <p>
                      <span className="text-ink-3">{selectedNode.isCalibrated ? "Calibrated confidence" : "Ranking score"}</span>{" "}
                      <strong className="font-mono font-medium text-ink">
                        {selectedNode.isCalibrated ? `${Math.round(selectedNode.confidence * 100)}%` : selectedNode.confidence.toFixed(2)}
                      </strong>
                    </p>
                  )}
                  {selectedNode.rootCause && (
                    <div className="rounded-md border border-line p-2.5">
                      <span className="font-medium text-ink">Root cause</span>
                      <p className="mt-0.5">{selectedNode.rootCause}</p>
                    </div>
                  )}
                  {selectedNode.resolution && (
                    <div className="rounded-md bg-ok-wash p-2.5">
                      <span className="font-medium text-ok">Fix that worked</span>
                      <p className="mt-0.5">{selectedNode.resolution}</p>
                    </div>
                  )}
                  {selectedNode.failedAttempts && selectedNode.failedAttempts.length > 0 && (
                    <div className="rounded-md bg-lily-wash p-2.5">
                      <span className="font-medium text-lily-ink">Tried and failed</span>
                      <ul className="mt-0.5 list-disc pl-4">
                        {selectedNode.failedAttempts.map((a) => (
                          <li key={a}>{a}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {selectedNode.details && !selectedNode.rootCause && (
                    <div className="code-block max-h-32 overflow-y-auto text-[11.5px]">{selectedNode.details}</div>
                  )}

                  <div className="border-t border-line pt-3">
                    <span className="text-ink-3">Connections ({selectedNodeEdges.length})</span>
                    <ul className="mt-2 max-h-44 space-y-2 overflow-y-auto pr-1">
                      {selectedNodeEdges.map((e) => {
                        const otherId = e.source === selectedNode.id ? e.target : e.source;
                        const otherNode = nodes.find((n) => n.id === otherId);
                        const isOutbound = e.source === selectedNode.id;
                        return (
                          <li key={e.id} className="rounded-md border border-line p-2 text-[12px]">
                            <div className="flex flex-wrap items-center justify-between gap-1">
                              <span className="font-mono text-[10.5px] uppercase tracking-[0.08em]" style={{ color: EDGE_COLORS[e.basis] }}>
                                {EDGE_LABELS[e.basis] ?? e.basis}
                              </span>
                              <span className="truncate text-ink-3">
                                {isOutbound ? "to" : "from"} {otherNode?.label.slice(0, 22)}
                              </span>
                            </div>
                            <p className="mt-1 text-ink-2">{e.rationale}</p>
                          </li>
                        );
                      })}
                    </ul>
                  </div>

                  {selectedNode.kind === "historical" && (
                    <Link href={`/incidents/${selectedNode.id.replace("past-", "")}`} className="btn btn-quiet btn-sm w-full">
                      Open this past incident
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                    </Link>
                  )}
                </div>
              ) : (
                <div className="py-10 text-center text-[13px] text-ink-3">
                  <Network className="mx-auto h-7 w-7 text-ink-4" aria-hidden="true" />
                  <p className="mt-2 font-medium text-ink-2">No node selected</p>
                  <p className="mt-1">Select a node to see its evidence, memory match, and connections.</p>
                </div>
              )}
            </div>

            <div className="border-t border-line pt-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
              <span className="label">Line styles</span>
              <div className="mt-2.5 grid grid-cols-2 gap-2 text-[12px] text-ink-2">
                {(["observed", "known", "inferred", "historical"] as GraphEdgeBasis[]).map((basis) => (
                  <span key={basis} className="flex items-center gap-2">
                    <svg width="26" height="8" aria-hidden="true">
                      <line
                        x1="1"
                        y1="4"
                        x2="25"
                        y2="4"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeDasharray={basis === "inferred" ? "6 4" : basis === "historical" ? "1.5 4.5" : undefined}
                        style={{ stroke: EDGE_COLORS[basis] }}
                      />
                    </svg>
                    {EDGE_LABELS[basis]}
                  </span>
                ))}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}
