import assert from "node:assert/strict";
import test from "node:test";

import {
  createSceneLayout,
  getConstellationEdges,
  getCorePoint,
  getCameraScale,
  getSatellitePoint,
  normalizeAngleDiff,
  calculateConstellationTiming,
  isCloserEligibleHit,
} from "./scene-layout.ts";

function pickClosestHit(candidates) {
  let selected = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const candidate of candidates) {
    if (isCloserEligibleHit(candidate.distance, candidate.hitRadius, bestDistance)) {
      selected = candidate.id;
      bestDistance = candidate.distance;
    }
  }
  return selected;
}

test("nearer ambient node stays selected when a farther recalled node is also in range", () => {
  assert.equal(pickClosestHit([
    { id: "current", distance: 10, hitRadius: 22 },
    { id: "recalled", distance: 20, hitRadius: 26 },
  ]), "current");
});

test("recalled node remains selectable within its 26px hit radius", () => {
  assert.equal(pickClosestHit([
    { id: "recalled", distance: 20, hitRadius: 26 },
  ]), "recalled");
});

test("getConstellationEdges handles 0 and 1 matches gracefully", () => {
  assert.deepEqual(getConstellationEdges(0), []);
  assert.deepEqual(getConstellationEdges(1), []);
});

test("getConstellationEdges creates a single progressive connecting edge for 2 matches", () => {
  const edges = getConstellationEdges(2);
  assert.equal(edges.length, 1);
  assert.deepEqual(edges[0], { from: 0, to: 1, isPerimeter: true });
});

test("getConstellationEdges creates a closed 3-edge triangular constellation for 3 matches", () => {
  const edges = getConstellationEdges(3);
  assert.equal(edges.length, 3);
  assert.deepEqual(edges, [
    { from: 0, to: 1, isPerimeter: true },
    { from: 1, to: 2, isPerimeter: true },
    { from: 2, to: 0, isPerimeter: true },
  ]);
  // Edges form a single continuous cycle
  assert.equal(edges[0].to, edges[1].from);
  assert.equal(edges[1].to, edges[2].from);
  assert.equal(edges[2].to, edges[0].from);
});

test("getConstellationEdges creates a 4-vertex geometric constellation for 4 matches", () => {
  const edges = getConstellationEdges(4);
  const perimeter = edges.filter((e) => e.isPerimeter);
  const internal = edges.filter((e) => !e.isPerimeter);

  assert.equal(perimeter.length, 4);
  assert.deepEqual(perimeter, [
    { from: 0, to: 1, isPerimeter: true },
    { from: 1, to: 2, isPerimeter: true },
    { from: 2, to: 3, isPerimeter: true },
    { from: 3, to: 0, isPerimeter: true },
  ]);
  assert.equal(internal.length, 2);
  assert.deepEqual(internal, [
    { from: 0, to: 2, isPerimeter: false },
    { from: 1, to: 3, isPerimeter: false },
  ]);
});

test("getConstellationEdges creates polygonal edges and chords for 5 or more matches", () => {
  for (const count of [5, 6, 7, 8]) {
    const edges = getConstellationEdges(count);
    const perimeter = edges.filter((e) => e.isPerimeter);
    const chords = edges.filter((e) => !e.isPerimeter);

    assert.equal(perimeter.length, count);
    assert.equal(chords.length, count);
    // Perimeter forms a closed Hamiltonian cycle
    for (let i = 0; i < count; i += 1) {
      assert.equal(perimeter[i].from, i);
      assert.equal(perimeter[i].to, (i + 1) % count);
    }
  }
});

test("getSatellitePoint positions 3 matches into an upright, symmetrical triangle around core", () => {
  const width = 1400;
  const height = 900;
  const camera = { yaw: 0, pitch: 0 };
  const core = getCorePoint(width, height);

  const p0 = getSatellitePoint(0, 3, 0.9, width, height, camera);
  const p1 = getSatellitePoint(1, 3, 0.8, width, height, camera);
  const p2 = getSatellitePoint(2, 3, 0.7, width, height, camera);

  // Apex (p0) is directly above core horizontally
  assert.ok(Math.abs(p0.x - core.x) < 0.001);
  assert.ok(p0.y < core.y);

  // Bottom vertices (p1, p2) are horizontally symmetrical about core.x
  const distP1X = p1.x - core.x;
  const distP2X = core.x - p2.x;
  assert.ok(Math.abs(distP1X - distP2X) < 0.001, "Triangle should be symmetrical about x axis");

  // Bottom vertices share the exact same y level
  assert.ok(Math.abs(p1.y - p2.y) < 0.001, "Bottom vertices should be level");
  assert.ok(p1.y > core.y, "Bottom vertices should be below core");

  // Distance from p0 to p1 equals distance from p0 to p2 (isosceles/equilateral in tilted view)
  const dist01 = Math.hypot(p0.x - p1.x, p0.y - p1.y);
  const dist02 = Math.hypot(p0.x - p2.x, p0.y - p2.y);
  assert.ok(Math.abs(dist01 - dist02) < 0.001);
});

test("getSatellitePoint positions 4 matches into a balanced diamond/quadrilateral around core", () => {
  const width = 1400;
  const height = 900;
  const camera = { yaw: 0, pitch: 0 };
  const core = getCorePoint(width, height);

  const p0 = getSatellitePoint(0, 4, 0.9, width, height, camera);
  const p1 = getSatellitePoint(1, 4, 0.8, width, height, camera);
  const p2 = getSatellitePoint(2, 4, 0.7, width, height, camera);
  const p3 = getSatellitePoint(3, 4, 0.6, width, height, camera);

  // Vertical axis: p0 is top, p2 is bottom
  assert.ok(Math.abs(p0.x - core.x) < 0.001);
  assert.ok(Math.abs(p2.x - core.x) < 0.001);
  assert.ok(p0.y < core.y);
  assert.ok(p2.y > core.y);

  // Horizontal axis: p1 is right, p3 is left
  assert.ok(p1.x > core.x);
  assert.ok(p3.x < core.x);
  assert.ok(Math.abs(p1.y - core.y) < 0.001);
  assert.ok(Math.abs(p3.y - core.y) < 0.001);
});

test("getSatellitePoint avoids overlapping nodes and scales gracefully for 5+ matches", () => {
  const width = 1400;
  const height = 900;
  const camera = { yaw: 0, pitch: 0 };
  const core = getCorePoint(width, height);
  const scale = getCameraScale(width, height);

  for (const total of [5, 6, 8, 12]) {
    const points = [];
    for (let i = 0; i < total; i += 1) {
      points.push(getSatellitePoint(i, total, 0.5, width, height, camera));
    }

    // Verify minimum distance between any two adjacent nodes
    for (let i = 0; i < total; i += 1) {
      const pA = points[i];
      const pB = points[(i + 1) % total];
      const distance = Math.hypot(pA.x - pB.x, pA.y - pB.y);
      assert.ok(distance > 30 * scale, `Nodes ${i} and ${(i + 1) % total} must not overlap (distance: ${distance})`);
    }

    // Verify all points remain outside the core radius (~112 * scale)
    for (let i = 0; i < total; i += 1) {
      const distanceToCore = Math.hypot(points[i].x - core.x, (points[i].y - core.y) / 0.74);
      assert.ok(distanceToCore >= 150 * scale, "Node must remain outside core radius");
    }
  }
});

test("createSceneLayout deterministically maps incidents by ID", () => {
  const memories = [
    { id: "INC-1001", recalled: false },
    { id: "INC-1002", recalled: false },
    { id: "INC-1003", recalled: true },
  ];
  const layout1 = createSceneLayout(memories);
  const layout2 = createSceneLayout(memories);

  assert.equal(layout1.length, 3);
  assert.equal(layout1[0].memoryId, "INC-1001");
  assert.equal(layout1[1].memoryId, "INC-1002");
  assert.equal(layout1[2].memoryId, "INC-1003");

  // Re-running layout with same IDs produces identical values
  assert.deepEqual(layout1, layout2);
});

test("normalizeAngleDiff returns the shortest signed arc across zero and pi boundaries", () => {
  // Standard positive quadrant
  assert.ok(Math.abs(normalizeAngleDiff(Math.PI / 2, 0) - Math.PI / 2) < 1e-6);

  // Crosses the +/- PI boundary: shortest way from +0.9 PI to -0.9 PI is +0.2 PI, not -1.8 PI
  const diffCrossPositive = normalizeAngleDiff(-0.9 * Math.PI, 0.9 * Math.PI);
  assert.ok(Math.abs(diffCrossPositive - 0.2 * Math.PI) < 1e-6);

  // Shortest way from -0.9 PI to +0.9 PI is -0.2 PI, not +1.8 PI
  const diffCrossNegative = normalizeAngleDiff(0.9 * Math.PI, -0.9 * Math.PI);
  assert.ok(Math.abs(diffCrossNegative - (-0.2 * Math.PI)) < 1e-6);

  // Full turn is zero
  assert.ok(Math.abs(normalizeAngleDiff(Math.PI * 2, 0)) < 1e-6);
  assert.ok(Math.abs(normalizeAngleDiff(0, Math.PI * 2)) < 1e-6);
});

test("interrupted angle transition calculates current interpolated angle first to avoid snapping", () => {
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
  const easeOut = (v) => 1 - (1 - clamp(v, 0, 1)) ** 3;

  // Simulate angle tracking state machine from draw-scene.ts
  const initialBaseAngle = -Math.PI / 2; // -90 deg
  const tracking = {
    currentAngle: initialBaseAngle,
    targetAngle: initialBaseAngle,
    changeTime: 0,
  };

  // 1. Move to a 2nd target angle at elapsed = 0
  const target2 = -Math.PI / 6; // -30 deg (+60 deg shift)
  if (Math.abs(normalizeAngleDiff(target2, tracking.targetAngle)) > 0.001) {
    const prevT = clamp((0 - tracking.changeTime) / 420, 0, 1);
    const currentInterp = tracking.currentAngle + normalizeAngleDiff(tracking.targetAngle, tracking.currentAngle) * easeOut(prevT);
    tracking.currentAngle = currentInterp;
    tracking.targetAngle = target2;
    tracking.changeTime = 0;
  }

  // 2. Midway through transition (e.g. elapsed = 210ms out of 420ms):
  const midElapsed = 210;
  const progressMid = clamp((midElapsed - tracking.changeTime) / 420, 0, 1);
  const angleBeforeInterruption = tracking.currentAngle + normalizeAngleDiff(tracking.targetAngle, tracking.currentAngle) * easeOut(progressMid);

  // Verify the star is strictly between start and target
  assert.ok(angleBeforeInterruption > initialBaseAngle);
  assert.ok(angleBeforeInterruption < target2);

  // 3. New memory arrives via SSE at elapsed = 210ms, changing base angle to target3
  const target3 = Math.PI / 6; // +30 deg
  const prevT = clamp((midElapsed - tracking.changeTime) / 420, 0, 1);
  const currentInterp = tracking.currentAngle + normalizeAngleDiff(tracking.targetAngle, tracking.currentAngle) * easeOut(prevT);
  assert.ok(Math.abs(currentInterp - angleBeforeInterruption) < 1e-9);

  if (Math.abs(normalizeAngleDiff(target3, tracking.targetAngle)) > 0.001) {
    tracking.currentAngle = currentInterp;
    tracking.targetAngle = target3;
    tracking.changeTime = midElapsed;
  }

  // 4. Verify position at the EXACT instant of arrival (elapsed = 210ms)
  const newT = clamp((midElapsed - tracking.changeTime) / 420, 0, 1);
  const angleAtInstantOfArrival = tracking.currentAngle + normalizeAngleDiff(tracking.targetAngle, tracking.currentAngle) * easeOut(newT);

  // Position at arrival must EXACTLY match the interpolated position right before arrival (0 jump)
  assert.ok(Math.abs(angleAtInstantOfArrival - angleBeforeInterruption) < 1e-9, "Positional snapping detected during interruption!");
  assert.equal(tracking.currentAngle, angleBeforeInterruption);
  assert.equal(tracking.targetAngle, target3);
  assert.equal(tracking.changeTime, midElapsed);
});

test("calculateConstellationTiming handles staggered memory arrivals without restarting earlier edges", () => {
  // Scenario: Memory 0 & Memory 1 arrive at t=0 (travel 1050ms -> land at 1050ms).
  // Memory 2 arrives later via SSE at t=600ms (lands at 600 + 1050 = 1650ms).
  const edges3 = getConstellationEdges(3);
  const landedTimesStaggered = [1050, 1050, 1650];

  const timing = calculateConstellationTiming(edges3, landedTimesStaggered, {
    perimeterEdgeDuration: 380,
  });

  assert.equal(timing.edges.length, 3);

  const edge0 = timing.edges[0]; // (0 -> 1)
  const edge1 = timing.edges[1]; // (1 -> 2)
  const edge2 = timing.edges[2]; // (2 -> 0)

  // Edge 0 between stars 0 and 1 starts as soon as stars 0 and 1 land (1050ms)
  assert.equal(edge0.startTime, 1050);
  assert.equal(edge0.endTime, 1430);

  // Edge 1 between star 1 and star 2 CANNOT start until star 2 lands at 1650ms, even though Edge 0 finished at 1430ms
  assert.equal(edge1.startTime, 1650);
  assert.equal(edge1.endTime, 2030);

  // Edge 2 between star 2 and star 0 starts after Edge 1 finishes
  assert.equal(edge2.startTime, 2030);
  assert.equal(edge2.endTime, 2410);

  // Formation completes strictly when Edge 2 finishes
  assert.equal(timing.perimeterCompleteAt, 2410);
  assert.equal(timing.formationCompleteAt, 2410);

  // Verify compare against 2-match state: Edge 0 timing is anchored and identical
  const edges2 = getConstellationEdges(2);
  const timing2 = calculateConstellationTiming(edges2, [1050, 1050], { perimeterEdgeDuration: 380 });
  assert.equal(timing2.edges[0].startTime, edge0.startTime);
  assert.equal(timing2.edges[0].endTime, edge0.endTime);
});

test("calculateConstellationTiming coordinates 4-or-more-memory sequence: perimeter in order, then chords, then completion", () => {
  const edges4 = getConstellationEdges(4);
  const landedSimultaneous = [1050, 1050, 1050, 1050];

  const timing = calculateConstellationTiming(edges4, landedSimultaneous, {
    perimeterEdgeDuration: 380,
    chordEdgeDuration: 280,
  });

  const perimeterEdges = timing.edges.filter((e) => e.isPerimeter);
  const chordEdges = timing.edges.filter((e) => !e.isPerimeter);

  assert.equal(perimeterEdges.length, 4);
  assert.equal(chordEdges.length, 2);

  // 1. Perimeter edges must draw in strict sequential order
  assert.equal(perimeterEdges[0].startTime, 1050);
  assert.equal(perimeterEdges[0].endTime, 1430);

  assert.equal(perimeterEdges[1].startTime, 1430);
  assert.equal(perimeterEdges[1].endTime, 1810);

  assert.equal(perimeterEdges[2].startTime, 1810);
  assert.equal(perimeterEdges[2].endTime, 2190);

  assert.equal(perimeterEdges[3].startTime, 2190);
  assert.equal(perimeterEdges[3].endTime, 2570);

  assert.equal(timing.perimeterCompleteAt, 2570);

  // 2. Chords must start strictly after perimeter is complete
  assert.ok(chordEdges[0].startTime >= timing.perimeterCompleteAt);
  assert.equal(chordEdges[0].startTime, 2570);
  assert.equal(chordEdges[0].endTime, 2850);

  // 3. Second chord draws after first chord
  assert.equal(chordEdges[1].startTime, 2850);
  assert.equal(chordEdges[1].endTime, 3130);

  // 4. Formation completion occurs strictly when the last chord completes
  assert.equal(timing.formationCompleteAt, 3130);
  assert.ok(timing.formationCompleteAt > timing.perimeterCompleteAt);
});

test("calculateConstellationTiming immediately completes all edges when reduced motion is requested", () => {
  const edges4 = getConstellationEdges(4);
  const timing = calculateConstellationTiming(edges4, [1050, 1050, 1050, 1050], {
    reducedMotion: true,
  });

  assert.equal(timing.perimeterCompleteAt, -10000);
  assert.equal(timing.formationCompleteAt, -10000);
  for (const edge of timing.edges) {
    assert.equal(edge.startTime, -10000);
    assert.equal(edge.endTime, -10000);
    assert.equal(edge.duration, 0);
  }
});

test("two-star state ensures both stars land before connecting edge begins drawing", () => {
  const edges2 = getConstellationEdges(2);
  assert.equal(edges2.length, 1);
  assert.equal(edges2[0].from, 0);
  assert.equal(edges2[0].to, 1);

  // Staggered star arrivals: star 0 lands at 1050ms, star 1 lands at 1270ms
  const landedTimes = [1050, 1270];
  const timing = calculateConstellationTiming(edges2, landedTimes, {
    perimeterEdgeDuration: 460,
  });

  assert.equal(timing.edges.length, 1);
  const edge = timing.edges[0];

  // Connecting edge start time must be at or after the landing time of BOTH stars
  assert.ok(edge.startTime >= landedTimes[0]);
  assert.ok(edge.startTime >= landedTimes[1]);
  assert.equal(edge.startTime, 1270);
  assert.equal(edge.endTime, 1270 + 460);
  assert.equal(timing.formationCompleteAt, edge.endTime);
});

test("single-star state schedules 0 geometric edges and does not trigger multi-star completion", () => {
  const edges1 = getConstellationEdges(1);
  assert.deepEqual(edges1, []);

  const landedTimes = [1050];
  const timing = calculateConstellationTiming(edges1, landedTimes);

  assert.equal(timing.edges.length, 0);
  assert.equal(timing.perimeterCompleteAt, 1050);
  assert.equal(timing.formationCompleteAt, 1050);
});

test("constellation ordering stability ensures existing stars do not change assigned positions as memories arrive", () => {
  // Comparator replicating draw-scene.ts stable ordering
  const stableOrder = (memories, recallStartTimes) =>
    [...memories]
      .filter((m) => m.recalled)
      .sort((a, b) => {
        const timeA = recallStartTimes.get(a.id) ?? -1000;
        const timeB = recallStartTimes.get(b.id) ?? -1000;
        if (timeA !== timeB) return timeA - timeB;
        const relA = a.relevance ?? 0;
        const relB = b.relevance ?? 0;
        if (Math.abs(relB - relA) > 0.001) return relB - relA;
        return a.id.localeCompare(b.id);
      });

  const recallStartTimes = new Map();

  // 1. First memory arrives at t=1000ms with moderate relevance (0.75)
  const memA = { id: "INC-1001", recalled: true, relevance: 0.75 };
  recallStartTimes.set(memA.id, 1000);

  const stage1 = stableOrder([memA], recallStartTimes);
  assert.equal(stage1.length, 1);
  assert.equal(stage1[0].id, "INC-1001");

  // 2. Second memory arrives at t=2000ms with lower relevance (0.60)
  const memB = { id: "INC-1002", recalled: true, relevance: 0.60 };
  recallStartTimes.set(memB.id, 2000);

  const stage2 = stableOrder([memA, memB], recallStartTimes);
  assert.equal(stage2.length, 2);
  assert.equal(stage2[0].id, "INC-1001", "Existing star A must remain at vertex 0");
  assert.equal(stage2[1].id, "INC-1002", "New star B is assigned vertex 1");

  // 3. Third memory arrives at t=3000ms with MUCH HIGHER relevance (0.98)
  const memC = { id: "INC-1003", recalled: true, relevance: 0.98 };
  recallStartTimes.set(memC.id, 3000);

  const stage3 = stableOrder([memA, memB, memC], recallStartTimes);
  assert.equal(stage3.length, 3);
  // High-relevance late arrival must NOT displace earlier established vertices
  assert.equal(stage3[0].id, "INC-1001", "Existing star A must still remain at vertex 0");
  assert.equal(stage3[1].id, "INC-1002", "Existing star B must still remain at vertex 1");
  assert.equal(stage3[2].id, "INC-1003", "New star C is appended at vertex 2");

  // 4. Edges generated for stage 2 and stage 3:
  // Edge 0 (from 0 to 1) was between INC-1001 and INC-1002 in stage 2,
  // and remains between INC-1001 and INC-1002 in stage 3!
  const edges3 = getConstellationEdges(3);
  assert.equal(stage3[edges3[0].from].id, "INC-1001");
  assert.equal(stage3[edges3[0].to].id, "INC-1002");
});
