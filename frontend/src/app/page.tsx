"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Plus } from "lucide-react";
import PublicHeader from "@/components/layout/PublicHeader";
import InteractiveHeroPreview from "@/components/home/InteractiveHeroPreview";
import ProblemSection from "@/components/home/ProblemSection";
import ScrollStoryNarrative from "@/components/home/ScrollStoryNarrative";
import WorkflowSection from "@/components/home/WorkflowSection";
import ShowcaseSection from "@/components/home/ShowcaseSection";
import DifferentiatorSection from "@/components/home/DifferentiatorSection";
import HomeFooter from "@/components/home/HomeFooter";
import CreateIncidentModal from "@/components/incidents/CreateIncidentModal";

export default function HomePage() {
  const router = useRouter();
  const [showCreateModal, setShowCreateModal] = useState(false);

  return (
    <div className="min-h-screen">
      <PublicHeader onOpenCreate={() => setShowCreateModal(true)} />

      <main>
        {/* Hero: the thesis, then the live investigation preview */}
        <section className="relative overflow-hidden pb-10 pt-10 sm:pt-14">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse 50% 45% at 78% 18%, var(--glow), transparent 70%), radial-gradient(ellipse 35% 30% at 92% 55%, var(--glow-2), transparent 70%)",
            }}
          />
          <div className="relative mx-auto max-w-[1180px] px-4 sm:px-6 lg:px-8">
            <div className="label eyebrow rise">Incident intelligence &amp; institutional memory</div>
            <h1 className="display rise mt-5 max-w-[17ch] text-[clamp(44px,7.4vw,96px)] leading-[0.98] tracking-[-0.025em]" style={{ ["--d" as string]: "0.08s" }}>
              Every incident leaves a lesson.{" "}
              <em className="text-ink-3">
                Aftermath makes sure you <span className="hl">remember it.</span>
              </em>
            </h1>

            <div className="rise mt-8 grid items-end gap-8 lg:grid-cols-[minmax(0,1fr)_auto]" style={{ ["--d" as string]: "0.16s" }}>
              <p className="max-w-[56ch] text-[16.5px] text-ink-2">
                Aftermath helps engineering teams investigate system failures, find likely root causes, and reuse what was
                learned from past incidents, so the next investigation starts with what the team already knows.
              </p>
              <div className="flex flex-wrap items-center gap-2.5">
                <Link href="/dashboard" className="btn btn-primary">
                  Explore Aftermath
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Link>
                <button type="button" onClick={() => setShowCreateModal(true)} className="btn btn-quiet">
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  Create incident
                </button>
                <a href="#how-it-works" className="btn-link px-1 text-sm">
                  How it works
                </a>
              </div>
            </div>

            <div className="rise mt-12" style={{ ["--d" as string]: "0.24s" }}>
              <InteractiveHeroPreview />
            </div>
          </div>
        </section>

        <ProblemSection />
        <ScrollStoryNarrative />
        <WorkflowSection />
        <ShowcaseSection />
        <DifferentiatorSection />
      </main>

      <HomeFooter />

      <CreateIncidentModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreated={(created) => {
          router.push(`/incidents/${created.id}`);
        }}
      />
    </div>
  );
}
