"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import type { SceneCamera, SceneMemory, ScenePoint, SceneNode } from "@/lib/memory-types";
import { createServiceEdges, drawScene } from "./draw-scene";
import { createSceneLayout, createWakeRanks } from "./scene-layout";

type Props = {
  memories: SceneMemory[];
  activeIncidentId: string | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onHover?: (id: string | null) => void;
  active: boolean;
  consensus: boolean;
  animationKey: number;
  investigationKey: number;
  paused: boolean;
  reducedMotion: boolean;
};

type PointerDrag = { pointerId: number; x: number; y: number; moved: boolean };
type SceneInputs = Pick<Props, "memories" | "selectedId" | "active" | "consensus" | "paused" | "reducedMotion"> & {
  layout: SceneNode[];
  serviceEdges: ReturnType<typeof createServiceEdges>;
  wakeRanks: Map<string, number>;
  hoveredId: string | null;
  onSelect: Props["onSelect"];
};

const RECALL_TRAVEL_MS = 900;
const INTRO_DURATION_MS = 6200;

function placeInspectionCard(anchorX: number, anchorY: number, canvasRect: DOMRect): { left: number; top: number; width: number; maxHeight: number } {
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const compact = canvasRect.width < 768;
  const preferredWidth = Math.min(compact ? 280 : 288, viewportWidth - 24);
  const preferredHeight = Math.min(compact ? 300 : 360, viewportHeight * (compact ? 0.42 : 0.4));
  const scale = Math.min(1, canvasRect.width / 1400, canvasRect.height / 820);
  const coreX = canvasRect.left + canvasRect.width / 2;
  const coreY = canvasRect.top + canvasRect.height * (compact ? 0.62 : 0.44) + (compact ? 0 : -10);
  const coreRadius = 112 * scale + 28;
  const headerBottom = Array.from(document.querySelectorAll<HTMLElement>("header, nav[aria-label='Incident panels']"))
    .filter((element) => element.getClientRects().length > 0)
    .reduce((bottom, element) => Math.max(bottom, element.getBoundingClientRect().bottom), 64);
  const dock = document.querySelector<HTMLElement>(".cinematic-dock")?.getBoundingClientRect();
  const safeTop = Math.max(76, headerBottom + (compact ? 8 : 12));
  const safeBottom = Math.min(viewportHeight - 12, (dock?.top ?? viewportHeight) - 12);
  const blockers = Array.from(document.querySelectorAll<HTMLElement>("header, #incident-context, #recall-spine, .cinematic-dock"))
    .filter((element) => element.getClientRects().length > 0)
    .map((element) => element.getBoundingClientRect());
  const collides = (candidate: { left: number; top: number }, candidateWidth: number, candidateHeight: number) => {
    const closestX = Math.max(candidate.left, Math.min(coreX, candidate.left + candidateWidth));
    const closestY = Math.max(candidate.top, Math.min(coreY, candidate.top + candidateHeight));
    const coreCollision = Math.hypot(closestX - coreX, closestY - coreY) < coreRadius;
    const outsideViewport = candidate.left < 12 || candidate.left + candidateWidth > viewportWidth - 12 || candidate.top < safeTop || candidate.top + candidateHeight > safeBottom;
    const panelCollision = blockers.some((panel) => candidate.left < panel.right && candidate.left + candidateWidth > panel.left && candidate.top < panel.bottom && candidate.top + candidateHeight > panel.top);
    return coreCollision || panelCollision || outsideViewport;
  };
  const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

  // Try beside the node first. Shrink only when the viewport has no full-size
  // safe slot; the card scrolls only when its content exceeds that slot.
  for (const width of [preferredWidth, Math.min(240, preferredWidth), Math.min(200, preferredWidth)]) {
    const maxHeight = Math.min(preferredHeight, Math.max(0, safeBottom - safeTop));
    if (width < 160 || maxHeight < 96) continue;
    const horizontal = [
      { left: anchorX + 18, top: anchorY - maxHeight / 2 },
      { left: anchorX - width - 18, top: anchorY - maxHeight / 2 },
      { left: anchorX + 18, top: anchorY - maxHeight - 14 },
      { left: anchorX - width - 18, top: anchorY + 14 },
    ];
    const vertical = [
      { left: anchorX - width / 2, top: anchorY - maxHeight - 18 },
      { left: anchorX - width / 2, top: anchorY + 18 },
    ];
    const candidates = [...horizontal, ...vertical].map((candidate) => ({
      left: clamp(candidate.left, 12, viewportWidth - width - 12),
      top: clamp(candidate.top, safeTop, safeBottom - maxHeight),
    }));
    const safeCandidates = candidates.filter((candidate) => !collides(candidate, width, maxHeight));
    safeCandidates.sort((a, b) => Math.hypot(a.left + width / 2 - anchorX, a.top + maxHeight / 2 - anchorY) - Math.hypot(b.left + width / 2 - anchorX, b.top + maxHeight / 2 - anchorY));
    if (safeCandidates[0]) return { ...safeCandidates[0], width, maxHeight };
  }

  // In very short layouts prefer a compact slot in the clear band above the
  // core. Never return a fallback rectangle that crosses the core or dock.
  const fallbackWidth = Math.min(200, preferredWidth, viewportWidth - 24);
  const fallbackHeight = Math.min(180, Math.max(0, coreY - coreRadius - safeTop - 12));
  if (fallbackHeight >= 96) {
    const fallback = { left: clamp(anchorX - fallbackWidth / 2, 12, viewportWidth - fallbackWidth - 12), top: coreY - coreRadius - fallbackHeight - 12 };
    if (!collides(fallback, fallbackWidth, fallbackHeight)) return { ...fallback, width: fallbackWidth, maxHeight: fallbackHeight };
  }

  // Last resort: a narrow, scrollable viewport edge slot, still respecting
  // all reserved regions. This is preferable to covering the intelligence core.
  for (const [left, width] of [[12, fallbackWidth], [viewportWidth - fallbackWidth - 12, fallbackWidth]] as const) {
    for (let top = safeTop; top + 96 <= safeBottom; top += 12) {
      const candidate = { left, top };
      const height = Math.min(160, safeBottom - top);
      if (height >= 96 && !collides(candidate, width, height)) return { ...candidate, width, maxHeight: height };
    }
  }
  // The available geometry can be smaller than a readable card on very short
  // viewports. Keep it on-screen and rely on its internal scroll affordance.
  return { left: 12, top: safeTop, width: fallbackWidth, maxHeight: Math.max(1, safeBottom - safeTop) };
}

export default function ConstellationCanvas(props: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const inputsRef = useRef<SceneInputs | null>(null);
  const renderRef = useRef<() => void>(() => {});
  const positionsRef = useRef<Map<string, ScenePoint>>(new Map());
  const cameraRef = useRef<SceneCamera>({ yaw: 0, pitch: 0 });
  const dragRef = useRef<PointerDrag | null>(null);
  const runtimeRef = useRef({
    elapsed: 0,
    lastTimestamp: 0,
    animationKey: -1,
    investigationKey: -1,
    activeIncidentId: null as string | null,
    recallStartTimes: new Map<string, number>(),
    knownRecallIds: new Set<string>(),
    consensusAt: null as number | null,
    consensusSeen: false,
  });
  const layout = useMemo(() => createSceneLayout(props.memories), [props.memories]);
  const serviceEdges = useMemo(() => createServiceEdges(props.memories), [props.memories]);
  const wakeRanks = useMemo(() => createWakeRanks(layout), [layout]);
  const [isDragging, setDragState] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [inspectionPlacement, setInspectionPlacement] = useState<{ left: number; top: number; width: number; maxHeight: number } | null>(null);
  const lastPlacementUpdateRef = useRef(0);
  const inspectionId = props.selectedId ?? hoveredId;
  const inspectionMemory = inspectionId ? props.memories.find((memory) => memory.id === inspectionId) ?? null : null;

  useEffect(() => {
    const updatePlacement = () => {
      const id = props.selectedId ?? hoveredId;
      const canvas = canvasRef.current;
      const point = id ? positionsRef.current.get(id) : null;
      const rect = canvas?.getBoundingClientRect();
      if (!id || !point || !rect) {
        setInspectionPlacement(null);
        return;
      }
      setInspectionPlacement(placeInspectionCard(rect.left + point.x, rect.top + point.y, rect));
    };
    updatePlacement();
    window.addEventListener("resize", updatePlacement);
    return () => window.removeEventListener("resize", updatePlacement);
  }, [props.selectedId, hoveredId, props.memories]);

  // Keep the renderer alive while React data changes. New memories receive a
  // visual start time at the moment their SSE event reaches the page.
  useEffect(() => {
    const runtime = runtimeRef.current;
    const replayReset = runtime.animationKey !== props.animationKey;
    const incidentReset = runtime.activeIncidentId !== props.activeIncidentId;
    const investigationReset = runtime.investigationKey !== props.investigationKey;

    if (replayReset) {
      runtime.animationKey = props.animationKey;
      runtime.elapsed = 0;
      runtime.lastTimestamp = 0;
      cameraRef.current = { yaw: 0, pitch: 0 };
    }
    if (incidentReset) runtime.activeIncidentId = props.activeIncidentId;
    if (investigationReset) runtime.investigationKey = props.investigationKey;

    if (replayReset || incidentReset || investigationReset) {
      runtime.recallStartTimes.clear();
      runtime.knownRecallIds.clear();
      runtime.consensusAt = null;
      runtime.consensusSeen = false;
      const recalled = props.memories.filter((memory) => memory.recalled).sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0));
      recalled.forEach((memory, index) => {
        runtime.knownRecallIds.add(memory.id);
        const startAt = replayReset && !props.reducedMotion
          ? 650 + index * 260
          : props.active && !props.reducedMotion
            ? runtime.elapsed
            : -RECALL_TRAVEL_MS;
        runtime.recallStartTimes.set(memory.id, startAt);
      });
    }

    for (const memory of props.memories) {
      if (!memory.recalled || runtime.knownRecallIds.has(memory.id)) continue;
      runtime.knownRecallIds.add(memory.id);
      runtime.recallStartTimes.set(memory.id, props.active && !props.reducedMotion ? runtime.elapsed : -RECALL_TRAVEL_MS);
    }

    if (props.reducedMotion) {
      for (const memory of props.memories) {
        if (memory.recalled) runtime.recallStartTimes.set(memory.id, -RECALL_TRAVEL_MS);
      }
      if (props.consensus) runtime.consensusAt = runtime.elapsed - 2400;
      else runtime.consensusAt = null;
    } else if (!props.consensus) {
      runtime.consensusAt = null;
    }
    runtime.consensusSeen = props.consensus;
    inputsRef.current = { ...props, layout, serviceEdges, wakeRanks, hoveredId };
    renderRef.current();
  }, [props, layout, serviceEdges, wakeRanks, hoveredId]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d", { alpha: false });
    if (!canvas || !context) return;

    let width = 0;
    let height = 0;
    let dpr = 1;
    let raf = 0;
    let visible = true;
    const shouldAnimate = () => {
      const inputs = inputsRef.current;
      return visible && !inputs?.paused && !inputs?.reducedMotion;
    };
    const draw = (timestamp = 0) => {
      raf = 0;
      const inputs = inputsRef.current;
      if (!inputs || width <= 0 || height <= 0) return;
      const runtime = runtimeRef.current;
      const animate = shouldAnimate();
      if (animate) {
        const delta = runtime.lastTimestamp ? Math.min(timestamp - runtime.lastTimestamp, 40) : 16;
        runtime.elapsed += Math.max(0, delta);
      }
      runtime.lastTimestamp = timestamp;

      if (inputs.consensus && runtime.consensusAt === null) {
        const recalled = inputs.memories.filter((memory) => memory.recalled);
        const allLanded = recalled.every((memory) => runtime.elapsed - (runtime.recallStartTimes.get(memory.id) ?? -RECALL_TRAVEL_MS) >= RECALL_TRAVEL_MS);
        if (allLanded) runtime.consensusAt = runtime.elapsed;
      }

      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      positionsRef.current = drawScene(context, inputs.memories, inputs.layout, inputs.serviceEdges, {
        elapsed: runtime.elapsed,
        introProgress: inputs.reducedMotion ? 1 : Math.min(1, runtime.elapsed / INTRO_DURATION_MS),
        width,
        height,
        camera: cameraRef.current,
        selectedId: inputs.selectedId,
        hoveredId: inputs.selectedId ? null : inputs.hoveredId,
        active: inputs.active || (inputs.consensus && runtime.elapsed < 1000),
        consensus: inputs.consensus,
        consensusAt: runtime.consensusAt,
        recallStartTimes: runtime.recallStartTimes,
        wakeRanks: inputs.wakeRanks,
      });
      if (animate) raf = window.requestAnimationFrame(draw);
    };

    const invalidate = () => {
      if (visible && raf === 0) raf = window.requestAnimationFrame(draw);
    };
    renderRef.current = invalidate;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(0, rect.width);
      height = Math.max(0, rect.height);
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      invalidate();
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(canvas);
    resize();

    const intersectionObserver = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? true;
      if (visible) {
        runtimeRef.current.lastTimestamp = 0;
        invalidate();
      } else if (raf) {
        window.cancelAnimationFrame(raf);
        raf = 0;
      }
    }, { rootMargin: "80px" });
    intersectionObserver.observe(canvas);

    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      renderRef.current = () => {};
      positionsRef.current.clear();
    };
  }, []);

  const localPoint = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const pickNode = (x: number, y: number): string | null => {
    let picked: string | null = null;
    let distance = 20;
    const introProgress = props.reducedMotion ? 1 : Math.min(1, runtimeRef.current.elapsed / INTRO_DURATION_MS);
    for (const [id, point] of positionsRef.current) {
      const memory = props.memories.find((candidate) => candidate.id === id);
      if (!memory?.recalled) {
        const rank = wakeRanks.get(id) ?? 0;
        const revealStart = 0.04 + rank * 0.48;
        if (introProgress <= revealStart) continue;
      }
      const candidate = Math.hypot(point.x - x, point.y - y);
      if (candidate < distance) {
        distance = candidate;
        picked = id;
      }
    }
    return picked;
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.button !== 0) return;
    setHoveredId(null);
    props.onHover?.(null);
    const point = localPoint(event);
    dragRef.current = { pointerId: event.pointerId, ...point, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragState(true);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const active = dragRef.current;
    const point = localPoint(event);
    if (active?.pointerId !== event.pointerId) {
      const nextHovered = pickNode(point.x, point.y);
      if (hoveredId !== nextHovered) {
        setHoveredId(nextHovered);
        props.onHover?.(nextHovered);
      }
      renderRef.current();
      return;
    }
    const dx = point.x - active.x;
    const dy = point.y - active.y;
    if (Math.abs(dx) + Math.abs(dy) > 1) active.moved = true;
    if (active.moved) {
      const rect = event.currentTarget.getBoundingClientRect();
      cameraRef.current = {
        yaw: cameraRef.current.yaw + dx / Math.max(1, rect.width) * 2.4,
        pitch: Math.max(-1.5, Math.min(1.5, cameraRef.current.pitch + dy / Math.max(1, rect.height) * 1.8)),
      };
      active.x = point.x;
      active.y = point.y;
      renderRef.current();
      if (props.selectedId && event.timeStamp - lastPlacementUpdateRef.current > 90) {
        lastPlacementUpdateRef.current = event.timeStamp;
        window.requestAnimationFrame(() => {
          const canvas = canvasRef.current;
          const selectedPoint = positionsRef.current.get(props.selectedId!);
          const rect = canvas?.getBoundingClientRect();
          if (selectedPoint && rect) setInspectionPlacement(placeInspectionCard(rect.left + selectedPoint.x, rect.top + selectedPoint.y, rect));
        });
      }
    }
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const active = dragRef.current;
    if (active?.pointerId !== event.pointerId) return;
    if (!active.moved) {
      const point = localPoint(event);
      const picked = pickNode(point.x, point.y);
      if (picked) props.onSelect(picked === props.selectedId ? null : picked);
    }
    dragRef.current = null;
    setDragState(false);
  };

  return (
    <>
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="absolute inset-0 z-0 h-full w-full touch-none"
        style={{ cursor: isDragging ? "grabbing" : "grab" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { setHoveredId(null); props.onHover?.(null); }}
        onPointerCancel={() => { dragRef.current = null; setDragState(false); setHoveredId(null); props.onHover?.(null); }}
      />
      {inspectionMemory && inspectionPlacement && typeof document !== "undefined" && createPortal(
        <section
          className={`memory-inspection-card ${props.selectedId === inspectionMemory.id ? "is-locked" : "is-preview"}`}
          style={{ left: inspectionPlacement.left, top: inspectionPlacement.top, width: inspectionPlacement.width, maxHeight: inspectionPlacement.maxHeight }}
          aria-label={`Memory inspection: ${inspectionMemory.title}`}
          aria-live={props.selectedId === inspectionMemory.id ? "polite" : undefined}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <div className="mb-2 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-mono text-[8px] tracking-[0.1em] text-[#38BDF8]">ACTIVE INCIDENT · {props.activeIncidentId ?? "NONE"}</p>
              <p className="mt-1 font-mono text-[9px] tracking-[0.14em] text-[#A855F7]">{inspectionMemory.id === props.activeIncidentId ? "ACTIVE INCIDENT MEMORY" : "HISTORICAL MEMORY"}</p>
              <p className="font-mono text-[9px] tracking-[0.14em] text-[#FACC15]">{props.selectedId === inspectionMemory.id ? "LOCKED IN FOCUS" : "HOVER PREVIEW"}</p>
              <p className="mt-1 break-all font-mono text-[10px] text-[#DDD6FE]">{inspectionMemory.id}</p>
            </div>
            {props.selectedId === inspectionMemory.id && <button type="button" className="memory-inspection-clear" aria-label="Clear selected memory" onClick={() => props.onSelect(null)}>×</button>}
          </div>
          <h2 className="break-words text-[13px] font-semibold leading-snug text-[#F4F0FF]">{inspectionMemory.title}</h2>
          <p className="mt-1 break-words text-[11px] text-[#C4B5FD]">{inspectionMemory.service}</p>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-[#38BDF8]">
            {inspectionMemory.relevance != null && <span>MATCH {Math.round(inspectionMemory.relevance * 100)}%</span>}
            {inspectionMemory.timeToResolveMinutes != null && <span>TTR {inspectionMemory.timeToResolveMinutes}m</span>}
          </div>
          {inspectionMemory.rootCause && <p className="mt-2 break-words text-[11px] leading-relaxed text-[#DDD6FE]"><b className="text-[#A855F7]">Root cause</b><br />{inspectionMemory.rootCause}</p>}
          {inspectionMemory.failedAttempts.length > 0 && <div className="mt-2 break-words text-[11px] leading-relaxed text-[#FEF08A]"><b className="text-[#FACC15]">Prior attempts · did not resolve</b><ul className="mt-1 list-disc space-y-1 pl-4">{inspectionMemory.failedAttempts.map((attempt, index) => <li key={`${index}-${attempt}`}>{attempt}</li>)}</ul></div>}
          {inspectionMemory.resolution && <p className="mt-2 break-words text-[11px] leading-relaxed text-[#FEF08A]"><b>Verified fix</b><br />{inspectionMemory.resolution}</p>}
          {inspectionMemory.summary && <p className="mt-2 break-words border-t border-[#A855F7]/20 pt-2 text-[10px] leading-relaxed text-[#C4B5FD]"><b className="text-[#A855F7]">{inspectionMemory.recalled ? "Historical evidence" : "Incident context"}</b><br />{inspectionMemory.summary}</p>}
        </section>,
        document.body,
      )}
    </>
  );
}
