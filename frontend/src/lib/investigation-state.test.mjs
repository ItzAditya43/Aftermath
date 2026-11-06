import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAnalysisEvent,
  beginInvestigation,
  failInvestigation,
  getIncidentListAvailability,
  hasConsensusEvidence,
  hasSavedConsensusEvidence,
} from "./investigation-state.ts";
import { ApiError, getIncidents, isApiOfflineError } from "./api-client.ts";

const incident = { id: "INC-CURRENT" };

function completeState(confidenceCalibrated) {
  let state = beginInvestigation();
  state = applyAnalysisEvent(state, {
    type: "memory",
    past_incident_id: "INC-OLD-1",
    title: "First match",
    service: "payments-api",
    summary: "A similar failure",
    relevance_score: 0.9,
    root_cause: null,
    resolution: null,
    failed_attempts: [],
    time_to_resolve_minutes: null,
  });
  state = applyAnalysisEvent(state, {
    type: "memory",
    past_incident_id: "INC-OLD-2",
    title: "Second match",
    service: "payments-api",
    summary: "Another similar failure",
    relevance_score: 0.8,
    root_cause: null,
    resolution: null,
    failed_attempts: [],
    time_to_resolve_minutes: null,
  });
  state = applyAnalysisEvent(state, { type: "token", text: "Prior investigation narrative." });
  state = applyAnalysisEvent(state, {
    type: "suggestion",
    likely_root_cause: "Connection pool exhaustion",
    steps: ["Check pool metrics"],
    warnings: [],
    confidence: 0.9,
    confidence_calibrated: confidenceCalibrated,
    based_on: ["INC-OLD-1", "INC-OLD-2"],
  });
  return applyAnalysisEvent(state, {
    type: "done",
    incident_id: "INC-CURRENT",
    memories_used: 2,
  });
}

test("only calibrated confidence can establish consensus", () => {
  assert.equal(hasConsensusEvidence(completeState(false), incident), false);
  assert.equal(hasConsensusEvidence(completeState(true), incident), true);
});

test("saved consensus requires calibrated confidence and two recalled IDs", () => {
  const memories = completeState(true).memories;
  const suggestion = {
    likely_root_cause: "Connection pool exhaustion",
    steps: [],
    warnings: [],
    confidence: 0.8,
    confidence_calibrated: true,
    based_on: ["INC-OLD-1", "INC-OLD-2"],
  };

  assert.equal(hasSavedConsensusEvidence(suggestion, memories), true);
  assert.equal(hasSavedConsensusEvidence({ ...suggestion, confidence: 0.79 }, memories), false);
  assert.equal(hasSavedConsensusEvidence({ ...suggestion, confidence: 0.95, confidence_calibrated: false }, memories), false);
  assert.equal(hasSavedConsensusEvidence({ ...suggestion, based_on: ["INC-OLD-1", "INC-MISSING"] }, memories), false);
  assert.equal(hasSavedConsensusEvidence({ ...suggestion, based_on: ["INC-MISSING-1", "INC-MISSING-2"] }, memories), false);
});

test("offline API failures are distinct from empty and HTTP-error incident lists", () => {
  assert.equal(isApiOfflineError(new ApiError("offline", null, null, true)), true);
  assert.equal(isApiOfflineError(new ApiError("server error", 503, "unavailable")), false);
  assert.equal(getIncidentListAvailability(false, "", true, 0), "offline");
  assert.equal(getIncidentListAvailability(false, null, false, 0), "empty");
  assert.equal(getIncidentListAvailability(false, "request failed", false, 0), "error");
});

test("a failed network request is classified as API offline", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  try {
    await assert.rejects(getIncidents(), (error) => isApiOfflineError(error));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("failed re-analysis preserves the previous successful result", () => {
  const previous = completeState(true);
  let partial = beginInvestigation();
  partial = applyAnalysisEvent(partial, { type: "token", text: "Partial replacement narrative." });

  const failed = failInvestigation(partial, "The stream was interrupted.", previous);

  assert.equal(failed.status, "error");
  assert.equal(failed.error, "The stream was interrupted.");
  assert.equal(failed.reasoning, previous.reasoning);
  assert.deepEqual(failed.memories, previous.memories);
  assert.deepEqual(failed.suggestion, previous.suggestion);
  assert.deepEqual(failed.done, previous.done);
  assert.equal(failed.consensus, false);
});
