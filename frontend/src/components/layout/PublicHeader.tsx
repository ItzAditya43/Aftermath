"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Menu, Plus, X } from "lucide-react";
import { BrandMark } from "@/components/common/Status";
import ThemeToggle from "@/components/layout/ThemeToggle";

interface PublicHeaderProps {
  onOpenCreate?: () => void;
}

const NAV_LINKS = [
  { href: "#the-problem", label: "The problem" },
  { href: "#the-story", label: "How memory works" },
  { href: "#how-it-works", label: "Workflow" },
  { href: "#showcase", label: "Platform" },
  { href: "#why-aftermath", label: "Why Aftermath" },
];

export default function PublicHeader({ onOpenCreate }: PublicHeaderProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`glass-bar sticky top-0 z-50 border-b transition-colors duration-300 ${
        scrolled || mobileOpen ? "border-line" : "border-transparent !bg-transparent !backdrop-blur-none"
      }`}
    >
      <div className="mx-auto flex h-[68px] max-w-[1180px] items-center justify-between gap-6 px-4 sm:px-6 lg:px-8">
        <Link href="/" aria-label="Aftermath homepage" className="inline-flex items-center gap-2.5">
          <BrandMark />
          <span className="display text-[25px] leading-none">Aftermath</span>
        </Link>

        <nav aria-label="Public website sections" className="hidden items-center gap-6 lg:flex">
          {NAV_LINKS.map(({ href, label }) => (
            <a key={href} href={href} className="text-[13.5px] text-ink-3 transition-colors hover:text-ink">
              {label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          {onOpenCreate && (
            <button type="button" onClick={onOpenCreate} className="btn btn-quiet btn-sm hidden sm:inline-flex">
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create incident
            </button>
          )}
          <Link href="/dashboard" className="btn btn-primary btn-sm">
            Open dashboard
          </Link>
          <ThemeToggle />
          <button
            type="button"
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-expanded={mobileOpen}
            aria-label="Toggle navigation menu"
            className="btn btn-quiet btn-sm btn-icon lg:hidden"
          >
            {mobileOpen ? <X className="h-4 w-4" aria-hidden="true" /> : <Menu className="h-4 w-4" aria-hidden="true" />}
          </button>
        </div>
      </div>

      {mobileOpen && (
        <nav aria-label="Mobile website sections" className="mx-auto grid max-w-[1180px] gap-1 px-4 pb-4 sm:px-6 lg:hidden">
          {NAV_LINKS.map(({ href, label }) => (
            <a
              key={href}
              href={href}
              onClick={() => setMobileOpen(false)}
              className="rounded-md px-3 py-2.5 text-sm text-ink-2 hover:bg-raise hover:text-ink"
            >
              {label}
            </a>
          ))}
          {onOpenCreate && (
            <button
              type="button"
              onClick={() => {
                setMobileOpen(false);
                onOpenCreate();
              }}
              className="btn btn-quiet mt-2"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create incident
            </button>
          )}
        </nav>
      )}
    </header>
  );
}
