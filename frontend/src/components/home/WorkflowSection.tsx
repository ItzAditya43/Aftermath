"use client";

import React from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import ScrollReveal from "@/components/common/ScrollReveal";

const WORKFLOW_STEPS = [
  {
    number: "01",
    title: "Capture the incident",
    description: "Create an incident from an alert, a description, and logs, or send the same fields through the API.",
    tag: "Ingestion",
  },
  {
    number: "02",
    title: "Analyze the evidence",
    description:
      "The engine pulls out known signatures, checks for anomalies, and ranks causes against the evidence supplied with the incident.",
    tag: "Analysis",
  },
  {
    number: "03",
    title: "Recall similar past incidents",
    description:
      "Compares service, signatures, and details, then returns related resolved incidents with the reason each one matched.",
    tag: "Memory",
    memory: true,
  },
  {
    number: "04",
    title: "Rank causes and fixes",
    description:
      "Shows competing explanations and the evidence behind each. The score is labelled a ranking until enough graded outcomes support calibration.",
    tag: "Remediation",
  },
  {
    number: "05",
    title: "Record the confirmed resolution",
    description: "Once service is back, engineers record what worked, what failed, and notes for next time.",
    tag: "Verification",
  },
  {
    number: "06",
    title: "Keep what the team learned",
    description:
      "The confirmed fix and the failed attempts are saved with the incident and made available to later investigations.",
    tag: "Learning",
    memory: true,
  },
];

export default function WorkflowSection() {
  return (
    <section id="how-it-works" className="border-t border-line py-20 sm:py-24">
      <div className="mx-auto grid max-w-[1180px] gap-12 px-4 sm:px-6 lg:grid-cols-[4fr_7fr] lg:gap-16 lg:px-8">
        <ScrollReveal className="self-start lg:sticky lg:top-24">
          <span className="label eyebrow">End-to-end workflow</span>
          <h2 className="section-title mt-3.5">How Aftermath guides an investigation</h2>
          <p className="mt-4 max-w-[40ch] text-ink-2">
            From the first alert to a recorded fix, every step keeps its evidence and its uncertainty visible.
          </p>
          <Link href="/incidents" className="btn btn-quiet mt-6">
            Open the incident list
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </ScrollReveal>

        <ScrollReveal delay={1}>
        <ol className="ledger m-0 list-none p-0">
          {WORKFLOW_STEPS.map((step) => (
              <li key={step.number} className="grid grid-cols-[44px_minmax(0,1fr)] items-baseline gap-x-5 gap-y-1 border-b border-line py-6 sm:grid-cols-[64px_minmax(0,1fr)_auto]">
                <span className={`display tabular col-start-1 row-span-2 row-start-1 text-[34px] leading-none sm:row-span-1 ${step.memory ? "text-gold" : "text-ink-4"}`}>
                  {step.number}
                </span>
                {/* Explicit column: without it the text block was auto-placed into the 44px number column on phones. */}
                <div className="col-start-2 row-start-2 min-w-0 sm:row-start-1">
                  <h3 className="text-[17px] font-medium leading-snug">{step.title}</h3>
                  <p className="mt-1.5 max-w-[58ch] text-[14.5px] text-ink-2">{step.description}</p>
                </div>
                <span className="label col-start-2 row-start-1 sm:col-start-3">{step.tag}</span>
              </li>
          ))}
        </ol>
        </ScrollReveal>
      </div>
    </section>
  );
}
