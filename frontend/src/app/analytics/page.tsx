"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { RefreshCw, Sparkles } from "lucide-react";
import AppHeader from "@/components/layout/AppHeader";
import { getIncidents, getStats } from "@/lib/api-client";
import type { IncidentSummary, StatsResponse } from "@/lib/memory-types";

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
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

function formatPeriod(period: string): string {
  const [year, month] = period.split("-").map(Number);
  if (!year || !month) return period;
  return new Intl.DateTimeFormat("en-US", { month: "short" }).format(new Date(Date.UTC(year, month - 1, 15)));
}

const SEVERITY_NOTES: Record<string, [string, string]> = {
  SEV1: ["Critical", "Complete service outage"],
  SEV2: ["Major", "Degraded customer traffic"],
  SEV3: ["Moderate", "Non-critical background jobs"],
  SEV4: ["Minor", "Low urgency or cosmetic"],
};

/** Mean time to resolve per month. One series, so no legend; the last month is highlighted in gold. */
function MonthlyTtrChart({ data }: { data: { period: string; resolved: number; avg_ttr_minutes: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560;
  const H = 260;
  const L = 44;
  const R = 12;
  const T = 26;
  const B = 30;
  const rawMax = Math.max(...data.map((d) => d.avg_ttr_minutes), 1);
  const step = rawMax > 120 ? 50 : rawMax > 60 ? 30 : rawMax > 20 ? 10 : 5;
  const max = Math.ceil(rawMax / step) * step;
  const ticks = Array.from({ length: Math.floor(max / step) + 1 }, (_, i) => i * step);
  const colW = (W - L - R) / data.length;
  const barW = Math.min(44, colW * 0.5);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);

  return (
    <div className="px-4 pb-4 pt-2">
      <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block h-auto w-full"
        role="img"
        aria-label={`Mean resolution time by month: ${data.map((d) => `${formatPeriod(d.period)} ${Math.round(d.avg_ttr_minutes)} minutes`).join(", ")}`}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} style={{ stroke: "var(--line)" }} />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" className="font-mono text-[11px]" style={{ fill: "var(--ink-3)" }}>
              {v}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = L + colW * i + (colW - barW) / 2;
          const top = y(d.avg_ttr_minutes);
          const h = Math.max(0, y(0) - top);
          const r = Math.min(4, h / 2);
          const last = i === data.length - 1;
          const fill = hover === i ? (last ? "var(--gold)" : "var(--ink-3)") : last ? "var(--gold-mark)" : "var(--line-strong)";
          return (
            <g key={d.period} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={L + colW * i} y={T} width={colW} height={H - T - B} style={{ fill: "transparent" }} />
              <path
                d={`M${x} ${y(0)}V${top + r}Q${x} ${top} ${x + r} ${top}H${x + barW - r}Q${x + barW} ${top} ${x + barW} ${top + r}V${y(0)}Z`}
                style={{ fill, transition: "fill 120ms" }}
              />
              <text x={x + barW / 2} y={H - 10} textAnchor="middle" className="font-mono text-[11px]" style={{ fill: "var(--ink-3)" }}>
                {formatPeriod(d.period)}
              </text>
              {(i === 0 || last) && (
                <text x={x + barW / 2} y={top - 8} textAnchor="middle" className="font-mono text-[12px] font-medium" style={{ fill: "var(--ink)" }}>
                  {Math.round(d.avg_ttr_minutes)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover != null && (
        <div
          className="pointer-events-none absolute whitespace-nowrap rounded-md bg-ink px-2.5 py-2 font-mono text-xs leading-snug text-paper"
          style={{ left: `${((L + colW * hover + colW / 2) / W) * 100}%`, top: 0, transform: "translateX(-50%)" }}
        >
          {data[hover].period}
          <br />
          {Math.round(data[hover].avg_ttr_minutes)} min mean · {data[hover].resolved} resolved
        </div>
      )}
      </div>
    </div>
  );
}

export default function AnalyticsPage() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
          : "Unable to load analytics metrics from API."
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
            : "Unable to load analytics metrics from API."
        );
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const severityDistribution = useMemo(() => {
    const counts: Record<string, number> = { SEV1: 0, SEV2: 0, SEV3: 0, SEV4: 0 };
    for (const inc of incidents) {
      if (counts[inc.severity] !== undefined) counts[inc.severity]++;
    }
    return counts;
  }, [incidents]);

  const serviceRecurrence = useMemo(() => {
    if (!stats?.by_service) return [];
    return Object.entries(stats.by_service)
      .map(([service, count]) => ({ service, count }))
      .sort((a, b) => b.count - a.count);
  }, [stats]);

  const maxServiceCount = useMemo(
    () => Math.max(1, ...serviceRecurrence.map((s) => s.count)),
    [serviceRecurrence]
  );

  const resolutionRate = useMemo(() => {
    if (!stats || stats.total_incidents === 0) return 0;
    return Math.round((stats.resolved / stats.total_incidents) * 100);
  }, [stats]);

  const assistedCount = stats?.resolutions?.filter((r) => r.memory_assisted).length ?? 0;

  return (
    <div className="min-h-screen md:pl-[76px]">
      <AppHeader />

      <main className="mx-auto max-w-[1120px] px-4 pb-20 pt-8 sm:px-6 lg:px-8">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="page-title">Reliability analytics</h1>
            <p className="mt-2 text-[14.5px] text-ink-3">
              Resolution time, service reliability, and how often memory helped. Calculated from saved incidents.
            </p>
          </div>
          <button type="button" onClick={loadData} disabled={loading} className="btn btn-quiet btn-sm" aria-label="Refresh analytics data">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </button>
        </div>

        {error && (
          <div role="alert" className="callout-error mb-4">
            {error}
          </div>
        )}

        <section aria-label="Overall reliability metrics" className="kpis">
          <div className="kpi">
            <div className="label">Overall resolution time</div>
            <div className="kpi-v">{formatMinutes(stats?.avg_time_to_resolve_minutes)}</div>
            <div className="kpi-s">Across {stats?.resolved ?? 0} resolved incidents</div>
          </div>
          <div className="kpi">
            <div className="label">With memory</div>
            <div className="kpi-v text-gold">{formatMinutes(stats?.avg_ttr_with_memory)}</div>
            <div className="kpi-s">When a similar past incident was recalled</div>
          </div>
          <div className="kpi">
            <div className="label">Without memory</div>
            <div className="kpi-v">{formatMinutes(stats?.avg_ttr_without_memory)}</div>
            <div className="kpi-s">First-time or unmatched failures</div>
          </div>
          <div className="kpi">
            <div className="label">Resolution rate</div>
            <div className="kpi-v">
              {resolutionRate}
              <small>%</small>
            </div>
            <div className="kpi-s">
              {stats?.resolved ?? 0} of {stats?.total_incidents ?? 0} closed with a root cause
            </div>
          </div>
        </section>

        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <section aria-labelledby="ttr-chart-heading" className="panel">
            <div className="panel-h">
              <h2 id="ttr-chart-heading">Mean time to resolve, by month</h2>
              <span className="label">Minutes</span>
            </div>
            {stats?.ttr_over_time && stats.ttr_over_time.length > 0 ? (
              <MonthlyTtrChart data={stats.ttr_over_time} />
            ) : (
              <p className="px-6 py-12 text-center text-[13px] text-ink-3">{loading ? "Loading…" : "No resolved incidents yet."}</p>
            )}
          </section>

          <section aria-labelledby="services-breakdown-heading" className="panel">
            <div className="panel-h">
              <h2 id="services-breakdown-heading">Incidents by service</h2>
              <span className="label">{serviceRecurrence.length} services</span>
            </div>
            <div className="grid gap-3.5 p-[18px]">
              {serviceRecurrence.map(({ service, count }) => (
                <div key={service}>
                  <div className="flex items-baseline justify-between gap-2 text-[13px]">
                    <span className="svc">{service}</span>
                    <span className="font-mono text-xs text-ink-3">{count}</span>
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-raise">
                    <div className="h-full rounded-full bg-ink-3" style={{ width: `${Math.round((count / maxServiceCount) * 100)}%` }} />
                  </div>
                </div>
              ))}
              {serviceRecurrence.length === 0 && <p className="text-[13px] text-ink-3">No incidents yet.</p>}
            </div>
          </section>
        </div>

        <section aria-labelledby="severity-distribution-heading" className="panel mt-4">
          <div className="panel-h">
            <h2 id="severity-distribution-heading">Severity distribution</h2>
            <span className="label">{incidents.length} incidents</span>
          </div>
          <div className="kpis rounded-none border-0 shadow-none">
            {(["SEV1", "SEV2", "SEV3", "SEV4"] as const).map((sev) => (
              <div key={sev} className="kpi">
                <div className="flex items-center gap-2">
                  <span className={`sev sev-${sev}`}>{sev}</span>
                  <span className="text-[13px] text-ink-2">{SEVERITY_NOTES[sev][0]}</span>
                </div>
                <div className={`kpi-v ${sev === "SEV1" && severityDistribution.SEV1 > 0 ? "text-lily-ink" : ""}`}>{severityDistribution[sev]}</div>
                <div className="kpi-s">{SEVERITY_NOTES[sev][1]}</div>
              </div>
            ))}
          </div>
        </section>

        {stats?.ttr_over_time && stats.ttr_over_time.length > 0 && (
          <section aria-labelledby="ttr-trends-heading" className="panel mt-4">
            <div className="panel-h">
              <h2 id="ttr-trends-heading">Monthly resolution time</h2>
              <span className="label">Same data as the chart</span>
            </div>
            <div className="overflow-x-auto">
              <table className="tbl">
                <thead>
                  <tr>
                    <th scope="col">Period</th>
                    <th scope="col" className="!text-right">Incidents resolved</th>
                    <th scope="col" className="!text-right">Mean resolution time</th>
                    <th scope="col" className="!text-right">Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.ttr_over_time.map((t, idx, arr) => {
                    const prev = arr[idx - 1];
                    const improved = prev ? t.avg_ttr_minutes < prev.avg_ttr_minutes : null;
                    return (
                      <tr key={t.period}>
                        <td className="font-mono text-[12.5px]">{t.period}</td>
                        <td className="num">{t.resolved}</td>
                        <td className="num">{formatMinutes(t.avg_ttr_minutes)}</td>
                        <td className="text-right text-[13px]">
                          {improved === true && <span className="text-ok">Faster</span>}
                          {improved === false && <span className="text-ink-3">Slower</span>}
                          {improved === null && <span className="text-ink-3">Baseline</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {stats?.resolutions && stats.resolutions.length > 0 && (
          <section aria-labelledby="resolutions-history-heading" className="panel mt-4">
            <div className="panel-h">
              <h2 id="resolutions-history-heading">Resolution history and memory assistance</h2>
              <span className="label">
                {stats.resolutions.length} resolutions · <span className="text-gold">{assistedCount} assisted</span>
              </span>
            </div>
            <div className="max-h-[440px] overflow-auto" tabIndex={0} role="region" aria-label="Resolution history log">
              <table className="tbl">
                <thead className="sticky top-0 z-10 bg-surface">
                  <tr>
                    <th scope="col">Incident</th>
                    <th scope="col">Service</th>
                    <th scope="col">Resolved</th>
                    <th scope="col" className="!text-right">Resolution time</th>
                    <th scope="col" className="!text-right">Memory</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.resolutions.map((r) => (
                    <tr key={r.id}>
                      <td className="min-w-[280px]">
                        <Link href={`/incidents/${r.id}`} className="font-mono text-[12.5px] font-medium hover:underline">
                          {r.id}
                        </Link>
                        <span className="ml-2.5">{r.title}</span>
                      </td>
                      <td>
                        <span className="svc">{r.service}</span>
                      </td>
                      <td className="whitespace-nowrap text-[13px] text-ink-3">{formatTimestamp(r.resolved_at)}</td>
                      <td className="num font-medium">{formatMinutes(r.time_to_resolve_minutes)}</td>
                      <td className="text-right">
                        {r.memory_assisted ? (
                          <span className="pill pill-memory">
                            <Sparkles className="h-3 w-3" aria-hidden="true" />
                            Assisted
                          </span>
                        ) : (
                          <span className="pill text-ink-3">Baseline</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
