import type { SceneCamera, SceneMemory, SceneNode, ScenePoint } from "@/lib/memory-types";

const TAU = Math.PI * 2;
const RING_RADII = [0.205, 0.315, 0.425] as const;

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
  return { x: width / 2, y: height * (width < 768 ? 0.62 : 0.44) };
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
  const normalized = total <= 1 ? 0 : index / (total - 1);
  const relevanceBias = Math.max(0, Math.min(1, relevance));
  const radius = (125 + normalized * 115 + (0.5 - relevanceBias) * 10) * scale;
  const angle = (index / Math.max(1, total)) * TAU - Math.PI / 2 + camera.yaw * 0.15;
  return {
    x: centerX + Math.cos(angle) * radius,
    y: centerY + Math.sin(angle) * radius * 0.74,
  };
}

export function getOrbitalRadii(width: number, height: number): number[] {
  const scale = Math.min(width, height) * 1.02;
  return RING_RADII.map((radius) => radius * scale);
}
