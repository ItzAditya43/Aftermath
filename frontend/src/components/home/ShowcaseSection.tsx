"use client";

import React, { useState } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import ScrollReveal from "@/components/common/ScrollReveal";

const PRODUCTS = [
  {
    title: "Executive dashboard",
    short: "Open work and resolution numbers at a glance.",
    description: "See open work, saved resolution metrics, and available incident history from the configured API.",
    specs: [
      ["Incident status", "From API"],
      ["Resolution metrics", "Computed"],
      ["Historical records", "Persisted"],
      ["Data source", "Live API"],
    ],
    href: "/dashboard",
    cta: "Open dashboard",
  },
  {
    title: "Incident registry",
    short: "Every incident, searchable.",
    description: "Search and filter the records available to this environment by severity, service, and symptoms.",
    specs: [
      ["Search", "Titles, alerts, root causes"],
      ["Incident filters", "Status, severity, service"],
      ["Service names", "No fixed list"],
      ["Resolution time", "Recorded"],
    ],
    href: "/incidents",
    cta: "View incident registry",
  },
  {
    title: "Investigation workspace",
    short: "The agent's report, with its evidence.",
    description:
      "Streams the analysis and shows the returned evidence, ranked explanations, the causal graph, and how perspectives agreed, for an engineer to review.",
    specs: [
      ["Explanation score", "Ranking or calibrated"],
      ["Causal graph", "Evidence-linked"],
      ["Remediation", "Steps and cautions"],
      ["Memory comparison", "Match reasons"],
    ],
    href: "/incidents",
    cta: "Open an investigation",
  },
  {
    title: "Memory bank",
    short: "Fixes and failed attempts, kept.",
    description:
      "Every resolved incident adds its confirmed fix, the dead ends, and notes to what the agent can recall.",
    specs: [
      ["Resolution records", "Root cause, fix, notes"],
      ["Failed attempts", "Recorded"],
      ["Search", "Full text"],
      ["Available to agent", "Yes"],
    ],
    href: "/memories",
    cta: "Explore memory bank",
  },
  {
    title: "Reliability analytics",
    short: "Resolution time over months.",
    description:
      "Track mean time to resolve by month, how memory-assisted incidents compare, and every recorded resolution.",
    specs: [
      ["Resolution time", "Grouped by month"],
      ["Memory assisted", "Per resolution"],
      ["Source", "Saved incidents"],
      ["Table view", "Included"],
    ],
    href: "/analytics",
    cta: "Open analytics",
  },
];

export default function ShowcaseSection() {
  const [active, setActive] = useState(2);
  const current = PRODUCTS[active];

  return (
    <section id="showcase" className="border-t border-line py-20 sm:py-24">
      <div className="mx-auto max-w-[1180px] px-4 sm:px-6 lg:px-8">
        <ScrollReveal>
          <div className="mb-12 grid items-end gap-6 lg:grid-cols-[5fr_6fr] lg:gap-10">
            <div>
              <span className="label eyebrow">The platform</span>
              <h2 className="section-title mt-3.5">Five screens, one record</h2>
            </div>
            <p className="max-w-[52ch] text-base text-ink-2">
              Every screen reads from the same saved incidents. The numbers come from the API, not from a mock-up.
            </p>
          </div>
        </ScrollReveal>

        <div className="grid gap-8 lg:grid-cols-[4fr_8fr] lg:gap-10">
          <div
            role="tablist"
            aria-label="Platform screens"
            className="flex gap-0.5 self-start overflow-x-auto [scrollbar-width:none] lg:grid"
          >
            {PRODUCTS.map((p, i) => {
              const selected = i === active;
              return (
                <button
                  key={p.title}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => setActive(i)}
                  className={`grid shrink-0 gap-1 whitespace-nowrap border-b-2 px-4 py-3.5 text-left lg:whitespace-normal lg:border-b-0 lg:border-l-2 ${
                    selected ? "border-lily" : "border-line"
                  }`}
                >
                  <span className={`display text-[22px] leading-[1.1] sm:text-[24px] ${selected ? "text-ink" : "text-ink-3 hover:text-ink"}`}>
                    {p.title}
                  </span>
                  {selected && <span className="hidden text-[13px] text-ink-3 lg:block">{p.short}</span>}
                </button>
              );
            })}
          </div>

          <div role="tabpanel" className="panel p-6 sm:p-7">
            <div className="label">{String(active + 1).padStart(2, "0")} / 05</div>
            <h3 className="display mt-2.5 text-[34px] leading-[1.05]">{current.title}</h3>
            <p className="mt-2.5 max-w-[60ch] text-ink-2">{current.description}</p>
            <dl className="mt-6 grid border-t border-line sm:grid-cols-2 sm:gap-x-8">
              {current.specs.map(([label, value]) => (
                <div key={label} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-3 border-b border-line py-3 text-[13.5px]">
                  <dt className="text-ink-3">{label}</dt>
                  <dd className="m-0 text-right font-mono">{value}</dd>
                </div>
              ))}
            </dl>
            <Link href={current.href} className="btn btn-quiet mt-6">
              {current.cta}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
