"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { AlertTriangle, ChevronLeft, ChevronRight, ExternalLink, Pause, Play, RotateCcw } from "lucide-react";
import type { CalibrationReport, IncidentDetail, InvestigationReport, MemoryReference, Suggestion } from "@/lib/memory-types";
import InvestigationGraph from "@/components/investigation/InvestigationGraph";

type Props = {
  report: InvestigationReport;
  incident?: IncidentDetail;
  memoryRefs?: MemoryReference[];
  activeSuggestion?: Suggestion | null;
};

function ReportSection({
  title,
  children,
  count,
  tone,
  defaultOpen = true,
}: {
  title: string;
  children: ReactNode;
  count?: number;
  tone?: "memory" | "alert";
  defaultOpen?: boolean;
}) {
  const toneClass = tone === "memory" ? "text-gold" : tone === "alert" ? "text-lily-ink" : "";
  return (
    <details className="panel group" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 [&::-webkit-details-marker]:hidden">
        <h2 className={`display text-[24px] leading-tight ${toneClass}`}>
          {title}
          {count !== undefined && <span className="ml-2 font-mono text-[13px] text-ink-4">{count}</span>}
        </h2>
        <ChevronRight className="h-4 w-4 shrink-0 text-ink-3 transition-transform group-open:rotate-90" aria-hidden="true" />
      </summary>
      <div className="space-y-3 border-t border-line px-5 py-4">{children}</div>
    </details>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 border-l border-line pl-3">
      <div className="label text-[10.5px]">{label}</div>
      <div className="mt-1 break-words text-[13px] text-ink">{children}</div>
    </div>
  );
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function stanceClass(stance: string): string {
  if (stance === "support") return "st-resolved";
  if (stance === "oppose") return "st-open";
  return "";
}

function CalibrationView({ calibration }: { calibration: CalibrationReport }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Fact label="Graded outcomes">{calibration.graded} of {calibration.samples}</Fact>
        <Fact label="Top-1 accuracy">{pct(calibration.top1_accuracy)}</Fact>
        <Fact label="Top-3 accuracy">{pct(calibration.top3_accuracy)}</Fact>
        <Fact label="Brier score">{calibration.brier_score?.toFixed(3) ?? "—"}</Fact>
      </div>
      <p className={`text-[13px] ${calibration.calibrated ? "text-ok" : "text-ink-3"}`}>{calibration.note}</p>
      {calibration.bins.length > 0 && (
        <div>
          <div className="label mb-2 text-[10.5px]">How often the leading cause was right, by score band</div>
          <div className="grid gap-1.5">
            {calibration.bins.map((bin) => (
              <div key={`${bin.low}-${bin.high}`} className="grid grid-cols-[84px_minmax(0,1fr)_86px] items-center gap-3 text-[12px]">
                <span className="font-mono text-ink-3">
                  {bin.low.toFixed(1)}–{bin.high.toFixed(1)}
                </span>
                <div className="h-1.5 overflow-hidden rounded-full bg-raise">
                  <div className="h-full rounded-full bg-ink-2" style={{ width: `${Math.round((bin.accuracy ?? 0) * 100)}%` }} />
                </div>
                <span className="text-right font-mono text-ink-2">
                  {bin.count ? `${pct(bin.accuracy)} of ${bin.count}` : "no data"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {calibration.overconfident_misses.length > 0 && (
        <p className="text-[13px] text-ink-3">
          Scored high but wrong:{" "}
          {calibration.overconfident_misses.map((id, i) => (
            <span key={id}>
              {i > 0 && ", "}
              <Link href={`/incidents/${id}`} className="font-mono text-ink hover:underline">
                {id}
              </Link>
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

export default function InvestigationReportPanel({
  report,
  incident,
  memoryRefs,
  activeSuggestion,
}: Props) {
  const evidence = new Map(report.evidence.map((item) => [item.evidence_id, item]));
  const hypothesisTitle = new Map(report.hypotheses.map((h) => [h.id, h.title]));
  const [replayIndex, setReplayIndex] = useState(0);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!replayPlaying || reducedMotion || report.replay.length < 2) return;
    const timeout = window.setTimeout(() => {
      if (replayIndex >= report.replay.length - 1) setReplayPlaying(false);
      else setReplayIndex((index) => index + 1);
    }, 1800);
    return () => window.clearTimeout(timeout);
  }, [replayIndex, replayPlaying, reducedMotion, report.replay.length]);
  const replay = report.replay[replayIndex];

  const durationMs =
    report.started_at && report.finished_at ? new Date(report.finished_at).getTime() - new Date(report.started_at).getTime() : null;
  const blast = report.blast_radius ?? {};
  const debateByHypothesis = new Map<string, NonNullable<InvestigationReport["debate"]>["stances"]>();
  for (const stance of report.debate?.stances ?? []) {
    const list = debateByHypothesis.get(stance.hypothesis_id) ?? [];
    list.push(stance);
    debateByHypothesis.set(stance.hypothesis_id, list);
  }
  const evaluation = report.resolution_evaluation;

  return (
    <div className="space-y-4" aria-label="Full investigation report">
      {/* Failure chain and engine facts */}
      <section className="panel p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="label">Failure chain</span>
          <div className="flex flex-wrap gap-2">
            <span className="pill">{report.confidence_label}</span>
            <span className="pill font-mono">Top score {report.top_score.toFixed(2)}</span>
          </div>
        </div>
        <p className="display mt-3 text-[clamp(22px,2.6vw,30px)] leading-[1.2]">{report.headline}</p>
        <div className="mt-5 grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          {(
            [
              ["Root candidates", report.chain.root_candidates],
              ["Triggers", report.chain.triggers],
              ["Mechanisms", report.chain.mechanisms],
              ["Symptoms", report.chain.symptoms],
            ] as [string, string[]][]
          ).map(([label, values]) => (
            <div key={label} className="bg-surface p-3.5">
              <h3 className="label text-[10.5px]">{label}</h3>
              <p className="mt-1.5 text-[13px] text-ink-2">{values.length ? values.join(", ") : "None reported"}</p>
            </div>
          ))}
        </div>
        {report.chain.unexplained_symptoms.length > 0 && (
          <p className="mt-3 text-[13px] text-lily-ink">Not yet explained: {report.chain.unexplained_symptoms.join(", ")}</p>
        )}

        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {report.origin_frame && (
            <Fact label="Stack trace points at">
              <span className="font-mono text-[12px]">{report.origin_frame}</span>
            </Fact>
          )}
          <Fact label="Running version">{report.current_version ?? "Not in the alert"}</Fact>
          <Fact label="Blast radius">
            {(blast.directly_affected ?? []).join(", ") || "—"}
            {(blast.possibly_affected_downstream ?? []).length > 0 && (
              <span className="block text-ink-3">Downstream: {blast.possibly_affected_downstream.join(", ")}</span>
            )}
          </Fact>
          <Fact label="Memory">
            {report.memory_consulted ? "Consulted" : "Not consulted"}
            {report.memory_backend && <span className="text-ink-3"> · {report.memory_backend.replace(/_/g, " ")}</span>}
          </Fact>
          <Fact label="AI model use">
            {report.llm && report.llm.calls > 0
              ? `${report.llm.calls} ${report.llm.calls === 1 ? "call" : "calls"} · ${report.llm.providers.join(", ")}`
              : "None, rule-based analysis"}
            {report.llm && report.llm.fallbacks.length > 0 && <span className="block text-ink-3">Fallbacks: {report.llm.fallbacks.join(", ")}</span>}
          </Fact>
          <Fact label="Run time">{durationMs != null ? `${(durationMs / 1000).toFixed(1)} s` : "—"}</Fact>
          {report.phases && report.phases.length > 0 && (
            <div className="min-w-0 sm:col-span-2">
              <div className="label text-[10.5px]">Phases</div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[11.5px] text-ink-3">
                {report.phases.map((phase, i) => (
                  <span key={`${phase}-${i}`} className="flex items-center gap-1.5">
                    {i > 0 && <span className="text-ink-4">→</span>}
                    <span className={i === report.phases!.length - 1 ? "text-ink" : ""}>{phase}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {evaluation && (
        <section className="panel border-ok p-5 sm:p-6" style={{ borderColor: "color-mix(in srgb, var(--ok) 45%, var(--line))" }}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="label !text-ok">How the prediction held up</span>
            <span className={`pill ${evaluation.prediction.verdict === "correct" ? "st-resolved" : evaluation.prediction.verdict === "incorrect" ? "st-open" : "st-investigating"}`}>
              Prediction {evaluation.prediction.verdict}
            </span>
          </div>
          <p className="display mt-3 text-[22px] leading-snug">{evaluation.summary}</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <Fact label="Predicted">{evaluation.prediction.predicted ?? "—"}</Fact>
            <Fact label="Rank of the real cause">{evaluation.prediction.rank_of_actual ?? "Not in the list"}</Fact>
            <Fact label="Outcome">{evaluation.outcome}</Fact>
          </div>
          {evaluation.attempts.length > 0 && (
            <ul className="mt-4 grid gap-1.5 text-[13px]">
              {evaluation.attempts.map((a) => (
                <li key={a.description} className="grid grid-cols-[86px_minmax(0,1fr)] gap-3">
                  <span className={`font-mono text-[11.5px] uppercase ${a.outcome === "verified" ? "text-ok" : a.outcome === "failed" ? "text-lily-ink" : "text-ink-3"}`}>
                    {a.outcome}
                  </span>
                  <span className="text-ink-2">{a.description}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[12.5px] text-ink-3">{evaluation.prediction.explanation}</p>
        </section>
      )}

      <ReportSection title="Ranked hypotheses" count={report.hypotheses.length}>
        {report.hypothesis_comparison && <p className="text-[13.5px] text-ink-2">{report.hypothesis_comparison}</p>}
        {report.hypotheses.length ? (
          report.hypotheses.map((hypothesis, index) => (
            <article key={hypothesis.id} className="rounded-lg border border-line p-4">
              <div className="grid items-center gap-3 sm:grid-cols-[minmax(0,1fr)_200px]">
                <h3 className="text-[15px] font-medium">
                  <span className="mr-2 font-mono text-[12px] text-ink-4">{index + 1}</span>
                  {hypothesis.title}
                </h3>
                <div className="grid grid-cols-[minmax(0,1fr)_40px] items-center gap-2">
                  <div className="h-1.5 overflow-hidden rounded-full bg-raise">
                    <div className={`h-full rounded-full ${index === 0 ? "bg-ink" : "bg-ink-3"}`} style={{ width: `${Math.round(hypothesis.score * 100)}%` }} />
                  </div>
                  <span className="text-right font-mono text-[12px]">{hypothesis.score.toFixed(2)}</span>
                </div>
              </div>
              <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.06em] text-ink-3">{hypothesis.status}</p>
              <p className="mt-2 text-[13.5px] text-ink-2">{hypothesis.description}</p>
              {hypothesis.reasoning && <p className="mt-2 text-[13.5px] text-ink-3">{hypothesis.reasoning}</p>}

              {(debateByHypothesis.get(hypothesis.id) ?? []).length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {(debateByHypothesis.get(hypothesis.id) ?? []).map((s) => (
                    <span key={`${s.perspective}-${s.hypothesis_id}`} className={`pill ${stanceClass(s.stance)}`} title={s.reason}>
                      {s.perspective}: {s.stance}
                    </span>
                  ))}
                </div>
              )}

              <details className="mt-3 text-[13px]">
                <summary className="cursor-pointer text-ink-2 hover:text-ink">Why this score, and the evidence</summary>
                <ul className="mt-2 space-y-1 text-ink-2">
                  {hypothesis.score_breakdown.map((factor, i) => (
                    <li key={`${factor.factor}-${i}`} className="grid grid-cols-[52px_minmax(0,1fr)] gap-2">
                      <span className={`font-mono ${factor.delta >= 0 ? "text-ok" : "text-lily-ink"}`}>
                        {factor.delta > 0 ? "+" : ""}
                        {factor.delta.toFixed(2)}
                      </span>
                      <span>
                        <b className="font-medium text-ink">{factor.factor}</b> · {factor.reason}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <div>
                    <h4 className="label !text-ok text-[10.5px]">Supports it</h4>
                    <ul className="mt-1 list-disc pl-5 text-ink-2">
                      {hypothesis.supporting_evidence_ids.length ? (
                        hypothesis.supporting_evidence_ids.map((id) => <li key={id}>{evidence.get(id)?.summary ?? id}</li>)
                      ) : (
                        <li className="text-ink-3">None</li>
                      )}
                    </ul>
                  </div>
                  <div>
                    <h4 className="label !text-lily-ink text-[10.5px]">Argues against it</h4>
                    <ul className="mt-1 list-disc pl-5 text-ink-2">
                      {hypothesis.contradicting_evidence_ids.length ? (
                        hypothesis.contradicting_evidence_ids.map((id) => <li key={id}>{evidence.get(id)?.summary ?? id}</li>)
                      ) : (
                        <li className="text-ink-3">None</li>
                      )}
                    </ul>
                  </div>
                </div>
                {hypothesis.missing_evidence.length > 0 && <p className="mt-3 text-gold">Still needed: {hypothesis.missing_evidence.join("; ")}</p>}
                {hypothesis.verification_steps.length > 0 && (
                  <ol className="mt-3 list-decimal pl-5 text-ink-2">
                    {hypothesis.verification_steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ol>
                )}
              </details>
            </article>
          ))
        ) : (
          <p className="text-[13px] text-ink-3">No hypotheses were returned.</p>
        )}
      </ReportSection>

      {report.debate && (report.debate.stances.length > 0 || report.debate.disagreements.length > 0) && (
        <ReportSection title="Perspectives" count={report.debate.stances.length} defaultOpen={false}>
          <p className="text-[13px] text-ink-3">
            Each perspective checks the hypotheses independently.{" "}
            {report.debate.consensus_hypothesis_id
              ? `They agree on: ${hypothesisTitle.get(report.debate.consensus_hypothesis_id) ?? report.debate.consensus_hypothesis_id}.`
              : "They did not reach a consensus."}
          </p>
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th scope="col">Perspective</th>
                  <th scope="col">Hypothesis</th>
                  <th scope="col">Stance</th>
                  <th scope="col">Reason</th>
                </tr>
              </thead>
              <tbody>
                {report.debate.stances.map((s, i) => (
                  <tr key={`${s.perspective}-${s.hypothesis_id}-${i}`}>
                    <td className="font-mono text-[12px]">{s.perspective}</td>
                    <td className="max-w-[280px] text-[13px]">
                      <span className="line-clamp-2">{hypothesisTitle.get(s.hypothesis_id) ?? s.hypothesis_id}</span>
                    </td>
                    <td>
                      <span className={`pill ${stanceClass(s.stance)}`}>{s.stance}</span>
                    </td>
                    <td className="text-[13px] text-ink-2">{s.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.debate.disagreements.length > 0 && (
            <ul className="list-disc pl-5 text-[13px] text-lily-ink">
              {report.debate.disagreements.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          )}
        </ReportSection>
      )}

      {report.llm_assessment && (
        <ReportSection title="AI assessment">
          {report.llm_assessment.root_cause_summary && <p className="display text-[20px] leading-snug">{report.llm_assessment.root_cause_summary}</p>}
          <div className="grid gap-4 md:grid-cols-3">
            {(
              [
                ["Contributing factors", report.llm_assessment.contributing_factors],
                ["Symptoms", report.llm_assessment.symptoms],
                ["Decisive evidence", report.llm_assessment.distinguishing_evidence],
              ] as [string, string[]][]
            ).map(([label, items]) => (
              <div key={label}>
                <h3 className="label text-[10.5px]">{label}</h3>
                <ul className="mt-1.5 list-disc pl-5 text-[13px] text-ink-2">
                  {items.length ? items.map((item) => <li key={item}>{item}</li>) : <li className="text-ink-3">None</li>}
                </ul>
              </div>
            ))}
          </div>
          {report.llm_assessment.assessments.length > 0 && (
            <ul className="grid gap-2 text-[13px]">
              {report.llm_assessment.assessments.map((a) => (
                <li key={a.hypothesis_id} className="rounded-md border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="pill">{a.verdict}</span>
                    <span className="font-medium">{hypothesisTitle.get(a.hypothesis_id) ?? a.hypothesis_id}</span>
                  </div>
                  {a.reasoning && <p className="mt-1.5 text-ink-2">{a.reasoning}</p>}
                  {a.missing_evidence.length > 0 && <p className="mt-1 text-gold">Still needed: {a.missing_evidence.join("; ")}</p>}
                </li>
              ))}
            </ul>
          )}
          {report.llm_assessment.new_hypothesis && (
            <p className="text-[13px] text-ink-2">
              <b className="font-medium text-ink">New idea from the model:</b> {report.llm_assessment.new_hypothesis.title}. {report.llm_assessment.new_hypothesis.description}
            </p>
          )}
          {report.llm_assessment.uncertainty && <p className="text-[13px] text-ink-3">Uncertainty: {report.llm_assessment.uncertainty}</p>}
        </ReportSection>
      )}

      <InvestigationGraph
        incident={
          incident ?? {
            id: report.incident_id,
            title: report.headline,
            service: report.graph.nodes.find((n) => n.kind === "service")?.label ?? "system",
            severity: "SEV1",
            status: "investigating",
            alert_text: "",
            logs: "",
            resolution: null,
            failed_attempts: [],
            notes: null,
            last_suggestion: null,
            timeline: [],
            created_at: "",
            resolved_at: null,
            root_cause: null,
            time_to_resolve_minutes: null,
          }
        }
        memoryRefs={memoryRefs}
        activeSuggestion={activeSuggestion}
        report={report}
      />

      <ReportSection title="Similar past incidents" count={report.recalled_incidents.length} tone="memory">
        {report.recalled_incidents.length ? (
          report.recalled_incidents.map((item) => (
            <article key={item.record.incident_id} className="rounded-lg border border-line p-4" style={{ background: "linear-gradient(var(--gold-wash), transparent 80%)" }}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-[14.5px] font-medium">
                  <Link className="mr-2 font-mono text-[12.5px] text-gold hover:underline" href={`/incidents/${item.record.incident_id}`}>
                    {item.record.incident_id}
                  </Link>
                  {item.record.title}
                </h3>
                <span className="font-mono text-[12px] text-gold">{Math.round(item.relevance * 100)}% match</span>
              </div>
              <p className="mt-2 text-[13px] text-ink-2">
                <b className="font-medium text-ink">Root cause:</b> {item.record.root_cause ?? "Not recorded"}
              </p>
              <p className="mt-1 text-[13px] text-ink-2">
                <b className="font-medium text-ok">Fix:</b> {item.record.resolution ?? "Not recorded"}
              </p>
              <p className="mt-1 text-[13px] text-ink-3">Matched because {item.matching_points.join("; ") || "the engine ranked it as similar"}</p>
              {item.differences.length > 0 && <p className="mt-1 text-[13px] text-ink-3">Differences: {item.differences.join("; ")}</p>}
              {item.record.attempts.filter((a) => a.outcome !== "verified").length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-[13px] text-lily-ink">
                  {item.record.attempts
                    .filter((attempt) => attempt.outcome !== "verified")
                    .map((attempt) => (
                      <li key={attempt.description}>Tried and failed: {attempt.description}</li>
                    ))}
                </ul>
              )}
            </article>
          ))
        ) : (
          <p className="text-[13px] text-ink-3">No similar past incident was recalled.</p>
        )}
        {report.lessons.length > 0 && (
          <div className="border-t border-line pt-3">
            <h3 className="label text-[10.5px]">Lessons carried over</h3>
            <ul className="mt-2 space-y-1.5 text-[13px] text-ink-2">
              {report.lessons.map((lesson, i) => (
                <li key={`${lesson.kind}-${i}`}>
                  <span className="mr-2 font-mono text-[11px] uppercase text-ink-3">{lesson.kind.replace(/_/g, " ")}</span>
                  {lesson.message} <span className="font-mono text-[12px] text-gold">{lesson.incident_ids.join(", ")}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </ReportSection>

      <ReportSection title="Recommended remediation" count={report.remediation.steps.length}>
        {report.remediation.steps.map((step, i) => (
          <article key={step.id} className="grid grid-cols-[28px_minmax(0,1fr)] gap-2 border-b border-line pb-3 last:border-b-0 last:pb-0">
            <span className="font-mono text-[12px] text-ink-4">{String(i + 1).padStart(2, "0")}</span>
            <div>
              <h3 className="text-[14px] font-medium">{step.description}</h3>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <span className="pill">{step.kind.replace(/_/g, " ")}</span>
                {step.requires_approval && <span className="pill st-investigating">Needs approval</span>}
                {step.temporary && <span className="pill">Temporary</span>}
              </div>
              <p className="mt-1.5 text-[13px] text-ink-2">{step.reason}</p>
              {step.expected_effect && <p className="mt-1 text-[13px] text-ink-3">Expected: {step.expected_effect}</p>}
              {step.verification && <p className="mt-1 text-[13px] text-ok">Check: {step.verification}</p>}
              {step.rollback && <p className="mt-1 text-[13px] text-ink-3">Rollback: {step.rollback}</p>}
            </div>
          </article>
        ))}
        {(report.remediation.warnings.length > 0 || report.remediation.blocked_actions.length > 0) && (
          <div className="callout-avoid">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-lily-ink" aria-hidden="true" />
            <div>
              <strong>Cautions and blocked actions.</strong>
              <ul className="mt-1 list-disc pl-5">
                {report.remediation.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
                {report.remediation.blocked_actions.map((b) => (
                  <li key={b}>Blocked: {b}</li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </ReportSection>

      {((report.signatures ?? []).length > 0 || report.log_stats) && (
        <ReportSection title="What the logs show" count={report.signatures?.length} defaultOpen={false}>
          {report.log_stats && (
            <div className="grid gap-4 sm:grid-cols-4">
              <Fact label="Log events">{report.log_stats.events}</Fact>
              <Fact label="By severity">
                {Object.entries(report.log_stats.severity_counts)
                  .map(([k, v]) => `${v} ${k}`)
                  .join(" · ") || "—"}
              </Fact>
              <Fact label="Formats">
                {Object.entries(report.log_stats.formats)
                  .map(([k, v]) => `${v} ${k}`)
                  .join(" · ") || "—"}
              </Fact>
              <Fact label="Dropped">
                {report.log_stats.malformed_lines} malformed · {report.log_stats.duplicates} duplicate
              </Fact>
            </div>
          )}
          {(report.signatures ?? []).length > 0 && (
            <div className="overflow-x-auto">
              <table className="tbl">
                <thead>
                  <tr>
                    <th scope="col">Signature</th>
                    <th scope="col">Role</th>
                    <th scope="col" className="!text-right">Seen</th>
                    <th scope="col">Example</th>
                  </tr>
                </thead>
                <tbody>
                  {report.signatures!.map((sig) => (
                    <tr key={sig.id}>
                      <td>
                        <span className="font-medium">{sig.label}</span>
                        <span className="block font-mono text-[11px] text-ink-3">{sig.category.replace(/_/g, " ")}</span>
                      </td>
                      <td>
                        <span className={`pill ${sig.role === "root" ? "st-open" : ""}`}>{sig.role}</span>
                      </td>
                      <td className="num">{sig.count}×</td>
                      <td className="max-w-[420px] font-mono text-[11.5px] text-ink-3">
                        <span className="line-clamp-2">{sig.snippet}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {report.log_stats && report.log_stats.top_error_groups.length > 0 && (
            <div>
              <h3 className="label text-[10.5px]">Most frequent errors</h3>
              <ul className="mt-2 space-y-1.5 text-[13px]">
                {report.log_stats.top_error_groups.map((g) => (
                  <li key={g.fingerprint} className="grid grid-cols-[40px_minmax(0,1fr)] gap-2">
                    <span className="font-mono text-ink-3">{g.count}×</span>
                    <span className="break-words font-mono text-[12px] text-ink-2">{g.exception_type ?? g.fingerprint}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </ReportSection>
      )}

      {(report.timeline ?? []).length > 0 && (
        <ReportSection title="Timeline" count={report.timeline!.length} defaultOpen={false}>
          <ol className="m-0 list-none space-y-1 p-0">
            {report.timeline!.map((entry, i) => (
              <li key={`${entry.at}-${i}`} className="grid grid-cols-[72px_12px_minmax(0,1fr)] gap-3 py-1 text-[13px]">
                <span className="font-mono text-[12px] text-ink-3">{formatTime(entry.at)}</span>
                <span
                  className={`mt-1.5 h-2 w-2 rounded-full ${entry.kind === "alert" || entry.kind === "first_error" ? "bg-lily" : entry.kind === "change" ? "bg-gold-mark" : "bg-ink-4"}`}
                  aria-hidden="true"
                />
                <span className="min-w-0 text-ink-2">
                  <span className="mr-2 font-mono text-[11px] uppercase text-ink-3">{entry.kind.replace(/_/g, " ")}</span>
                  {entry.summary}
                </span>
              </li>
            ))}
          </ol>
        </ReportSection>
      )}

      <ReportSection title="Evidence" count={report.evidence.length} defaultOpen={false}>
        {report.evidence.length ? (
          report.evidence.map((item) => (
            <article key={item.evidence_id} className="border-b border-line pb-2.5 text-[13px] last:border-b-0">
              <p className="text-ink-2">
                <span className="pill mr-2">{item.kind.replace(/_/g, " ")}</span>
                {item.summary}
              </p>
              <p className="mt-1 font-mono text-[11px] text-ink-4">
                {item.evidence_id} · {item.source}
              </p>
            </article>
          ))
        ) : (
          <p className="text-[13px] text-ink-3">No evidence entries were returned.</p>
        )}
      </ReportSection>

      <ReportSection title="Anomalies" count={report.anomalies.length} defaultOpen={false}>
        {report.anomalies.length ? (
          report.anomalies.map((item, i) => (
            <p key={`${item.kind}-${i}`} className="text-[13px] text-ink-2">
              <span className={`pill mr-2 ${item.severity === "high" ? "st-open" : ""}`}>{item.severity}</span>
              {item.summary} <span className="font-mono text-[11px] text-ink-4">{item.method}</span>
            </p>
          ))
        ) : (
          <p className="text-[13px] text-ink-3">No anomalies reported.</p>
        )}
      </ReportSection>

      <ReportSection title="Change suspects" count={report.change_suspects.length} defaultOpen={false}>
        {report.change_suspects.length ? (
          report.change_suspects.map((item) => (
            <article key={item.change.id} className="rounded-lg border border-line p-3 text-[13px]">
              <h3 className="font-medium">
                <span className="pill mr-2">{item.change.kind}</span>
                {item.change.title}
              </h3>
              <p className="mt-1 text-ink-2">
                Score {item.score.toFixed(2)} · {item.reasons.join("; ")}
              </p>
              {item.matched_files.length > 0 && <p className="mt-1 font-mono text-[12px] text-ink-3">Files: {item.matched_files.join(", ")}</p>}
              {item.change.url && (
                <a className="btn-link mt-2 inline-flex items-center gap-1.5" href={item.change.url} target="_blank" rel="noreferrer">
                  Open change <ExternalLink size={13} aria-hidden="true" />
                </a>
              )}
            </article>
          ))
        ) : (
          <p className="text-[13px] text-ink-3">No change suspects were returned. Connect a deploy or Git source to see them.</p>
        )}
      </ReportSection>

      {(report.diagnostics ?? []).length > 0 && (
        <ReportSection title="Diagnostic checks" count={report.diagnostics!.length} defaultOpen={false}>
          {report.diagnostics!.map((d, i) => (
            <article key={`${d.tool_name}-${i}`} className="rounded-lg border border-line p-3 text-[13px]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{d.tool_name}</span>
                <span className={`pill ${d.status === "executed" ? "st-resolved" : d.status === "failed" ? "st-open" : "st-investigating"}`}>
                  {d.status.replace(/_/g, " ")}
                </span>
                <span className="pill">risk {d.risk}</span>
              </div>
              <p className="mt-1.5 text-ink-2">{d.purpose}</p>
              {d.result_summary && <p className="mt-1 text-ink-3">{d.result_summary}</p>}
            </article>
          ))}
        </ReportSection>
      )}

      <ReportSection title="Investigation replay" count={report.replay.length} defaultOpen={false}>
        {replay ? (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-[12px] text-ink-3" aria-live="polite">
                Step {replayIndex + 1} of {report.replay.length}
              </span>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  className="btn btn-quiet btn-sm"
                  onClick={() => {
                    setReplayPlaying(false);
                    setReplayIndex((i) => Math.max(0, i - 1));
                  }}
                  disabled={replayIndex === 0}
                  aria-label="Previous replay step"
                >
                  <ChevronLeft className="h-4 w-4" aria-hidden="true" /> Previous
                </button>
                <button
                  type="button"
                  className="btn btn-quiet btn-sm"
                  onClick={() => {
                    if (reducedMotion) {
                      setReplayIndex((i) => Math.min(report.replay.length - 1, i + 1));
                      return;
                    }
                    setReplayPlaying((playing) => !playing);
                  }}
                  aria-label={replayPlaying ? "Pause investigation replay" : "Play investigation replay"}
                >
                  {replayPlaying ? <Pause className="h-4 w-4" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
                  {replayPlaying ? "Pause" : "Play"}
                </button>
                <button
                  type="button"
                  className="btn btn-quiet btn-sm btn-icon"
                  onClick={() => {
                    setReplayPlaying(false);
                    setReplayIndex(0);
                  }}
                  aria-label="Replay investigation from the beginning"
                >
                  <RotateCcw className="h-4 w-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="btn btn-quiet btn-sm"
                  onClick={() => {
                    setReplayPlaying(false);
                    setReplayIndex((i) => Math.min(report.replay.length - 1, i + 1));
                  }}
                  disabled={replayIndex === report.replay.length - 1}
                  aria-label="Next replay step"
                >
                  Next <ChevronRight className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            </div>
            <div className="mt-3 h-1 overflow-hidden rounded-full bg-raise">
              <div className="h-full rounded-full bg-ink-2 transition-[width] duration-300" style={{ width: `${((replayIndex + 1) / report.replay.length) * 100}%` }} />
            </div>
            <h3 className="mt-4 text-[15px] font-medium">
              <span className="mr-2 font-mono text-[11px] uppercase text-ink-3">{replay.phase}</span>
              {replay.kind.replace(/_/g, " ")}
            </h3>
            <p className="mt-1 text-[13.5px] text-ink-2">{replay.summary}</p>
            <p className="mt-1 font-mono text-[11.5px] text-ink-4">
              {formatTime(replay.at)} · {replay.duration_ms} ms · {replay.status}
            </p>
          </div>
        ) : (
          <p className="text-[13px] text-ink-3">No replay timeline was returned.</p>
        )}
      </ReportSection>

      {report.calibration && (
        <ReportSection title="How far to trust the score" defaultOpen={false}>
          <CalibrationView calibration={report.calibration} />
        </ReportSection>
      )}

      {report.degraded.length > 0 && (
        <div className="callout-avoid">
          <AlertTriangle className="mt-0.5 h-4 w-4 text-lily-ink" aria-hidden="true" />
          <div>
            <strong>Limitations of this run</strong>
            <ul className="mt-1 list-disc pl-5">
              {report.degraded.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
