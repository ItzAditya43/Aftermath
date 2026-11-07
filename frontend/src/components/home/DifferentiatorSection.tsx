"use client";

import React from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import ScrollReveal from "@/components/common/ScrollReveal";

const COMPARISONS = [
  {
    title: "Keeping failed attempts",
    generic:
      "Post-mortems record only what worked. The next on-call engineer repeats the same restarts, cache purges, and config tweaks.",
    aftermath:
      "Every incident records what failed and why. Remediation plans warn against known dead ends, which saves hours of triage.",
  },
  {
    title: "Ranking backed by evidence",
    generic:
      "A polished explanation is hard to trust when it does not show which observations support it or what is still uncertain.",
    aftermath:
      "Shows candidate causes, linked evidence, score factors, missing checks, and whether a score is a ranking or a calibrated confidence.",
  },
  {
    title: "Knowledge that compounds",
    generic: "Troubleshooting context disappears when incidents close or senior engineers change teams.",
    aftermath:
      "Resolved incidents are recalled by service, signatures, and details. The report shows why each past case was picked.",
  },
];

export default function DifferentiatorSection() {
  return (
    <section id="why-aftermath" className="border-t border-line py-20 sm:py-24">
      <div className="mx-auto max-w-[1180px] px-4 sm:px-6 lg:px-8">
        <ScrollReveal>
          <div className="mb-12 grid items-end gap-6 lg:grid-cols-[5fr_6fr] lg:gap-10">
            <div>
              <span className="label eyebrow">The difference</span>
              <h2 className="section-title mt-3.5">Why memory changes incident response</h2>
            </div>
            <p className="max-w-[52ch] text-base text-ink-2">
              Most tools help you see a failure. Aftermath helps your team remember how they recovered from it.
            </p>
          </div>
        </ScrollReveal>

        <ScrollReveal delay={1}>
          <div className="ledger">
            <div className="hidden grid-cols-[3fr_4fr_4fr] gap-8 border-b border-line py-3.5 lg:grid">
              <span className="label">Problem</span>
              <span className="label">Traditional incident management</span>
              <span className="label !text-gold">The Aftermath memory model</span>
            </div>
            {COMPARISONS.map((item) => (
              <div key={item.title} className="grid gap-3 border-b border-line py-7 lg:grid-cols-[3fr_4fr_4fr] lg:gap-8">
                <h3 className="display text-[26px] leading-[1.12]">{item.title}</h3>
                <div>
                  <span className="label mb-1 block lg:hidden">Traditional</span>
                  <p className="text-[14.5px] text-ink-3 line-through decoration-[color-mix(in_srgb,var(--lily)_45%,transparent)] decoration-1">
                    {item.generic}
                  </p>
                </div>
                <div>
                  <span className="label mb-1 block !text-gold lg:hidden">Aftermath</span>
                  <p className="text-[14.5px] text-ink">{item.aftermath}</p>
                </div>
              </div>
            ))}
          </div>
        </ScrollReveal>

        <ScrollReveal delay={2} className="mt-20">
          <div className="rounded-[10px] bg-ink px-6 py-10 text-paper sm:px-14 sm:py-14">
            <h2 className="display max-w-[16ch] text-[clamp(38px,5.6vw,76px)] leading-none tracking-[-0.025em]">
              Don&apos;t investigate the same failure <em className="text-lily">twice.</em>
            </h2>
            <p className="mt-4 max-w-[52ch] text-[color-mix(in_srgb,var(--paper)_70%,var(--ink))]">
              Give your on-call team a memory of every past outage, and the fix that actually worked.
            </p>
            <div className="mt-7 flex flex-wrap gap-2.5">
              <Link href="/dashboard" className="btn btn-primary">
                Launch Aftermath
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <Link
                href="/incidents"
                className="btn border-[color-mix(in_srgb,var(--paper)_30%,var(--ink))] text-paper hover:bg-[color-mix(in_srgb,var(--paper)_10%,var(--ink))]"
              >
                Inspect all incidents
              </Link>
            </div>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
