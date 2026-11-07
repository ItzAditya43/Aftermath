"use client";

import React, { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, BookOpen, LayoutGrid, Plus, TriangleAlert } from "lucide-react";
import { getHealth } from "@/lib/api-client";
import type { HealthResponse } from "@/lib/memory-types";
import { BrandMark } from "@/components/common/Status";
import ThemeToggle from "@/components/layout/ThemeToggle";

interface AppHeaderProps {
  onOpenCreate?: () => void;
}

const NAV_LINKS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutGrid },
  { href: "/incidents", label: "Incidents", icon: TriangleAlert },
  { href: "/memories", label: "Memories", icon: BookOpen },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
];

function healthLabel(health: HealthResponse | null): string {
  if (!health) return "Connecting to API";
  const mode =
    health.agent_mode === "mock"
      ? "demo mode"
      : (health.llm_providers_configured ?? []).length
        ? `real engine · ${(health.llm_providers_configured ?? []).join(", ")}`
        : "real engine · no AI provider";
  return `API ${health.status} · database ${health.database} · ${mode}`;
}

function HeaderNavContent({ onOpenCreate }: AppHeaderProps) {
  const pathname = usePathname();
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchHealth = async () => {
      try {
        const data = await getHealth();
        if (mounted) setHealth(data);
      } catch {
        if (mounted) setHealth(null);
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 30000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const isActive = (href: string) => (href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(href));
  const statusDot = health?.status === "ok" ? "bg-ok" : health?.status === "degraded" ? "bg-gold-mark" : "bg-ink-4";

  const newIncident = onOpenCreate ? (
    <button
      type="button"
      onClick={onOpenCreate}
      className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-lily text-white shadow-[0_10px_24px_-12px_var(--lily)] transition-colors hover:bg-[var(--lily-hover)]"
      aria-label="Create incident"
      title="New incident"
    >
      <Plus className="h-4 w-4" aria-hidden="true" />
    </button>
  ) : (
    <Link
      href="/incidents?create=true"
      className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-lily text-white shadow-[0_10px_24px_-12px_var(--lily)] transition-colors hover:bg-[var(--lily-hover)]"
      aria-label="Create incident"
      title="New incident"
    >
      <Plus className="h-4 w-4" aria-hidden="true" />
    </Link>
  );

  return (
    <>
      {/* Desktop: transparent rail on the page background */}
      <nav
        aria-label="Primary navigation"
        className="fixed inset-y-0 left-0 z-40 hidden w-[76px] flex-col items-center gap-1.5 border-r border-line py-4 md:flex"
      >
        <Link href="/" aria-label="Aftermath home" className="mb-4 grid h-11 w-11 place-items-center rounded-md hover:bg-raise">
          <BrandMark size={26} />
        </Link>
        {NAV_LINKS.map(({ href, label, icon: Icon }) => {
          const active = isActive(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`grid w-[62px] justify-items-center gap-1 rounded-md py-2.5 text-[11px] transition-colors ${
                active ? "bg-raise text-ink shadow-[inset_2px_0_0_var(--lily)]" : "text-ink-3 hover:bg-raise hover:text-ink"
              }`}
            >
              <Icon className="h-[19px] w-[19px]" strokeWidth={1.7} aria-hidden="true" />
              {label}
            </Link>
          );
        })}
        <span className="flex-1" />
        {newIncident}
        <ThemeToggle className="mt-2" />
        <span
          role="status"
          aria-live="polite"
          title={healthLabel(health)}
          aria-label={healthLabel(health)}
          className={`mt-3 h-2 w-2 rounded-full ${statusDot}`}
        />
      </nav>

      {/* Phone: transparent top bar that blurs what scrolls under it */}
      <header className="glass-bar sticky top-0 z-40 border-b border-line md:hidden">
        <div className="flex items-center gap-1 px-3 py-1.5">
          <Link href="/" aria-label="Aftermath home" className="grid h-10 w-10 shrink-0 place-items-center">
            <BrandMark size={22} />
          </Link>
          <nav aria-label="Mobile navigation" className="flex min-w-0 flex-1 justify-around">
            {NAV_LINKS.map(({ href, label, icon: Icon }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`grid min-h-[44px] flex-1 justify-items-center gap-0.5 px-1 pt-1.5 text-[10.5px] ${
                    active ? "text-ink shadow-[inset_0_-2px_0_var(--lily)]" : "text-ink-3"
                  }`}
                >
                  <Icon className="h-[18px] w-[18px]" strokeWidth={1.7} aria-hidden="true" />
                  {label}
                </Link>
              );
            })}
          </nav>
          {newIncident}
        </div>
      </header>
    </>
  );
}

export default function AppHeader(props: AppHeaderProps) {
  return (
    <Suspense fallback={<div className="h-14 md:hidden" />}>
      <HeaderNavContent {...props} />
    </Suspense>
  );
}
