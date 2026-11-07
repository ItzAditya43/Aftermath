"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Plus, RefreshCw, X } from "lucide-react";
import { SeverityBadge, StatusPill } from "@/components/common/Status";
import AppHeader from "@/components/layout/AppHeader";
import CreateIncidentModal from "@/components/incidents/CreateIncidentModal";
import { getIncidents, getStats } from "@/lib/api-client";
import type { IncidentSummary, StatsResponse } from "@/lib/memory-types";

function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null || isNaN(minutes)) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = Math.floor(minutes / 60);
  const rem = Math.round(minutes % 60);
  return rem > 0 ? `${hours}h ${rem}m` : `${hours}h`;
}

function formatRelativeTime(dateStr: string): string {
  try {
    const diffMs = Date.now() - new Date(dateStr).getTime();
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    if (diffHours < 1) {
      const diffMins = Math.max(1, Math.floor(diffMs / (1000 * 60)));
      return `${diffMins}m ago`;
    }
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  } catch {
    return dateStr;
  }
}

export default function DashboardPage() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showGettingStarted, setShowGettingStarted] = useState(true);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [statsData, incidentsData] = await Promise.all([
        getStats(),
        getIncidents(),
      ]);
      setStats(statsData);
      setIncidents(incidentsData);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load dashboard metrics. Check API connection."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    Promise.all([getStats(), getIncidents()])
      .then(([statsData, incidentsData]) => {
        if (!active) return;
        setStats(statsData);
        setIncidents(incidentsData);
        setLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load dashboard metrics. Check API connection."
        );
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const openIncidents = incidents.filter(
    (i) => i.status === "open" || i.status === "investigating"
  );
  const resolvedIncidents = incidents.filter((i) => i.status === "resolved");

  const speedup =
    stats?.avg_ttr_with_memory != null && stats?.avg_ttr_without_memory != null && stats.avg_ttr_with_memory > 0
      ? stats.avg_ttr_without_memory / stats.avg_ttr_with_memory
      : null;

  return (
    <div className="min-h-screen md:pl-[76px]">
      <AppHeader onOpenCreate={() => setShowCreateModal(true)} />

      <main className="mx-auto max-w-[1120px] px-4 pb-20 pt-8 sm:px-6 lg:px-8">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="page-title">Dashboard</h1>
            <p className="mt-2 text-[14.5px] text-ink-3">
              {loading
                ? "Loading incidents…"
                : `${openIncidents.length} active ${openIncidents.length === 1 ? "incident" : "incidents"}. Memory has ${resolvedIncidents.length} resolved incidents to draw on.`}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={loadData} disabled={loading} className="btn btn-quiet btn-sm" aria-label="Refresh dashboard metrics">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Refresh
            </button>
            <button type="button" onClick={() => setShowCreateModal(true)} className="btn btn-primary btn-sm">
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create incident
            </button>
          </div>
        </div>

        {error && (
          <div role="alert" className="callout-error mb-4">
            {error}
          </div>
        )}

        <section aria-label="Key numbers" className="kpis">
          <div className="kpi">
            <div className="label">Active incidents</div>
            <div className={`kpi-v ${stats && stats.open + stats.investigating > 0 ? "text-lily-ink" : ""}`}>
              {stats ? stats.open + stats.investigating : "—"}
            </div>
            <div className="kpi-s">{stats ? `${stats.open} open · ${stats.investigating} investigating` : "Waiting for the API"}</div>
          </div>
          <div className="kpi">
            <div className="label">Resolved</div>
            <div className="kpi-v">{stats?.resolved ?? "—"}</div>
            <div className="kpi-s">of {stats?.total_incidents ?? 0} total</div>
          </div>
          <div className="kpi">
            <div className="label">Mean time to resolve</div>
            <div className="kpi-v">{formatMinutes(stats?.avg_time_to_resolve_minutes)}</div>
            <div className="kpi-s">All recorded resolutions</div>
          </div>
          <div className="kpi">
            <div className="label">With memory</div>
            <div className="kpi-v text-gold">{speedup != null ? `${speedup.toFixed(1)}×` : "—"}</div>
            <div className="kpi-s">
              {stats?.avg_ttr_with_memory != null
                ? `${formatMinutes(stats.avg_ttr_with_memory)} with memory vs ${formatMinutes(stats.avg_ttr_without_memory)} without`
                : "Shown once memory-assisted resolutions exist"}
            </div>
          </div>
        </section>

        {showGettingStarted && (
          <section
            aria-label="How to investigate an incident"
            className="mt-4 grid items-start gap-6 rounded-[10px] border border-dashed border-line-strong px-5 py-5 lg:grid-cols-[auto_repeat(3,minmax(0,1fr))_auto]"
          >
            <h2 className="display max-w-[12ch] text-[22px] leading-[1.1]">How to investigate an incident</h2>
            {[
              ["Create an incident", "Add the alert and relevant logs."],
              ["Analyze the evidence", "Review likely causes and related incidents."],
              ["Record the outcome", "Save the confirmed fix and what failed."],
            ].map(([title, text], i) => (
              <div key={title}>
                <span className="label">Step {i + 1}</span>
                <b className="mt-1 block text-[13.5px] font-medium">{title}</b>
                <span className="text-[13px] text-ink-3">{text}</span>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setShowGettingStarted(false)}
              className="btn btn-quiet btn-sm btn-icon -order-1 justify-self-end border-transparent lg:order-none"
              aria-label="Dismiss getting started guidance"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </section>
        )}

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <section aria-labelledby="active-incidents-heading" className="panel">
            <div className="panel-h">
              <h2 id="active-incidents-heading">Needs attention</h2>
              <span className={`pill ${openIncidents.length ? "st-open" : ""}`}>{openIncidents.length} active</span>
            </div>
            {openIncidents.length === 0 ? (
              <div className="px-6 py-10 text-center">
                <p className="display text-[22px]">All services operational</p>
                <p className="mt-1 text-[13px] text-ink-3">No open or uninvestigated incidents right now.</p>
              </div>
            ) : (
              <ul className="m-0 list-none p-0">
                {openIncidents.slice(0, 5).map((incident) => (
                  <li key={incident.id} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3.5 border-b border-line px-[18px] py-3.5 last:border-b-0">
                    <SeverityBadge severity={incident.severity} />
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{incident.title}</div>
                      <div className="mt-0.5 truncate font-mono text-xs text-ink-3">
                        {incident.id} · {incident.service} · {formatRelativeTime(incident.created_at)}
                      </div>
                    </div>
                    <Link href={`/incidents/${incident.id}`} className="btn btn-primary btn-sm">
                      Investigate
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {openIncidents.length > 5 && (
              <div className="border-t border-line px-[18px] py-3 text-[13px]">
                <Link href="/incidents?status=open" className="btn-link">
                  View all {openIncidents.length} active incidents
                </Link>
              </div>
            )}
          </section>

          <section aria-labelledby="recent-activity-heading" className="panel">
            <div className="panel-h">
              <h2 id="recent-activity-heading">Recent incidents</h2>
              <Link href="/incidents" className="btn-link text-[13px]">
                View all
              </Link>
            </div>
            <ul className="m-0 list-none p-0">
              {incidents.slice(0, 5).map((incident) => (
                <li key={incident.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3.5 border-b border-line px-[18px] py-3.5 last:border-b-0">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-medium">{incident.id}</span>
                      <StatusPill status={incident.status} />
                      <span className="svc truncate text-xs">{incident.service}</span>
                    </div>
                    <div className="mt-1 truncate text-[13.5px]">{incident.title}</div>
                    {incident.root_cause && <div className="mt-0.5 truncate text-[12.5px] text-ink-3">Root cause: {incident.root_cause}</div>}
                  </div>
                  <div className="flex items-center gap-2">
                    {incident.time_to_resolve_minutes != null && (
                      <span className="font-mono text-xs text-ink-2">{formatMinutes(incident.time_to_resolve_minutes)}</span>
                    )}
                    <Link href={`/incidents/${incident.id}`} className="btn btn-quiet btn-sm btn-icon border-line" aria-label={`View incident ${incident.id}`}>
                      <ArrowRight className="h-4 w-4" aria-hidden="true" />
                    </Link>
                  </div>
                </li>
              ))}
              {!loading && incidents.length === 0 && (
                <li className="px-6 py-10 text-center text-[13px] text-ink-3">No incidents yet. Create one to start an investigation.</li>
              )}
            </ul>
          </section>
        </div>

        <section aria-label="Shortcuts" className="mt-4 grid gap-2 sm:grid-cols-3">
          {[
            { href: "/incidents", title: "All incidents", text: `Search and filter all ${incidents.length} incidents` },
            { href: "/memories", title: "Memory bank", text: `${resolvedIncidents.length} resolved incidents with fixes` },
            { href: "/analytics", title: "Reliability analytics", text: "Resolution time by month and service" },
          ].map((item) => (
            <Link key={item.href} href={item.href} className="group flex items-center justify-between gap-3 rounded-[10px] border border-line px-4 py-3.5 hover:bg-raise">
              <span>
                <span className="block text-sm font-medium">{item.title}</span>
                <span className="block text-[12.5px] text-ink-3">{item.text}</span>
              </span>
              <ArrowRight className="h-4 w-4 text-ink-3 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
            </Link>
          ))}
        </section>
      </main>

      <CreateIncidentModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreated={(created) => {
          setIncidents((prev) => [created, ...prev]);
          loadData();
        }}
      />
    </div>
  );
}
