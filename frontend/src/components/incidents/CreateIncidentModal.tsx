"use client";

import React, { FormEvent, useState } from "react";
import { AlertCircle, LoaderCircle, X } from "lucide-react";
import { createIncident } from "@/lib/api-client";
import type { CreateIncidentRequest, IncidentDetail, Severity } from "@/lib/memory-types";

interface CreateIncidentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (incident: IncidentDetail) => void;
}

export default function CreateIncidentModal({
  isOpen,
  onClose,
  onCreated,
}: CreateIncidentModalProps) {
  const [severity, setSeverity] = useState<Severity>("SEV2");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serviceName, setServiceName] = useState("");
  const serviceNameInvalid = serviceName.length > 0 && !/^[a-z0-9][a-z0-9._\-]*$/.test(serviceName);

  if (!isOpen) return null;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);

    const payload: CreateIncidentRequest = {
      title: String(data.get("title") ?? "").trim(),
      service: String(data.get("service") ?? "").trim(),
      severity,
      alert_text: String(data.get("alert_text") ?? "").trim(),
      logs: String(data.get("logs") ?? ""),
    };

    try {
      const created = await createIncident(payload);
      form.reset();
      setServiceName("");
      setSeverity("SEV2");
      onCreated(created);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to create incident."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      className="modal-backdrop"
    >
      <div
        className="modal-card focus:outline-none"
        tabIndex={-1}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line pb-4">
          <div>
            <h2 id="modal-title" className="display text-[28px] leading-tight">
              Create incident
            </h2>
            <p className="mt-1 text-[13px] text-ink-3">
              Add an alert and evidence so the investigation can compare likely causes.
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
              htmlFor="incident-title"
              className="field-label"
            >
              Title
            </label>
            <input
              id="incident-title"
              name="title"
              required
              minLength={3}
              maxLength={200}
              className="field"
              placeholder="e.g. Checkout service failing 502 Bad Gateway"
            />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label
                htmlFor="incident-service"
                className="field-label"
              >
                Service
              </label>
              <input
                id="incident-service"
                name="service"
                required
                pattern="[a-z0-9][a-z0-9._\-]*"
                maxLength={64}
                value={serviceName}
                onChange={(event) => setServiceName(event.target.value)}
                aria-invalid={serviceNameInvalid}
                aria-describedby="incident-service-hint"
                className={`field ${serviceNameInvalid ? "!border-lily" : ""}`}
                placeholder="payments-api"
                title="Use lowercase letters, digits, dots, underscores, or hyphens."
              />
              <span id="incident-service-hint" className={`mt-1.5 block text-[12.5px] ${serviceNameInvalid ? "text-lily-ink" : "text-ink-3"}`}>
                {serviceNameInvalid
                ? "Use a lowercase letter or digit first, then lowercase letters, digits, dots, underscores, or hyphens."
                  : "Lowercase letters, digits, dots, underscores, and hyphens."}
              </span>
            </div>

            <div>
              <label
                htmlFor="incident-severity"
                className="field-label"
              >
                Severity
              </label>
              <select
                id="incident-severity"
                name="severity"
                value={severity}
                onChange={(e) => setSeverity(e.target.value as Severity)}
                className="field"
              >
                <option value="SEV1">SEV1 · Critical outage</option>
                <option value="SEV2">SEV2 · Major impairment</option>
                <option value="SEV3">SEV3 · Moderate degradation</option>
                <option value="SEV4">SEV4 · Minor, low impact</option>
              </select>
            </div>
          </div>

          <div>
            <label
              htmlFor="incident-alert"
              className="field-label"
            >
              Alert description
            </label>
            <textarea
              id="incident-alert"
              name="alert_text"
              required
              rows={3}
              className="field font-mono text-[13.5px]"
              placeholder="Paste raw alert webhook, pager message, or symptom description..."
            />
          </div>

          <div>
            <label
              htmlFor="incident-logs"
              className="field-label"
            >
              Logs (optional)
            </label>
            <textarea
              id="incident-logs"
              name="logs"
              rows={4}
              className="field font-mono text-[13.5px]"
              placeholder="Paste stack traces, recent stdout logs, or metric anomalies..."
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
              {busy && <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />}
              <span>Create incident</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
