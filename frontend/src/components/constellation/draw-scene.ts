import type { SceneCamera, SceneMemory, SceneNode, ScenePoint } from "@/lib/memory-types";
import {
  getCameraScale,
  getCorePoint,
  getOrbitalRadii,
  getScenePositions,
  normalizeAngleDiff,
} from "./scene-layout";

const PALETTE = {
  black: "#000000",
  navy: "#0E0E10",
  violet: "#5F5F68",
  cyan: "#FFBF57",
  white: "#F5F5F7",
  lily: "#E0193F",
};
const TAU = Math.PI * 2;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const easeOut = (value: number) => 1 - (1 - clamp(value, 0, 1)) ** 3;
const RECALL_TRAVEL_MS = 1050;

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
  reducedMotion?: boolean;
  angleTracking?: Map<string, { currentAngle: number; targetAngle: number; changeTime: number }>;
};

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
  const recallRecords = memories
    .filter((memory) => memory.recalled)
    .sort((a, b) => {
      const timeA = frame.recallStartTimes.get(a.id) ?? -1000;
      const timeB = frame.recallStartTimes.get(b.id) ?? -1000;
      if (timeA !== timeB) return timeA - timeB;
      const relA = a.relevance ?? 0;
      const relB = b.relevance ?? 0;
      if (Math.abs(relB - relA) > 0.001) return relB - relA;
      return a.id.localeCompare(b.id);
    });
  const scenePoints = new Map(basePositions);
  const arrival = new Map<string, { start: ScenePoint; target: ScenePoint; progress: number; landedAt: number; control: ScenePoint }>();

  recallRecords.forEach((memory, index) => {
    const origin = basePositions.get(memory.id);
    const node = nodeById.get(memory.id);
    if (!origin || !node) return;
    const startAt = frame.recallStartTimes.get(memory.id) ?? -1000;
    const progress = frame.reducedMotion ? 1 : easeOut((elapsed - startAt) / RECALL_TRAVEL_MS);

    const total = recallRecords.length;
    let baseAngle: number;
    if (total <= 1) {
      baseAngle = -Math.PI / 2;
    } else if (total === 2) {
      baseAngle = index === 0 ? -Math.PI / 2 - Math.PI / 3 : -Math.PI / 2 + Math.PI / 3;
    } else {
      baseAngle = (index / total) * TAU - Math.PI / 2;
    }

    let smoothBaseAngle = baseAngle;
    if (frame.angleTracking && !frame.reducedMotion) {
      const tracking = frame.angleTracking.get(memory.id);
      if (!tracking) {
        frame.angleTracking.set(memory.id, { currentAngle: baseAngle, targetAngle: baseAngle, changeTime: elapsed });
      } else {
        const prevT = clamp((elapsed - tracking.changeTime) / 420, 0, 1);
        const currentInterp = tracking.currentAngle + normalizeAngleDiff(tracking.targetAngle, tracking.currentAngle) * easeOut(prevT);
        if (Math.abs(normalizeAngleDiff(baseAngle, tracking.targetAngle)) > 0.001) {
          tracking.currentAngle = currentInterp;
          tracking.targetAngle = baseAngle;
          tracking.changeTime = elapsed;
        }
        const t = clamp((elapsed - tracking.changeTime) / 420, 0, 1);
        smoothBaseAngle = tracking.currentAngle + normalizeAngleDiff(tracking.targetAngle, tracking.currentAngle) * easeOut(t);
      }
    }

    const formationRadius = (165 + Math.min(total, 8) * 4) * cameraScale;
    const finalAngle = smoothBaseAngle + camera.yaw * 0.15;
    const satellite = {
      x: center.x + Math.cos(finalAngle) * formationRadius,
      y: center.y + Math.sin(finalAngle) * formationRadius * 0.74,
    };
    const formation = nodeFormation.get(memory.id) ?? 1;
    const target = { x: center.x + (satellite.x - center.x) * formation, y: center.y + (satellite.y - center.y) * formation };
    const control = curveControl(origin, target, node.angle, cameraScale);
    const point = quadraticPoint(origin, control, target, progress);
    scenePoints.set(memory.id, point);
    arrival.set(memory.id, { start: origin, target, progress, landedAt: startAt + RECALL_TRAVEL_MS, control });
  });

  context.clearRect(0, 0, width, height);
  context.fillStyle = PALETTE.black;
  context.fillRect(0, 0, width, height);

  // 1. Deep atmosphere and volumetric violet light.
  const atmosphere = context.createRadialGradient(center.x, center.y, 0, center.x, center.y, scale * 0.7);
  const coreBuild = easeOut((introProgress - 0.025) / 0.48);
  atmosphere.addColorStop(0, `rgba(224, 25, 63, ${0.025 + coreBuild * 0.095})`);
  atmosphere.addColorStop(0.34, `rgba(120, 120, 130, ${0.018 + coreBuild * 0.072})`);
  atmosphere.addColorStop(0.72, `rgba(14, 14, 16, ${0.008 + coreBuild * 0.027})`);
  atmosphere.addColorStop(1, "rgba(0, 0, 0, 0)");
  context.fillStyle = atmosphere;
  context.fillRect(0, 0, width, height);

  // 2. Parallax coordinate grid and deterministic depth stardust.
  const grid = 58;
  const offsetX = ((center.x + camera.yaw * 30) % grid + grid) % grid;
  const offsetY = ((center.y + camera.pitch * 24) % grid + grid) % grid;
  context.fillStyle = "rgba(120, 120, 130, 0.055)";
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
    context.fillStyle = `rgba(120, 120, 130, ${twinkle})`;
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
      context.fillStyle = `rgba(245, 245, 247, ${alpha})`;
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
      ? `rgba(255, 191, 87, ${0.36 * ringBuild})`
      : `rgba(224, 25, 63, ${(0.08 + (1 - index) * 0.035) * ringBuild})`;
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
        ? `rgba(255, 191, 87, ${alpha + waveGlow * 0.44})`
      : `rgba(120, 120, 130, ${alpha * (0.68 + breathe * 0.32)})`;
    context.shadowColor = memory.recalled || waveGlow > 0.15 ? PALETTE.cyan : PALETTE.violet;
    context.shadowBlur = memory.recalled ? 12 : waveGlow * 16;
    context.fill();
    context.shadowBlur = 0;
  }

  if (frame.selectedId) {
    const selectedPoint = scenePoints.get(frame.selectedId);
    if (selectedPoint) {
      context.beginPath();
      context.moveTo(center.x, center.y);
      context.lineTo(selectedPoint.x, selectedPoint.y);
      const focusLink = context.createLinearGradient(center.x, center.y, selectedPoint.x, selectedPoint.y);
      focusLink.addColorStop(0, "rgba(224, 25, 63, 0.1)");
      focusLink.addColorStop(1, "rgba(255, 191, 87, 0.78)");
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

    // Initial detachment pulse from origin orbit when recall begins
    const recallAge = elapsed - startAt;
    if (migrating && recallAge >= 0 && recallAge < 550) {
      const depProg = clamp(recallAge / 550, 0, 1);
      const depRadius = 4 + depProg * 22;
      const depAlpha = (1 - depProg) * 0.75;
      context.beginPath();
      context.arc(motion.start.x, motion.start.y, depRadius, 0, TAU);
      context.strokeStyle = `rgba(255, 191, 87, ${depAlpha})`;
      context.lineWidth = 1.3 * (1 - depProg);
      context.stroke();
    }

    if (migrating) {
      // Radiant migration flight path connecting origin orbit to traveling star
      context.beginPath();
      context.moveTo(motion.start.x, motion.start.y);
      const flightControl = curveControl(motion.start, point, node.angle, cameraScale);
      context.quadraticCurveTo(flightControl.x, flightControl.y, point.x, point.y);
      const flightGradient = context.createLinearGradient(motion.start.x, motion.start.y, point.x, point.y);
      flightGradient.addColorStop(0, "rgba(120, 120, 130, 0.25)");
      flightGradient.addColorStop(0.55, "rgba(120, 120, 130, 0.7)");
      flightGradient.addColorStop(1, "rgba(255, 191, 87, 0.95)");
      context.strokeStyle = flightGradient;
      context.lineWidth = 1.6;
      context.shadowColor = PALETTE.cyan;
      context.shadowBlur = 10;
      context.stroke();
      context.shadowBlur = 0;
    } else {
      // Settled quadratic link between intelligence core and settled star
      context.beginPath();
      context.moveTo(center.x, center.y);
      context.quadraticCurveTo(control.x, control.y, motion.target.x, motion.target.y);
      const filament = context.createLinearGradient(center.x, center.y, endpoint.x, endpoint.y);
      filament.addColorStop(0, "rgba(224, 25, 63, 0.94)");
      filament.addColorStop(0.55, "rgba(120, 120, 130, 0.92)");
      filament.addColorStop(1, "rgba(255, 191, 87, 0.98)");
      context.strokeStyle = filament;
      context.lineWidth = 0.8;
      context.shadowColor = PALETTE.cyan;
      context.shadowBlur = 4;
      context.stroke();
      context.shadowBlur = 0;
    }

    // A luminous comet trail and traveling star nucleus along the migration path
    if (migrating) {
      const particleCount = 32;
      for (let particle = 0; particle < particleCount; particle += 1) {
        const trailT = motion.progress - particle * 0.0075;
        if (trailT <= 0) continue;
        const vapor = quadraticPoint(motion.start, motion.control, motion.target, trailT);
        const pFrac = particle / particleCount;
        const alpha = (1 - pFrac) * 0.42;
        context.beginPath();
        context.arc(vapor.x, vapor.y, 0.8 + (1 - pFrac) * 1.5, 0, TAU);
        context.fillStyle = pFrac < 0.28 ? `rgba(255, 220, 160, ${alpha})` : `rgba(120, 120, 130, ${alpha})`;
        context.fill();
      }
      context.beginPath();
      context.arc(point.x, point.y, 4.6, 0, TAU);
      context.fillStyle = PALETTE.white;
      context.shadowColor = PALETTE.cyan;
      context.shadowBlur = 22;
      context.fill();
      context.shadowBlur = 0;
    }

    if (flare > 0) {
      context.beginPath();
      context.arc(point.x, point.y, 6 + (1 - flare) * 26, 0, TAU);
      context.strokeStyle = `rgba(255, 191, 87, ${flare * 0.85})`;
      context.lineWidth = 1.2 + flare * 1.2;
      context.shadowColor = PALETTE.cyan;
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
        context.fillStyle = PALETTE.cyan;
        context.shadowColor = PALETTE.cyan;
        context.shadowBlur = 12;
        context.fill();
        context.shadowBlur = 0;
      }
    }

    const hovered = frame.hoveredId === memory.id;
    const selected = frame.selectedId === memory.id;
    const haloRadius = hovered || selected ? 26 : 17 + Math.sin(elapsed * 0.002 + index) * 3;
    const halo = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, haloRadius);
    halo.addColorStop(0, "rgba(255, 191, 87, 0.38)");
    halo.addColorStop(1, "rgba(255, 191, 87, 0)");
    context.fillStyle = halo;
    context.beginPath();
    context.arc(point.x, point.y, haloRadius, 0, TAU);
    context.fill();
    context.beginPath();
    context.arc(point.x, point.y, 5 + (selected ? 1.5 : 0), 0, TAU);
    context.fillStyle = PALETTE.cyan;
    context.shadowColor = PALETTE.cyan;
    context.shadowBlur = 18;
    context.fill();
    context.shadowBlur = 0;
    if (hovered || selected) {
      context.beginPath();
      context.arc(point.x, point.y, 12, 0, TAU);
      context.strokeStyle = PALETTE.cyan;
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
    context.fillStyle = memory.severity === "SEV1" ? `rgba(205, 205, 211, ${0.9 * reveal})` : `rgba(120, 120, 130, ${0.86 * reveal})`;
    context.shadowColor = PALETTE.violet;
    context.shadowBlur = (hovered || selected ? 14 : 4) * reveal;
    context.fill();
    context.shadowBlur = 0;
    if (hovered || selected) {
      context.beginPath();
      context.arc(point.x, point.y, radius + 6, 0, TAU);
      context.strokeStyle = selected ? PALETTE.white : "rgba(160, 160, 170, 0.9)";
      context.lineWidth = selected ? 1.6 : 1;
      context.stroke();
    }
  }

  // Keep recalled incidents as individual nodes. Their only shown links are the recall paths
  // from the active incident; no peer relationships are implied by geometric placement.
  const corePulse = 1 + Math.sin(elapsed * 0.0032) * 0.04;
  const coreRadius = 24 * cameraScale * corePulse * (0.35 + coreBuild * 0.65);
  context.beginPath();
  context.arc(center.x, center.y, coreRadius, 0, TAU);
  context.strokeStyle = `rgba(224, 25, 63, ${0.2 + coreBuild * 0.4})`;
  context.lineWidth = 1.2;
  context.shadowColor = PALETTE.lily;
  context.shadowBlur = 10 * coreBuild;
  context.stroke();
  context.shadowBlur = 0;

  context.beginPath();
  context.arc(center.x, center.y, 4 + coreBuild * 2, 0, TAU);
  context.fillStyle = PALETTE.lily;
  context.shadowColor = PALETTE.lily;
  context.shadowBlur = 12 * coreBuild;
  context.fill();
  context.shadowBlur = 0;

  return scenePoints;
}
