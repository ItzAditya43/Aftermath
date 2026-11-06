import type { SceneCamera, SceneMemory, SceneNode, ScenePoint } from "@/lib/memory-types";
import { getCameraScale, getCorePoint, getOrbitalRadii, getSatellitePoint, getScenePositions } from "./scene-layout";

const PALETTE = {
  black: "#03040B",
  navy: "#0D1022",
  violet: "#A855F7",
  gold: "#FACC15",
  white: "#DDD6FE",
};
const TAU = Math.PI * 2;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const easeOut = (value: number) => 1 - (1 - clamp(value, 0, 1)) ** 3;

export type DrawSceneFrame = {
  elapsed: number;
  introProgress: number;
  width: number;
  height: number;
  camera: SceneCamera;
  selectedId: string | null;
  hoveredId: string | null;
  active: boolean;
  consensus: boolean;
  consensusAt: number | null;
  recallStartTimes: Map<string, number>;
  wakeRanks: Map<string, number>;
};

export type ServiceEdge = { from: string; to: string; phase: number };

export function createServiceEdges(memories: SceneMemory[]): ServiceEdge[] {
  const byService = new Map<string, SceneMemory[]>();
  for (const memory of memories) {
    const group = byService.get(memory.service) ?? [];
    group.push(memory);
    byService.set(memory.service, group);
  }

  const edges: ServiceEdge[] = [];
  for (const service of Array.from(byService.keys()).sort()) {
    const group = byService.get(service);
    if (!group) continue;
    group.sort((a, b) => a.id.localeCompare(b.id));
    for (let index = 0; index + 1 < group.length; index += 1) {
      const from = group[index];
      const to = group[index + 1];
      if (from && to) edges.push({ from: from.id, to: to.id, phase: edges.length });
    }
  }
  const visibleEdges = edges.slice(0, 120);
  return visibleEdges.map((edge, index) => ({ ...edge, phase: index / Math.max(1, visibleEdges.length - 1) }));
}

function quadraticPoint(start: ScenePoint, control: ScenePoint, end: ScenePoint, t: number): ScenePoint {
  const inverse = 1 - t;
  return {
    x: inverse * inverse * start.x + 2 * inverse * t * control.x + t * t * end.x,
    y: inverse * inverse * start.y + 2 * inverse * t * control.y + t * t * end.y,
  };
}

function curveControl(start: ScenePoint, end: ScenePoint, angle: number, scale: number): ScenePoint {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy) || 1;
  const curvature = (Math.sin(angle * 2) * 45 + 30) * scale;
  return { x: (start.x + end.x) / 2 - (dy / length) * curvature, y: (start.y + end.y) / 2 + (dx / length) * curvature };
}

function seeded(index: number, salt: number): number {
  const value = Math.sin(index * 127.1 + salt * 311.7) * 43758.5453;
  return value - Math.floor(value);
}

export function drawScene(
  context: CanvasRenderingContext2D,
  memories: SceneMemory[],
  layout: SceneNode[],
  serviceEdges: ServiceEdge[],
  frame: DrawSceneFrame,
): Map<string, ScenePoint> {
  const { elapsed, width, height, camera } = frame;
  const center = getCorePoint(width, height);
  const scale = Math.min(width, height);
  const cameraScale = getCameraScale(width, height);
  const targetPositions = getScenePositions(layout, width, height, elapsed, camera);
  const introProgress = clamp(frame.introProgress, 0, 1);
  const nodeFormation = new Map<string, number>();
  const basePositions = new Map<string, ScenePoint>();
  for (const node of layout) {
    const target = targetPositions.get(node.memoryId);
    if (!target) continue;
    const rank = frame.wakeRanks.get(node.memoryId) ?? 0;
    const releaseAt = 0.1 + rank * 0.48;
    const formation = easeOut((introProgress - releaseAt) / 0.32);
    nodeFormation.set(node.memoryId, formation);
    basePositions.set(node.memoryId, {
      x: center.x + (target.x - center.x) * formation,
      y: center.y + (target.y - center.y) * formation,
    });
  }
  const memoryById = new Map(memories.map((memory) => [memory.id, memory]));
  const nodeById = new Map(layout.map((node) => [node.memoryId, node]));
  const recallRecords = memories.filter((memory) => memory.recalled).sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0));
  const scenePoints = new Map(basePositions);
  const arrival = new Map<string, { start: ScenePoint; target: ScenePoint; progress: number; landedAt: number; control: ScenePoint }>();

  recallRecords.forEach((memory, index) => {
    const origin = basePositions.get(memory.id);
    const node = nodeById.get(memory.id);
    if (!origin || !node) return;
    const startAt = frame.recallStartTimes.get(memory.id) ?? -1000;
    const progress = easeOut((elapsed - startAt) / 900);
    const satellite = getSatellitePoint(index, recallRecords.length, memory.relevance ?? 0.5, width, height, camera);
    const formation = nodeFormation.get(memory.id) ?? 1;
    const target = { x: center.x + (satellite.x - center.x) * formation, y: center.y + (satellite.y - center.y) * formation };
    const control = curveControl(origin, target, node.angle, cameraScale);
    const point = quadraticPoint(origin, control, target, progress);
    scenePoints.set(memory.id, point);
    arrival.set(memory.id, { start: origin, target, progress, landedAt: startAt + 900, control });
  });

  context.clearRect(0, 0, width, height);
  context.fillStyle = PALETTE.black;
  context.fillRect(0, 0, width, height);

  // 1. Deep atmosphere and volumetric violet light.
  const atmosphere = context.createRadialGradient(center.x, center.y, 0, center.x, center.y, scale * 0.7);
  const coreBuild = easeOut((introProgress - 0.025) / 0.48);
  atmosphere.addColorStop(0, `rgba(124, 58, 237, ${0.025 + coreBuild * 0.095})`);
  atmosphere.addColorStop(0.34, `rgba(168, 85, 247, ${0.018 + coreBuild * 0.072})`);
  atmosphere.addColorStop(0.72, `rgba(13, 16, 34, ${0.008 + coreBuild * 0.027})`);
  atmosphere.addColorStop(1, "rgba(3, 4, 11, 0)");
  context.fillStyle = atmosphere;
  context.fillRect(0, 0, width, height);

  // 2. Parallax coordinate grid and deterministic depth stardust.
  const grid = 58;
  const offsetX = ((center.x + camera.yaw * 30) % grid + grid) % grid;
  const offsetY = ((center.y + camera.pitch * 24) % grid + grid) % grid;
  context.fillStyle = "rgba(168, 85, 247, 0.055)";
  for (let x = offsetX - grid; x < width + grid; x += grid) {
    for (let y = offsetY - grid; y < height + grid; y += grid) {
      const distance = Math.hypot((x - center.x) / width, (y - center.y) / height);
      if (distance < 0.82) {
        context.beginPath();
        context.arc(x, y, 0.8, 0, TAU);
        context.fill();
      }
    }
  }
  for (let index = 0; index < 125; index += 1) {
    const x = (seeded(index, 1) * width + camera.yaw * (4 + index % 15)) % width;
    const y = (seeded(index, 2) * height + camera.pitch * (4 + index % 11)) % height;
    const twinkle = (0.055 + 0.09 * (0.5 + 0.5 * Math.sin(elapsed * 0.0009 + index * 2.1))) * (0.16 + introProgress * 0.84);
    context.beginPath();
    context.arc((x + width) % width, (y + height) % height, 0.3 + seeded(index, 3) * 1.1, 0, TAU);
    context.fillStyle = `rgba(168, 85, 247, ${twinkle})`;
    context.fill();
  }

  // A short-lived ignition plume uses deterministic energy motes, not incident
  // records. Incident nodes and their real edges are formed in the stages below.
  if (introProgress < 0.82) {
    for (let index = 0; index < 22; index += 1) {
      const delay = 0.035 + seeded(index, 6) * 0.09;
      const progress = clamp((introProgress - delay) / 0.64, 0, 1);
      if (progress <= 0 || progress >= 1) continue;
      const angle = seeded(index, 7) * TAU;
      const distance = scale * 0.42 * easeOut(progress);
      const alpha = (1 - progress) * 0.52;
      context.beginPath();
      context.arc(center.x + Math.cos(angle) * distance, center.y + Math.sin(angle) * distance * 0.72, 0.7 + seeded(index, 8) * 1.25, 0, TAU);
      context.fillStyle = `rgba(221, 214, 254, ${alpha})`;
      context.shadowColor = PALETTE.violet;
      context.shadowBlur = 8;
      context.fill();
      context.shadowBlur = 0;
    }
  }

  // 3. Three perspective orbit guides.
  const ringBuild = easeOut(introProgress / 0.78);
  const orbitalRadii = getOrbitalRadii(width, height);
  orbitalRadii.forEach((radius, index) => {
    context.beginPath();
    context.ellipse(center.x, center.y, radius * ringBuild, radius * 0.74 * ringBuild, 0, 0, TAU);
    context.strokeStyle = index === 2
      ? `rgba(250, 204, 21, ${0.36 * ringBuild})`
      : `rgba(124, 58, 237, ${(0.08 + (1 - index) * 0.035) * ringBuild})`;
    context.lineWidth = index === 0 ? 1 : 0.7;
    context.setLineDash(index === 1 ? [4, 9] : [2, 7]);
    context.stroke();
  });
  context.setLineDash([]);

  // 4. Real incident bank. Wake rank is computed once from the stable layout.
  const consensusAge = frame.consensusAt === null ? -1 : elapsed - frame.consensusAt;
  const burstStrength = frame.consensus ? clamp(1 - consensusAge / 2400, 0, 1) : 0;
  const primaryWave = frame.consensus && consensusAge >= 0 ? { radius: 20 + consensusAge * 0.52, alpha: clamp(1 - consensusAge / 1900, 0, 1) } : null;
  const secondaryWave = frame.consensus && consensusAge >= 40 ? { radius: 8 + (consensusAge - 40) * 0.43, alpha: clamp(0.8 - (consensusAge - 40) / 1900, 0, 0.8) } : null;

  for (const node of layout) {
    const memory = memoryById.get(node.memoryId);
    const point = scenePoints.get(node.memoryId);
    if (!memory || !point || memory.recalled) continue;
    const rank = frame.wakeRanks.get(node.memoryId) ?? 0;
    const nodeReveal = easeOut((introProgress - (0.04 + rank * 0.48)) / 0.13);
    if (nodeReveal <= 0) continue;
    const wake = (frame.active ? clamp((elapsed / 1000 - rank) / 0.18, 0, 1) : 1) * nodeReveal;
    const breathe = 0.5 + 0.5 * Math.sin(elapsed * 0.00075 + node.phase);
    const distance = Math.hypot(point.x - center.x, point.y - center.y);
    let waveGlow = 0;
    if (primaryWave) waveGlow = Math.max(waveGlow, (1 - clamp(Math.abs(distance - primaryWave.radius) / 90, 0, 1)) * primaryWave.alpha);
    if (secondaryWave) waveGlow = Math.max(waveGlow, (1 - clamp(Math.abs(distance - secondaryWave.radius) / 75, 0, 1)) * secondaryWave.alpha * 0.55);
    const drift = burstStrength > 0 && distance < 560 * cameraScale ? burstStrength * Math.max(0, 0.16 - node.depth * 0.045) : 0;
    const x = center.x + (point.x - center.x) * (1 + drift);
    const y = center.y + (point.y - center.y) * (1 + drift);
    const alpha = frame.consensus && consensusAge > 1200 ? 0.15 : wake * (0.26 + node.depth * 0.62);
    const radius = node.size * (0.68 + nodeReveal * 0.32) + waveGlow * 2.5;
    context.beginPath();
    context.arc(x, y, radius, 0, TAU);
    context.fillStyle = memory.recalled || waveGlow > 0.15
        ? `rgba(250, 204, 21, ${alpha + waveGlow * 0.44})`
      : `rgba(168, 85, 247, ${alpha * (0.68 + breathe * 0.32)})`;
    context.shadowColor = memory.recalled || waveGlow > 0.15 ? PALETTE.gold : PALETTE.violet;
    context.shadowBlur = memory.recalled ? 12 : waveGlow * 16;
    context.fill();
    context.shadowBlur = 0;
  }

  // Faint same-service links are visual constellation scaffolding only; they
  // are not historical recall or evidence. Draw them as their nodes form.
  for (const edge of serviceEdges) {
    const from = scenePoints.get(edge.from);
    const to = scenePoints.get(edge.to);
    const fromMemory = memoryById.get(edge.from);
    const toMemory = memoryById.get(edge.to);
    if (!from || !to || fromMemory?.recalled || toMemory?.recalled) continue;
    const endpointFormation = Math.min(nodeFormation.get(edge.from) ?? 0, nodeFormation.get(edge.to) ?? 0);
    const edgeProgress = easeOut((introProgress - (0.18 + edge.phase * 0.38)) / 0.2) * endpointFormation;
    if (edgeProgress <= 0) continue;
    const drawnEnd = { x: from.x + (to.x - from.x) * edgeProgress, y: from.y + (to.y - from.y) * edgeProgress };
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(drawnEnd.x, drawnEnd.y);
    context.strokeStyle = `rgba(168, 85, 247, ${0.075 * edgeProgress})`;
    context.lineWidth = 0.6;
    context.stroke();
    if (edgeProgress < 0.995) continue;
    const progress = (elapsed * 0.0001 + edge.phase) % 1;
    context.beginPath();
    context.arc(from.x + (to.x - from.x) * progress, from.y + (to.y - from.y) * progress, 1, 0, TAU);
    context.fillStyle = "rgba(168, 85, 247, 0.46)";
    context.fill();
  }

  if (frame.selectedId) {
    const selectedPoint = scenePoints.get(frame.selectedId);
    if (selectedPoint) {
      context.beginPath();
      context.moveTo(center.x, center.y);
      context.lineTo(selectedPoint.x, selectedPoint.y);
      const focusLink = context.createLinearGradient(center.x, center.y, selectedPoint.x, selectedPoint.y);
      focusLink.addColorStop(0, "rgba(124, 58, 237, 0.1)");
      focusLink.addColorStop(1, "rgba(250, 204, 21, 0.78)");
      context.strokeStyle = focusLink;
      context.lineWidth = 1.2;
      context.setLineDash([2, 5]);
      context.stroke();
      context.setLineDash([]);
    }
  }

  // 5. Backend-recalled memories detach and migrate along curved filaments.
  recallRecords.forEach((memory, index) => {
    const node = nodeById.get(memory.id);
    const motion = arrival.get(memory.id);
    const point = scenePoints.get(memory.id);
    if (!node || !motion || !point) return;
    const startAt = frame.recallStartTimes.get(memory.id) ?? -1000;
    const migrating = elapsed >= startAt && motion.progress < 1;
    const landedAt = motion.landedAt;
    const flare = migrating ? 0 : clamp(1 - (elapsed - landedAt) / 650, 0, 1);
    const start = migrating ? motion.start : center;
    const endpoint = migrating ? point : motion.target;
    const control = migrating ? curveControl(start, endpoint, node.angle, cameraScale) : curveControl(center, endpoint, node.angle, cameraScale);

    context.beginPath();
    context.moveTo(center.x, center.y);
    context.quadraticCurveTo(control.x, control.y, migrating ? point.x : motion.target.x, migrating ? point.y : motion.target.y);
    const filament = context.createLinearGradient(center.x, center.y, endpoint.x, endpoint.y);
    filament.addColorStop(0, "rgba(124, 58, 237, 0.94)");
    filament.addColorStop(0.55, "rgba(168, 85, 247, 0.92)");
    filament.addColorStop(1, "rgba(250, 204, 21, 0.98)");
    context.strokeStyle = filament;
    context.lineWidth = migrating ? 1.35 : 0.8;
    context.shadowColor = migrating ? PALETTE.violet : PALETTE.gold;
    context.shadowBlur = migrating ? 13 : 4;
    context.stroke();
    context.shadowBlur = 0;

    // A compact vapor trail decays behind the moving satellite.
    if (migrating) {
      const particleCount = 36;
      for (let particle = 0; particle < particleCount; particle += 1) {
        const trailT = motion.progress - particle * 0.0065;
        if (trailT <= 0) continue;
        const vapor = quadraticPoint(motion.start, curveControl(motion.start, motion.target, node.angle, cameraScale), motion.target, trailT);
        const alpha = (1 - particle / particleCount) * 0.3;
        context.beginPath();
        context.arc(vapor.x, vapor.y, 0.6 + (1 - particle / particleCount) * 0.8, 0, TAU);
        context.fillStyle = `rgba(168, 85, 247, ${alpha})`;
        context.fill();
      }
      const spark = quadraticPoint(motion.start, curveControl(motion.start, motion.target, node.angle, cameraScale), motion.target, motion.progress);
      context.beginPath();
      context.arc(spark.x, spark.y, 3.4, 0, TAU);
      context.fillStyle = PALETTE.white;
      context.shadowColor = PALETTE.gold;
      context.shadowBlur = 20;
      context.fill();
      context.shadowBlur = 0;
    }

    if (flare > 0) {
      context.beginPath();
      context.arc(point.x, point.y, 8 + (1 - flare) * 24, 0, TAU);
      context.strokeStyle = `rgba(250, 204, 21, ${flare * 0.8})`;
      context.lineWidth = 1 + flare;
      context.shadowColor = PALETTE.gold;
      context.shadowBlur = 18 * flare;
      context.stroke();
      context.shadowBlur = 0;
    }

    // Two photon packets cycle along each settled quadratic connection.
    if (!migrating) {
      for (let packet = 0; packet < 2; packet += 1) {
        const t = (elapsed * 0.00032 + index * 0.13 + packet * 0.5) % 1;
        const photon = quadraticPoint(center, control, motion.target, t);
        context.beginPath();
        context.arc(photon.x, photon.y, 1.6, 0, TAU);
        context.fillStyle = PALETTE.gold;
        context.shadowColor = PALETTE.gold;
        context.shadowBlur = 12;
        context.fill();
        context.shadowBlur = 0;
      }
    }

    const hovered = frame.hoveredId === memory.id;
    const selected = frame.selectedId === memory.id;
    const haloRadius = hovered || selected ? 26 : 17 + Math.sin(elapsed * 0.002 + index) * 3;
    const halo = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, haloRadius);
    halo.addColorStop(0, "rgba(250, 204, 21, 0.38)");
    halo.addColorStop(1, "rgba(250, 204, 21, 0)");
    context.fillStyle = halo;
    context.beginPath();
    context.arc(point.x, point.y, haloRadius, 0, TAU);
    context.fill();
    context.beginPath();
    context.arc(point.x, point.y, 5 + (selected ? 1.5 : 0), 0, TAU);
    context.fillStyle = PALETTE.gold;
    context.shadowColor = PALETTE.gold;
    context.shadowBlur = 18;
    context.fill();
    context.shadowBlur = 0;
    if (hovered || selected) {
      context.beginPath();
      context.arc(point.x, point.y, 12, 0, TAU);
      context.strokeStyle = PALETTE.gold;
      context.lineWidth = selected ? 1.8 : 1.1;
      context.stroke();
    }
  });

  // Non-recalled bank nodes remain visible and selectable.
  for (const node of layout) {
    const memory = memoryById.get(node.memoryId);
    const point = scenePoints.get(node.memoryId);
    if (!memory || !point || memory.recalled) continue;
    const reveal = easeOut((introProgress - (0.04 + (frame.wakeRanks.get(node.memoryId) ?? 0) * 0.48)) / 0.13);
    if (reveal <= 0) continue;
    const selected = frame.selectedId === memory.id;
    const hovered = frame.hoveredId === memory.id;
    const radius = node.size * (0.65 + reveal * 0.35) + (selected ? 1.8 : 0);
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, TAU);
    context.fillStyle = memory.severity === "SEV1" ? `rgba(250, 204, 21, ${0.9 * reveal})` : `rgba(168, 85, 247, ${0.86 * reveal})`;
    context.shadowColor = memory.severity === "SEV1" ? PALETTE.gold : PALETTE.violet;
    context.shadowBlur = (hovered || selected ? 14 : 4) * reveal;
    context.fill();
    context.shadowBlur = 0;
    if (hovered || selected) {
      context.beginPath();
      context.arc(point.x, point.y, radius + 6, 0, TAU);
      context.strokeStyle = selected ? PALETTE.gold : "rgba(168, 85, 247, 0.9)";
      context.lineWidth = selected ? 1.6 : 1;
      context.stroke();
    }
  }

  // 6. Singularity core: progress lens, chromatic bloom and anamorphic flare.
  const compression = frame.consensus && consensusAge >= 0 && consensusAge < 120 ? 1 - (consensusAge / 120) * 0.2 : frame.consensus && consensusAge < 280 ? 0.8 + ((consensusAge - 120) / 160) * 0.2 : 1;
  const kineticZoom = frame.consensus && consensusAge >= 0 ? 1 + 0.065 * Math.exp(-consensusAge / 900) : 1;
  const shake = frame.consensus && consensusAge >= 0 ? Math.sin(consensusAge * 0.12) * 8 * Math.exp(-consensusAge / 380) : 0;
  const coreX = center.x + shake;
  const coreY = center.y + (width < 768 ? 0 : -10) + Math.cos(consensusAge * 0.11) * 8 * Math.exp(-Math.max(0, consensusAge) / 380);
  const corePulse = (1 + Math.sin(elapsed * 0.0032) * 0.06) * compression;
  const coreRadius = 112 * cameraScale * kineticZoom * corePulse * (0.22 + coreBuild * 0.78);
  context.beginPath();
  context.arc(coreX, coreY, coreRadius, 0, TAU);
  context.strokeStyle = `rgba(168, 85, 247, ${0.08 + coreBuild * 0.24})`;
  context.lineWidth = 1;
  context.stroke();
  const progress = recallRecords.length ? recallRecords.filter((memory) => (arrival.get(memory.id)?.progress ?? 0) >= 1).length / recallRecords.length : 0;
  context.beginPath();
  context.arc(coreX, coreY, coreRadius, -Math.PI / 2, -Math.PI / 2 + TAU * progress);
  context.strokeStyle = `rgba(250, 204, 21, ${coreBuild})`;
  context.lineWidth = 2;
  context.shadowColor = PALETTE.gold;
  context.shadowBlur = 12;
  context.stroke();
  context.shadowBlur = 0;

  const bloom = context.createRadialGradient(coreX, coreY, 0, coreX, coreY, coreRadius);
  bloom.addColorStop(0, `rgba(250, 204, 21, ${0.16 + coreBuild * 0.6})`);
  bloom.addColorStop(0.18, `rgba(254, 240, 138, ${coreBuild * 0.46})`);
  bloom.addColorStop(0.52, `rgba(168, 85, 247, ${coreBuild * 0.25})`);
  bloom.addColorStop(1, "rgba(13, 16, 34, 0)");
  context.fillStyle = bloom;
  context.beginPath();
  context.arc(coreX, coreY, coreRadius, 0, TAU);
  context.fill();

  context.save();
  context.globalCompositeOperation = "lighter";
  for (const [offset, color] of [[-3, `rgba(250, 204, 21, ${coreBuild * 0.1})`], [3, `rgba(168, 85, 247, ${coreBuild * 0.12})`]] as const) {
    context.beginPath();
    context.arc(coreX + offset, coreY, 64 * cameraScale * corePulse, 0, TAU);
    context.fillStyle = color;
    context.fill();
  }
  const streak = context.createLinearGradient(coreX - coreRadius * 1.25, coreY, coreX + coreRadius * 1.25, coreY);
  streak.addColorStop(0, "rgba(124, 58, 237, 0)");
  streak.addColorStop(0.5, `rgba(250, 204, 21, ${(0.12 + coreBuild * 0.22) + burstStrength * 0.3})`);
  streak.addColorStop(1, "rgba(168, 85, 247, 0)");
  context.fillStyle = streak;
  context.fillRect(coreX - coreRadius * 1.25, coreY - 1.5, coreRadius * 2.5, 3);
  context.restore();

  for (let ring = 0; ring < 3; ring += 1) {
    const phase = (elapsed * 0.00035 + ring / 3) % 1;
    context.beginPath();
    context.arc(coreX, coreY, (16 + phase * coreRadius) * corePulse, 0, TAU);
    context.strokeStyle = ring === 0
      ? `rgba(254, 240, 138, ${0.32 * (1 - phase) * coreBuild})`
      : `rgba(168, 85, 247, ${0.24 * (1 - phase) * coreBuild})`;
    context.lineWidth = 1;
    context.stroke();
  }
  context.beginPath();
  context.arc(coreX, coreY, 9 * cameraScale + 1, 0, TAU);
  context.fillStyle = PALETTE.gold;
  context.shadowColor = PALETTE.gold;
  context.shadowBlur = frame.consensus ? 36 : 28;
  context.fill();
  context.shadowBlur = 0;

  // 7. Automatic gold shockwaves with violet secondary refraction require backend evidence.
  for (const wave of [
    primaryWave && { ...primaryWave, color: PALETTE.gold, blur: 28 },
    secondaryWave && { ...secondaryWave, color: PALETTE.violet, blur: 22 },
  ]) {
    if (!wave || wave.alpha <= 0) continue;
    context.beginPath();
    context.arc(coreX, coreY, wave.radius, 0, TAU);
    context.strokeStyle = wave.color === PALETTE.gold
      ? `rgba(250, 204, 21, ${wave.alpha * 0.78})`
      : `rgba(168, 85, 247, ${wave.alpha * 0.76})`;
    context.lineWidth = 2.3;
    context.shadowColor = wave.color;
    context.shadowBlur = wave.blur;
    context.stroke();
    context.shadowBlur = 0;
  }

  // Fullscreen radial gold flash; no UI button can trigger this effect.
  if (frame.consensus && consensusAge >= 0 && consensusAge < 850) {
    const alpha = 0.34 * (1 - consensusAge / 850);
    const flash = context.createRadialGradient(coreX, coreY, 0, coreX, coreY, scale * 0.72);
    flash.addColorStop(0, `rgba(250, 204, 21, ${alpha})`);
    flash.addColorStop(0.4, `rgba(250, 204, 21, ${alpha * 0.28})`);
    flash.addColorStop(1, "rgba(250, 204, 21, 0)");
    context.fillStyle = flash;
    context.fillRect(0, 0, width, height);
  }

  // 8. Bank nodes catch the wave, flare gold, drift, then settle into a quiet hum.
  // 9. A phase-locked recall web appears only after the verified consensus event.
  if (frame.consensus && consensusAge >= 0 && recallRecords.length > 2) {
    const points = recallRecords.map((memory) => scenePoints.get(memory.id)).filter((point): point is ScenePoint => Boolean(point));
    if (points.length > 2) {
      context.beginPath();
      context.moveTo(points[0]!.x, points[0]!.y);
      for (let index = 1; index < points.length; index += 1) context.lineTo(points[index]!.x, points[index]!.y);
      context.closePath();
      context.fillStyle = `rgba(250, 204, 21, ${0.035 + Math.sin(elapsed * 0.0018) * 0.012})`;
      context.fill();
      context.strokeStyle = "rgba(250, 204, 21, 0.82)";
      context.lineWidth = 1.1;
      context.shadowColor = PALETTE.gold;
      context.shadowBlur = 16;
      context.stroke();
      context.shadowBlur = 0;
      const tieCount = Math.min(12, Math.floor(points.length / 2));
      for (let tie = 0; tie < tieCount; tie += 1) {
        const fromIndex = Math.floor(tie * points.length / tieCount);
        const from = points[fromIndex];
        const to = points[(fromIndex + Math.floor(points.length / 2)) % points.length];
        if (!from || !to) continue;
        context.beginPath();
        context.moveTo(from.x, from.y);
        context.lineTo(to.x, to.y);
        context.strokeStyle = "rgba(168, 85, 247, 0.18)";
        context.lineWidth = 0.7;
        context.stroke();
      }
    }
  }

  return scenePoints;
}
