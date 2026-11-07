import React from "react";
import Link from "next/link";
import { BrandMark } from "@/components/common/Status";

export default function HomeFooter() {
  return (
    <footer className="border-t border-line py-10">
      <div className="mx-auto flex max-w-[1180px] flex-wrap items-center justify-between gap-x-10 gap-y-6 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="inline-flex items-center gap-2.5" aria-label="Aftermath homepage">
          <BrandMark />
          <span className="display text-[21px] leading-none">Aftermath</span>
        </Link>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-6 gap-y-2 text-[13.5px] text-ink-3">
          <Link href="/dashboard" className="hover:text-ink">Dashboard</Link>
          <Link href="/incidents" className="hover:text-ink">Incident registry</Link>
          <Link href="/memories" className="hover:text-ink">Memory bank</Link>
          <Link href="/analytics" className="hover:text-ink">Reliability analytics</Link>
          <a href="#how-it-works" className="hover:text-ink">Workflow guide</a>
        </nav>
        <p className="text-[12.5px] text-ink-4">Built for production engineering teams.</p>
      </div>
    </footer>
  );
}
