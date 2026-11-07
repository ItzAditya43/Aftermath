import React, { Suspense } from "react";
import AppHeader from "@/components/layout/AppHeader";
import InvestigationWorkspace from "@/components/investigation/InvestigationWorkspace";

interface IncidentPageProps {
  params: Promise<{ id: string }>;
}

async function IncidentContent({ params }: IncidentPageProps) {
  const { id } = await params;
  return <InvestigationWorkspace incidentId={id} />;
}

export default function IncidentDetailPage({ params }: IncidentPageProps) {
  return (
    <div className="min-h-screen md:pl-[76px]">
      <AppHeader />
      <main className="mx-auto max-w-[1120px] px-4 pb-20 pt-8 sm:px-6 lg:px-8">
        <Suspense
          fallback={
            <div className="flex min-h-[60vh] items-center justify-center p-8 text-center text-[13px] text-ink-3">
              Loading incident…
            </div>
          }
        >
          <IncidentContent params={params} />
        </Suspense>
      </main>
    </div>
  );
}
