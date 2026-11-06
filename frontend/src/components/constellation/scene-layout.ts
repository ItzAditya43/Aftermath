import type { SceneCamera, SceneMemory, SceneNode, ScenePoint } from "@/lib/memory-types";

const TAU = Math.PI * 2;
const RING_RADII = [0.205, 0.315, 0.425] as const;

/** A hit can replace the current pick only when it is both inside its radius and nearer. */
export function isCloserEligibleHit(candidateDistance: number, hitRadius: number, bestDistance: number): boolean {
  return candidateDistance < hitRadius && candidateDistance < bestDistance;
}

function hash(value: string, salt = ""): number {
  let result = 2166136261;
  for (const character of `${value}:${salt}`) {
    result ^= character.charCodeAt(0);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0) / 4294967295;
}

/** Position each incident from its ID so refreshing the bank does not reshuffle it. */
export function createSceneLayout(memories: SceneMemory[]): SceneNode[] {
  return memories.map((memory) => {
    const ring = memory.recalled ? 2 : Math.floor(hash(memory.id, "ring") * 3) as 0 | 1 | 2;
    const jitter = (hash(memory.id, "radius") - 0.5) * 0.026;
    return {
      memoryId: memory.id,
      ring,
      angle: hash(memory.id, "angle") * TAU,
      radius: RING_RADII[ring] + jitter,
      depth: 1 - ring * 0.23,
      size: 1.45 + hash(memory.id, "size") * 1.5 + (2 - ring) * 0.25,
      phase: hash(memory.id, "phase") * TAU,
    };
  });
}

export function createWakeRanks(layout: SceneNode[]): Map<string, number> {
  const ordered = layout
    .map((node) => ({ id: node.memoryId, rank: Math.hypot(node.radius, node.angle / TAU) * 0.5 + (node.angle + Math.PI) * 0.035 }))
    .sort((a, b) => a.rank - b.rank);
  return new Map(ordered.map((node, index) => [node.id, index / Math.max(1, ordered.length - 1)]));
}

export function getCameraScale(width: number, height: number): number {
  return Math.max(0.18, Math.min(1, width / 1400, height / 820));
}

export function getCorePoint(width: number, height: number): ScenePoint {
  // On compact screens, keep the core between the mobile HUD and dock.
  return { x: width / 2, y: height * (width < 768 ? 0.52 : 0.44) };
}

export function getScenePositions(
  layout: SceneNode[],
  width: number,
  height: number,
  elapsed: number,
  camera: SceneCamera,
): Map<string, ScenePoint> {
  const scale = Math.min(width, height) * 1.02;
  const { x: centerX, y: centerY } = getCorePoint(width, height);
  const flatten = Math.max(0.57, Math.min(0.77, 0.74 + camera.pitch * 0.045));
  const positions = new Map<string, ScenePoint>();

  for (const node of layout) {
    const breathe = 1 + Math.sin(elapsed * 0.0004 + node.phase) * 0.004;
    const angle = node.angle + camera.yaw + elapsed * 0.000008;
    const radius = node.radius * scale * breathe;
    positions.set(node.memoryId, {
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius * flatten,
    });
  }

  return positions;
}

export function getSatellitePoint(
  index: number,
  total: number,
  relevance: number,
  width: number,
  height: number,
  camera: SceneCamera,
): ScenePoint {
  const { x: centerX, y: centerY } = getCorePoint(width, height);
  const scale = getCameraScale(width, height);
  const radius = (165 + Math.min(total, 8) * 4) * scale;

  let baseAngle: number;
  if (total <= 1) {
    baseAngle = -Math.PI / 2;
  } else if (total === 2) {
    baseAngle = index === 0 ? -Math.PI / 2 - Math.PI / 3 : -Math.PI / 2 + Math.PI / 3;
  } else {
    baseAngle = (index / total) * TAU - Math.PI / 2;
  }

  const angle = baseAngle + camera.yaw * 0.15;
  return {
    x: centerX + Math.cos(angle) * radius,
    y: centerY + Math.sin(angle) * radius * 0.74,
  };
}

export type ConstellationEdge = { from: number; to: number; isPerimeter: boolean };

export type ConstellationEdgeTiming = ConstellationEdge & {
  startIndex: number;
  startTime: number;
  endTime: number;
  duration: number;
};

export type ConstellationTiming = {
  edges: ConstellationEdgeTiming[];
  perimeterCompleteAt: number;
  formationCompleteAt: number;
};

/**
 * Returns shortest signed angular difference (target - source) in range [-PI, PI).
 */
export function normalizeAngleDiff(target: number, source: number): number {
  const diff = (target - source) % TAU;
  return ((diff + Math.PI * 3) % TAU) - Math.PI;
}

/**
 * Calculates deterministic start and end times for constellation edges.
 * Perimeter edges draw in sequence as their endpoint stars land.
 * For 4+ nodes, internal chords draw in deliberate sequence strictly after the perimeter completes.
 * The aura release triggers when the last edge in the entire formation finishes.
 */
export function calculateConstellationTiming(
  edges: ConstellationEdge[],
  landedTimes: number[],
  options?: {
    reducedMotion?: boolean;
    perimeterEdgeDuration?: number;
    chordEdgeDuration?: number;
  },
): ConstellationTiming {
  if (edges.length === 0) {
    const landed = landedTimes[0] ?? 0;
    return { edges: [], perimeterCompleteAt: landed, formationCompleteAt: landed };
  }

  if (options?.reducedMotion) {
    const timedEdges: ConstellationEdgeTiming[] = edges.map((edge, index) => ({
      ...edge,
      startIndex: index,
      startTime: -10000,
      endTime: -10000,
      duration: 0,
    }));
    return { edges: timedEdges, perimeterCompleteAt: -10000, formationCompleteAt: -10000 };
  }

  const perimeterEdges = edges.filter((e) => e.isPerimeter);
  const chordEdges = edges.filter((e) => !e.isPerimeter);

  const defaultPerimeterDuration = edges.length === 1
    ? 460
    : Math.max(220, Math.min(380, Math.round(1520 / Math.max(1, perimeterEdges.length))));
  const perimDuration = options?.perimeterEdgeDuration ?? defaultPerimeterDuration;

  const defaultChordDuration = Math.max(160, Math.min(280, Math.round(1120 / Math.max(1, chordEdges.length))));
  const chordDuration = options?.chordEdgeDuration ?? defaultChordDuration;

  const timedPerimeter: ConstellationEdgeTiming[] = [];
  let prevPerimeterEnd = 0;
  for (let i = 0; i < perimeterEdges.length; i += 1) {
    const edge = perimeterEdges[i];
    const uLanded = landedTimes[edge.from] ?? 0;
    const vLanded = landedTimes[edge.to] ?? 0;
    const readyAt = Math.max(uLanded, vLanded);
    const startTime = i === 0 ? readyAt : Math.max(prevPerimeterEnd, readyAt);
    const endTime = startTime + perimDuration;
    prevPerimeterEnd = endTime;
    timedPerimeter.push({
      ...edge,
      startIndex: i,
      startTime,
      endTime,
      duration: perimDuration,
    });
  }

  const perimeterCompleteAt = perimeterEdges.length > 0 ? prevPerimeterEnd : (landedTimes[0] ?? 0);

  const timedChords: ConstellationEdgeTiming[] = [];
  let prevChordEnd = perimeterCompleteAt;
  for (let i = 0; i < chordEdges.length; i += 1) {
    const edge = chordEdges[i];
    const uLanded = landedTimes[edge.from] ?? 0;
    const vLanded = landedTimes[edge.to] ?? 0;
    const readyAt = Math.max(uLanded, vLanded);
    const startTime = Math.max(prevChordEnd, readyAt);
    const endTime = startTime + chordDuration;
    prevChordEnd = endTime;
    timedChords.push({
      ...edge,
      startIndex: perimeterEdges.length + i,
      startTime,
      endTime,
      duration: chordDuration,
    });
  }

  return {
    edges: [...timedPerimeter, ...timedChords],
    perimeterCompleteAt,
    formationCompleteAt: chordEdges.length > 0 ? prevChordEnd : perimeterCompleteAt,
  };
}

export function getConstellationEdges(total: number): ConstellationEdge[] {
  if (total < 2) return [];
  if (total === 2) return [{ from: 0, to: 1, isPerimeter: true }];
  if (total === 3) {
    return [
      { from: 0, to: 1, isPerimeter: true },
      { from: 1, to: 2, isPerimeter: true },
      { from: 2, to: 0, isPerimeter: true },
    ];
  }
  if (total === 4) {
    return [
      { from: 0, to: 1, isPerimeter: true },
      { from: 1, to: 2, isPerimeter: true },
      { from: 2, to: 3, isPerimeter: true },
      { from: 3, to: 0, isPerimeter: true },
      { from: 0, to: 2, isPerimeter: false },
      { from: 1, to: 3, isPerimeter: false },
    ];
  }
  const edges: ConstellationEdge[] = [];
  for (let i = 0; i < total; i += 1) {
    edges.push({ from: i, to: (i + 1) % total, isPerimeter: true });
  }
  for (let i = 0; i < total; i += 1) {
    edges.push({ from: i, to: (i + 2) % total, isPerimeter: false });
  }
  return edges;
}

export function getOrbitalRadii(width: number, height: number): number[] {
  const scale = Math.min(width, height) * 1.02;
  return RING_RADII.map((radius) => radius * scale);
}
