"use client";

import React, { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowRight, ChevronLeft, ChevronRight, Plus, RefreshCw, Search, X } from "lucide-react";
import { SeverityBadge, StatusPill } from "@/components/common/Status";
import AppHeader from "@/components/layout/AppHeader";
import CreateIncidentModal from "@/components/incidents/CreateIncidentModal";
import { getIncidents } from "@/lib/api-client";
import type { IncidentSummary } from "@/lib/memory-types";

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
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

function IncidentsContent() {
  const searchParams = useSearchParams();
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createModalManuallyOpen, setCreateModalManuallyOpen] = useState(false);
  const createRequested = searchParams?.get("create") === "true";
  const showCreateModal = createRequested || createModalManuallyOpen;

  const handleCloseCreateModal = () => {
    setCreateModalManuallyOpen(false);
    if (createRequested && typeof window !== "undefined") {
      window.history.replaceState(null, "", "/incidents");
    }
  };

  // Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>(
    searchParams?.get("status") ?? "all"
  );
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [serviceFilter, setServiceFilter] = useState<string>("all");

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 12;

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getIncidents();
      setIncidents(data);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load incidents from the API."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    getIncidents()
      .then((data) => {
        if (!active) return;
        setIncidents(data);
        setLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load incidents from the API."
        );
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  // Extract unique services
  const uniqueServices = useMemo(() => {
    const set = new Set(incidents.map((i) => i.service));
    return Array.from(set).sort();
  }, [incidents]);

  // Filtered incidents
  const filteredIncidents = useMemo(() => {
    return incidents.filter((incident) => {
      // Search
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const matches =
          incident.id.toLowerCase().includes(query) ||
          incident.title.toLowerCase().includes(query) ||
          incident.service.toLowerCase().includes(query) ||
          (incident.root_cause && incident.root_cause.toLowerCase().includes(query));
        if (!matches) return false;
      }

      // Status
      if (statusFilter === "open") {
        if (incident.status === "resolved") return false;
      } else if (statusFilter !== "all" && incident.status !== statusFilter) {
        return false;
      }

      // Severity
      if (severityFilter !== "all" && incident.severity !== severityFilter) {
        return false;
      }

      // Service
      if (serviceFilter !== "all" && incident.service !== serviceFilter) {
        return false;
      }

      return true;
    });
  }, [incidents, searchQuery, statusFilter, severityFilter, serviceFilter]);

  // Paginated chunk
  const totalPages = Math.max(1, Math.ceil(filteredIncidents.length / pageSize));
  const paginatedIncidents = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredIncidents.slice(start, start + pageSize);
  }, [filteredIncidents, currentPage, pageSize]);

  return (
    <main className="mx-auto max-w-[1120px] px-4 pb-20 pt-8 sm:px-6 lg:px-8">
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Incidents</h1>
          <p className="mt-2 text-[14.5px] text-ink-3">
            Search, filter, and review active investigations and resolved incidents ({incidents.length} total).
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={loadData} disabled={loading} className="btn btn-quiet btn-sm" aria-label="Refresh incident listing">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </button>
          <button type="button" onClick={() => setCreateModalManuallyOpen(true)} className="btn btn-primary btn-sm">
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

      <div className="mb-3.5 grid gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" aria-hidden="true" />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setCurrentPage(1);
            }}
            placeholder="Search ID, title, service, root cause"
            className="field pl-9 pr-9"
            aria-label="Search incidents"
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
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="field"
          aria-label="Filter by incident status"
        >
          <option value="all">All statuses</option>
          <option value="open">Active (open + investigating)</option>
          <option value="investigating">Investigating only</option>
          <option value="resolved">Resolved only</option>
        </select>
        <select
          value={severityFilter}
          onChange={(e) => {
            setSeverityFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="field"
          aria-label="Filter by incident severity"
        >
          <option value="all">All severities</option>
          <option value="SEV1">SEV1 · Critical</option>
          <option value="SEV2">SEV2 · Major</option>
          <option value="SEV3">SEV3 · Moderate</option>
          <option value="SEV4">SEV4 · Minor</option>
        </select>
        <select
          value={serviceFilter}
          onChange={(e) => {
            setServiceFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="field"
          aria-label="Filter by service"
        >
          <option value="all">All services</option>
          {uniqueServices.map((svc) => (
            <option key={svc} value={svc}>
              {svc}
            </option>
          ))}
        </select>
      </div>

      <div className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th scope="col">Incident</th>
                <th scope="col">Title</th>
                <th scope="col">Service</th>
                <th scope="col">Severity</th>
                <th scope="col">Status</th>
                <th scope="col">Created</th>
                <th scope="col" className="!text-right">TTR</th>
                <th scope="col" className="!text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading && incidents.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-ink-3">Loading incidents…</td>
                </tr>
              ) : paginatedIncidents.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center">
                    <p className="display text-[22px]">No matching incidents</p>
                    <p className="mt-1 text-[13px] text-ink-3">Clear the search or filters to see more records.</p>
                  </td>
                </tr>
              ) : (
                paginatedIncidents.map((incident) => (
                  <tr key={incident.id} className={`sev-row-${incident.severity}`}>
                    <td className="stripe whitespace-nowrap font-mono text-[12.5px] font-medium">
                      <Link href={`/incidents/${incident.id}`} className="hover:underline">
                        {incident.id}
                      </Link>
                    </td>
                    <td className="min-w-[240px] max-w-[380px]">
                      <span className="line-clamp-2">{incident.title}</span>
                    </td>
                    <td>
                      <span className="svc">{incident.service}</span>
                    </td>
                    <td>
                      <SeverityBadge severity={incident.severity} />
                    </td>
                    <td>
                      <StatusPill status={incident.status} />
                    </td>
                    <td className="whitespace-nowrap font-mono text-[12.5px] text-ink-3">{formatTimestamp(incident.created_at)}</td>
                    <td className="num">{incident.status === "resolved" ? formatMinutes(incident.time_to_resolve_minutes) : <span className="text-ink-3">running</span>}</td>
                    <td className="text-right">
                      <Link
                        href={`/incidents/${incident.id}`}
                        className={`btn btn-sm ${incident.status === "resolved" ? "btn-quiet" : "btn-primary"}`}
                      >
                        {incident.status === "resolved" ? "Review" : "Investigate"}
                        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-3.5 py-3 text-[12.5px] text-ink-3">
          <span>
            {filteredIncidents.length === 0
              ? "No incidents shown"
              : `Showing ${(currentPage - 1) * pageSize + 1}–${Math.min(currentPage * pageSize, filteredIncidents.length)} of ${filteredIncidents.length}`}
          </span>
          {totalPages > 1 && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="btn btn-quiet btn-sm"
                aria-label="Previous page"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                Previous
              </button>
              <span className="font-mono">
                {currentPage} / {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="btn btn-quiet btn-sm"
                aria-label="Next page"
              >
                Next
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </div>

      <CreateIncidentModal
        isOpen={showCreateModal}
        onClose={handleCloseCreateModal}
        onCreated={(created) => {
          setIncidents((prev) => [created, ...prev]);
        }}
      />
    </main>
  );
}

export default function IncidentsPage() {
  return (
    <div className="min-h-screen md:pl-[76px]">
      <AppHeader />
      <Suspense fallback={<div className="p-8 text-center text-[13px] text-ink-3">Loading incidents…</div>}>
        <IncidentsContent />
      </Suspense>
    </div>
  );
}
