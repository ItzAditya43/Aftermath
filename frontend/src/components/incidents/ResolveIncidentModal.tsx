"use client";

import React, { FormEvent, useState } from "react";
import { AlertCircle, Check, LoaderCircle, X } from "lucide-react";
import { resolveIncident } from "@/lib/api-client";
import type { ResolveIncidentRequest, ResolveResponse, Suggestion } from "@/lib/memory-types";

interface ResolveIncidentModalProps {
  isOpen: boolean;
  incidentId: string;
  incidentTitle: string;
  suggestion?: Suggestion | null;
  onClose: () => void;
  onResolved: (response: ResolveResponse) => void;
}

export default function ResolveIncidentModal({
  isOpen,
  incidentId,
  incidentTitle,
  suggestion,
  onClose,
  onResolved,
}: ResolveIncidentModalProps) {
  const [rootCause, setRootCause] = useState(suggestion?.likely_root_cause ?? "");
  const [fixWorked, setFixWorked] = useState("");
  const [whatFailed, setWhatFailed] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const payload: ResolveIncidentRequest = {
      root_cause: rootCause.trim(),
      fix_that_worked: fixWorked.trim(),
      what_failed: whatFailed
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      notes: notes.trim() || null,
    };

    try {
      const result = await resolveIncident(incidentId, payload);
      onResolved(result);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to record resolution."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="resolve-modal-title"
      className="modal-backdrop"
    >
      <div
        className="modal-card focus:outline-none"
        tabIndex={-1}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line pb-4">
          <div>
            <h2 id="resolve-modal-title" className="display text-[28px] leading-tight">
              Record resolution · {incidentId}
            </h2>
            <p className="mt-1 line-clamp-1 text-[13px] text-ink-3">
              {incidentTitle}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-quiet btn-sm btn-icon border-line"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="callout-error mt-4 flex items-center gap-2"
          >
            <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label
              htmlFor="resolve-root-cause"
              className="field-label"
            >
              Confirmed root cause
            </label>
            <textarea
              id="resolve-root-cause"
              name="root_cause"
              required
              rows={2}
              value={rootCause}
              onChange={(e) => setRootCause(e.target.value)}
              className="field text-[13.5px]"
              placeholder="What specifically triggered this outage or degradation?"
            />
          </div>

          <div>
            <label
              htmlFor="resolve-fix"
              className="field-label"
            >
              Fix that worked
            </label>
            <textarea
              id="resolve-fix"
              name="fix_that_worked"
              required
              rows={2}
              value={fixWorked}
              onChange={(e) => setFixWorked(e.target.value)}
              className="field text-[13.5px]"
              placeholder="Exact fix or rollback applied to restore normal service operations..."
            />
          </div>

          <div>
            <label
              htmlFor="resolve-failed"
              className="field-label"
            >
              What failed (one attempt per line)
            </label>
            <textarea
              id="resolve-failed"
              name="what_failed"
              rows={2}
              value={whatFailed}
              onChange={(e) => setWhatFailed(e.target.value)}
              className="field text-[13.5px]"
              placeholder="e.g. Restarted pods without config rollback&#10;Increased memory limit to 4GB"
            />
          </div>

          <div>
            <label
              htmlFor="resolve-notes"
              className="field-label"
            >
              Notes for next time
            </label>
            <textarea
              id="resolve-notes"
              name="notes"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="field text-[13.5px]"
              placeholder="Follow-up action items, monitoring tickets, or architectural adjustments..."
            />
          </div>

          <div className="flex items-center justify-end gap-2.5 border-t border-line pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="btn btn-quiet"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy}
              className="btn btn-primary"
            >
              {busy ? (
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Check className="h-4 w-4" aria-hidden="true" />
              )}
              <span>Save resolution</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
