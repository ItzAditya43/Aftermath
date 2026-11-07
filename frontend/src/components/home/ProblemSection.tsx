"use client";

import React from "react";
import ScrollReveal from "@/components/common/ScrollReveal";

const PROBLEMS = [
  {
    title: "Scattered diagnostic evidence",
    body: "Alerts, logs, and resolution notes usually live in different places. Aftermath keeps the evidence for an incident next to its analysis and its confirmed outcome.",
    evidence: ["Incident record", "alert · logs · analysis · resolution"],
  },
  {
    title: "Repeating known failed attempts",
    body: "If failed attempts are not recorded with the resolution, the next on-call engineer cannot see what was already tried or how it ended.",
    evidence: ["Seed example · INC-1002", "gateway restart; 502s returned"],
  },
  {
    title: "Unread post-mortem documents",
    body: "A confirmed root cause, the fix that worked, failed attempts, and notes are saved to the incident and recalled when a later case looks similar.",
    evidence: ["Resolution", "what worked · what failed · notes"],
  },
];

export default function ProblemSection() {
  return (
    <section id="the-problem" className="border-t border-line py-20 sm:py-24">
      <div className="mx-auto max-w-[1180px] px-4 sm:px-6 lg:px-8">
        <ScrollReveal>
          <div className="mb-12 grid items-end gap-6 lg:grid-cols-[5fr_6fr] lg:gap-10">
            <div>
              <span className="label eyebrow">The recurring dilemma</span>
              <h2 className="section-title mt-3.5">Why production outages repeat themselves</h2>
            </div>
            <p className="max-w-[52ch] text-base text-ink-2">
              Most teams forget their incidents. The knowledge needed to fix today&apos;s outage was often found three
              months ago and lost three hours later.
            </p>
          </div>
        </ScrollReveal>

        <div className="ledger">
          {PROBLEMS.map((item, idx) => (
            <ScrollReveal key={item.title} delay={((idx % 3) + 1) as 1 | 2 | 3}>
              <div className="grid items-start gap-3 border-b border-line py-7 lg:grid-cols-[4fr_5fr_3.4fr] lg:gap-8">
                <h3 className="display text-[28px] leading-[1.1]">{item.title}</h3>
                <p className="text-ink-2">{item.body}</p>
                <div className="border-l border-line-strong pl-3.5 font-mono text-xs leading-relaxed text-ink-3">
                  {item.evidence[0]}
                  <br />
                  {item.evidence[1]}
                </div>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}
