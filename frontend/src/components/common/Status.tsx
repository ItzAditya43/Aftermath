import React from "react";
import type { IncidentStatus } from "@/lib/memory-types";

const STATUS_LABEL: Record<IncidentStatus, string> = {
  open: "Open",
  investigating: "Investigating",
  resolved: "Resolved",
};

export function SeverityBadge({ severity, long = false }: { severity: string; long?: boolean }) {
  const names: Record<string, string> = { SEV1: "Critical", SEV2: "Major", SEV3: "Moderate", SEV4: "Minor" };
  return (
    <span className={`sev sev-${severity}`}>
      {severity}
      {long && names[severity] ? ` · ${names[severity]}` : ""}
    </span>
  );
}

export function StatusPill({ status }: { status: IncidentStatus }) {
  return <span className={`pill pill-dot st-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

/** The product mark: a red incident dot inside a gold memory ring. */
export function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="shrink-0">
      <circle
        cx="12"
        cy="12"
        r="10"
        strokeWidth="1.6"
        strokeDasharray="46 17"
        transform="rotate(-60 12 12)"
        style={{ fill: "none", stroke: "var(--gold-mark)" }}
      />
      <circle cx="12" cy="12" r="4.5" style={{ fill: "var(--lily)" }} />
    </svg>
  );
}
