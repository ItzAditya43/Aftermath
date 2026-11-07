"use client";

import React from "react";
import ScrollReveal from "@/components/common/ScrollReveal";

interface StoryStep {
  number: string;
  badge: string;
  title: string;
  description: string;
  context: string;
  highlight: string;
  turn?: boolean;
}

const STORY_STEPS: StoryStep[] = [
  {
    number: "01",
    badge: "The outage",
    title: "An incident happens.",
    description:
      "An alert arrives with symptoms, service details, and logs. These signals narrow the search, but do not establish a cause by themselves.",
    context: "The investigation begins with evidence submitted by the team. Missing telemetry stays visible as uncertainty.",
    highlight: "Symptoms without clear causes",
  },
  {
    number: "02",
    badge: "The triage",
    title: "An engineer investigates.",
    description:
      "Engineers compare logs, timing, and recent changes, then check whether a suspected failure mode fits the observed symptoms.",
    context:
      "Aftermath ranks candidate explanations and links the evidence and score factors behind each one. It also shows what still needs verification.",
    highlight: "Hours lost to trial and error",
  },
  {
    number: "03",
    badge: "The recovery",
    title: "A solution is found.",
    description:
      "In the included seed data, INC-1002 records a gateway 502 incident caused by an upstream idle-timeout mismatch.",
    context: "That sample record stores the confirmed cause, its fix, failed attempts, and a 145-minute resolution time.",
    highlight: "Resolved, but at high cost",
  },
  {
    number: "04",
    badge: "The loss",
    title: "The lesson is lost.",
    description:
      "If the resolution omits the actions that failed, a later investigation has less context to avoid repeating them.",
    context: "The sample record for INC-1002 notes that restarting gateway pods did not resolve the 502s.",
    highlight: "Knowledge trapped in silos",
  },
  {
    number: "05",
    badge: "The recurrence",
    title: "What if the next investigation already knew what happened?",
    description:
      "Other seeded gateway incidents (INC-1011 and INC-1020) describe the same class of idle-timeout regression and link back to earlier cases.",
    context:
      "When a new case shares relevant evidence, the agent can return these records with match reasons and differences for an engineer to review.",
    highlight: "Continuous memory retrieval",
    turn: true,
  },
  {
    number: "06",
    badge: "The intelligence",
    title: "Aftermath keeps the lesson.",
    description:
      "The seeded sequence records resolution times of 145, 52, and 18 minutes for INC-1002, INC-1011, and INC-1020.",
    context: "Treat these as illustrative seed records. Teams should judge each diagnosis against evidence from their own incidents.",
    highlight: "Memory returned with reasons",
    turn: true,
  },
];

const DECAY = [
  { minutes: 145, id: "INC-1002", month: "May" },
  { minutes: 52, id: "INC-1011", month: "Jul" },
  { minutes: 18, id: "INC-1020", month: "Oct" },
];

export default function ScrollStoryNarrative() {
  return (
    <section id="the-story" className="border-t border-line py-20 sm:py-24">
      <div className="mx-auto max-w-[1180px] px-4 sm:px-6 lg:px-8">
        <ScrollReveal>
          <div className="mb-12 grid items-end gap-6 lg:grid-cols-[5fr_6fr] lg:gap-10">
            <div>
              <span className="label eyebrow">The anatomy of an incident</span>
              <h2 className="section-title mt-3.5">Why teams solve the same problem twice</h2>
            </div>
            <p className="max-w-[52ch] text-base text-ink-2">
              Every production incident teaches something. Without a memory, the lesson disappears when the ticket
              closes. Here is one real sequence from the included seed data.
            </p>
          </div>
        </ScrollReveal>

        <div className="grid gap-12 lg:grid-cols-2 lg:gap-16">
          <ol className="m-0 list-none p-0">
            {STORY_STEPS.map((step) => (
              <li key={step.number} className="grid grid-cols-[44px_minmax(0,1fr)] gap-4 border-b border-line py-6 first:pt-0">
                <span className="font-mono text-xs leading-[1.9] text-ink-4">{step.number}</span>
                <div>
                  <span className={`label ${step.turn ? "!text-gold" : ""}`}>{step.badge}</span>
                  <h3 className={`display mb-1.5 mt-2 text-[26px] leading-[1.15] ${step.turn ? "italic" : ""}`}>{step.title}</h3>
                  <p className="text-[14.5px] text-ink-2">{step.description}</p>
                  <p className="mt-2 text-[14px] text-ink-3">{step.context}</p>
                  <span className={`mt-2.5 inline-block text-[12.5px] ${step.turn ? "text-gold" : "text-lily-ink"}`}>{step.highlight}</span>
                </div>
              </li>
            ))}
          </ol>

          <aside
            aria-label="Resolution times for the api-gateway idle-timeout pattern"
            className="panel self-start p-7 lg:sticky lg:top-24"
          >
            <div className="label">api-gateway · 502 after Friday deploy</div>
            <div className="my-6 grid grid-cols-3 gap-3">
              {DECAY.map((d, i) => (
                <div key={d.id} className={`border-t-2 pt-3 ${i === DECAY.length - 1 ? "border-gold-mark" : "border-line-strong"}`}>
                  <div className={`display tabular text-[clamp(48px,6vw,84px)] leading-[0.9] tracking-[-0.03em] ${i === DECAY.length - 1 ? "text-gold" : ""}`}>
                    {d.minutes}
                  </div>
                  <div className="mt-1.5 font-mono text-xs text-ink-3">minutes</div>
                  <div className="mt-2.5 font-mono text-xs font-medium text-ink-2">
                    {d.id} · {d.month}
                  </div>
                </div>
              ))}
            </div>
            <p className="display text-[21px] italic leading-[1.3] text-ink-2">
              &ldquo;Third time. Proposed a freeze on gateway deploys after 15:00 on Fridays.&rdquo;
            </p>
            <p className="mt-4 border-t border-line pt-3.5 text-[13px] text-ink-3">
              Notes from INC-1020. This example dataset does not prove that memory caused the drop; judge each diagnosis
              against your own incidents.
            </p>
            <a href="#how-it-works" className="btn btn-quiet btn-sm mt-5">
              See how Aftermath works
            </a>
          </aside>
        </div>
      </div>
    </section>
  );
}
