"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, RefreshCw, Search, X } from "lucide-react";
import AppHeader from "@/components/layout/AppHeader";
import { getIncident, getIncidents, getStats } from "@/lib/api-client";
import type { IncidentDetail, IncidentSummary, StatsResponse } from "@/lib/memory-types";
import { SeverityBadge } from "@/components/common/Status";

function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null || isNaN(minutes)) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = Math.floor(minutes / 60);
  const rem = Math.round(minutes % 60);
  return rem > 0 ? `${hours}h ${rem}m` : `${hours}h`;
}

function formatTimestamp(isoString: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

export default function MemoriesPage() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [serviceFilter, setServiceFilter] = useState("all");
  // Full records (fix that worked, failed attempts, notes) for the resolved incidents.
  const [details, setDetails] = useState<Record<string, IncidentDetail>>({});
  const requestedRef = useRef<Set<string>>(new Set());

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [incidentsData, statsData] = await Promise.all([
        getIncidents(),
        getStats().catch(() => null),
      ]);
      setIncidents(incidentsData);
      setStats(statsData);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load memories."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    Promise.all([
      getIncidents(),
      getStats().catch(() => null),
    ])
      .then(([incidentsData, statsData]) => {
        if (!active) return;
        setIncidents(incidentsData);
        setStats(statsData);
        setLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error ? err.message : "Unable to load memories."
        );
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Filter for incidents with root causes or resolved status
  const memoryIncidents = useMemo(() => {
    return incidents.filter(
      (i) => i.status === "resolved" || Boolean(i.root_cause)
    );
  }, [incidents]);

  const uniqueServices = useMemo(() => {
    const set = new Set(memoryIncidents.map((i) => i.service));
    return Array.from(set).sort();
  }, [memoryIncidents]);

  useEffect(() => {
    const missing = memoryIncidents.filter((i) => !requestedRef.current.has(i.id)).slice(0, 60);
    if (missing.length === 0) return;
    missing.forEach((i) => requestedRef.current.add(i.id));
    Promise.all(missing.map((i) => getIncident(i.id).catch(() => null))).then((rows) => {
      setDetails((prev) => {
        const next = { ...prev };
        for (const row of rows) if (row) next[row.id] = row;
        return next;
      });
    });
  }, [memoryIncidents]);

  const filteredMemories = useMemo(() => {
    return memoryIncidents.filter((incident) => {
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matches =
          incident.id.toLowerCase().includes(q) ||
          incident.title.toLowerCase().includes(q) ||
          incident.service.toLowerCase().includes(q) ||
          (incident.root_cause && incident.root_cause.toLowerCase().includes(q)) ||
          incident.alert_text.toLowerCase().includes(q) ||
          (details[incident.id]?.resolution ?? "").toLowerCase().includes(q) ||
          (details[incident.id]?.failed_attempts ?? []).some((a) => a.toLowerCase().includes(q));
        if (!matches) return false;
      }

      if (serviceFilter !== "all" && incident.service !== serviceFilter) {
        return false;
      }

      return true;
    });
  }, [memoryIncidents, searchQuery, serviceFilter, details]);

  return (
    <div className="min-h-screen md:pl-[76px]">
      <AppHeader />

      <main className="mx-auto max-w-[1120px] px-4 pb-20 pt-8 sm:px-6 lg:px-8">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="page-title">Memory bank</h1>
            <p className="mt-2 max-w-[64ch] text-[14.5px] text-ink-3">
              Confirmed fixes and failed attempts from resolved incidents. The agent reads these when a new incident looks similar.
            </p>
          </div>
          <button type="button" onClick={loadData} disabled={loading} className="btn btn-quiet btn-sm" aria-label="Refresh memory entries">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </button>
        </div>

        {error && (
          <div role="alert" className="callout-error mb-4">
            {error}
          </div>
        )}

        <section aria-label="Memory metrics" className="kpis [--kpi-cols:3]">
          <div className="kpi">
            <div className="label">Indexed incidents</div>
            <div className="kpi-v text-gold">{memoryIncidents.length}</div>
            <div className="kpi-s">Available to the agent</div>
          </div>
          <div className="kpi">
            <div className="label">Services covered</div>
            <div className="kpi-v">{uniqueServices.length}</div>
            <div className="kpi-s truncate">{uniqueServices.slice(0, 4).join(", ") || "None yet"}</div>
          </div>
          <div className="kpi">
            <div className="label">Resolution with memory</div>
            <div className="kpi-v">{formatMinutes(stats?.avg_ttr_with_memory)}</div>
            <div className="kpi-s">
              {stats?.avg_ttr_without_memory != null && stats.avg_ttr_with_memory != null
                ? `vs ${formatMinutes(stats.avg_ttr_without_memory)} without memory`
                : "Mean duration"}
            </div>
          </div>
        </section>

        <div className="my-4 grid gap-2 sm:grid-cols-[minmax(0,1fr)_16rem]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" aria-hidden="true" />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search root causes, fixes, failed attempts, services"
              className="field pl-9 pr-9"
              aria-label="Search incident memories"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-1.5 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center text-ink-3 hover:text-ink"
                aria-label="Clear search"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>
          <select value={serviceFilter} onChange={(e) => setServiceFilter(e.target.value)} className="field" aria-label="Filter memories by service">
            <option value="all">All services ({memoryIncidents.length})</option>
            {uniqueServices.map((svc) => (
              <option key={svc} value={svc}>
                {svc}
              </option>
            ))}
          </select>
        </div>

        {filteredMemories.length === 0 ? (
          <div className="rounded-[10px] border border-dashed border-line-strong p-12 text-center">
            <p className="display text-[24px]">{loading ? "Loading memories…" : "No memories match"}</p>
            {!loading && (
              <p className="mt-1 text-[13px] text-ink-3">Try a general word like &quot;deploy&quot;, &quot;pool&quot; or &quot;OOM&quot;, or clear the filters.</p>
            )}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {filteredMemories.map((mem) => {
              const detail = details[mem.id];
              return (
                <article key={mem.id} className="panel grid content-start gap-3.5 p-5">
                  <div className="flex items-center justify-between gap-2">
                    <Link href={`/incidents/${mem.id}`} className="font-mono text-[12.5px] font-medium text-gold hover:underline">
                      {mem.id}
                    </Link>
                    <SeverityBadge severity={mem.severity} />
                  </div>
                  <h2 className="display text-[23px] leading-[1.15]">{mem.title}</h2>
                  <dl className="m-0 grid gap-2.5">
                    <div>
                      <dt className="label mb-1 text-[10.5px]">Root cause</dt>
                      <dd className="m-0 text-[13.5px] text-ink-2">{mem.root_cause ?? "Not recorded"}</dd>
                    </div>
                    {detail?.resolution && (
                      <div>
                        <dt className="label mb-1 text-[10.5px]">What worked</dt>
                        <dd className="m-0 text-[13.5px] text-ink-2">{detail.resolution}</dd>
                      </div>
                    )}
                    {detail && (
                      <div>
                        <dt className="label mb-1 text-[10.5px]">What failed</dt>
                        <dd className="m-0 text-[13.5px] text-lily-ink">
                          {detail.failed_attempts.length ? detail.failed_attempts.join(" · ") : "Nothing recorded as failed."}
                        </dd>
                      </div>
                    )}
                  </dl>
                  {detail?.notes && (
                    <p className="display border-t border-line pt-3 text-[17px] italic leading-snug text-gold">&ldquo;{detail.notes}&rdquo;</p>
                  )}
                  <div className="flex items-center justify-between gap-2 font-mono text-xs text-ink-3">
                    <span>
                      {mem.service} · {formatTimestamp(mem.resolved_at ?? mem.created_at)}
                    </span>
                    <span className="flex items-center gap-3">
                      {formatMinutes(mem.time_to_resolve_minutes)}
                      <Link href={`/incidents/${mem.id}`} className="inline-flex items-center gap-1 text-ink hover:underline" aria-label={`Open incident ${mem.id}`}>
                        Open
                        <ArrowRight className="h-3 w-3" aria-hidden="true" />
                      </Link>
                    </span>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
