import { AlertTriangle, ArrowLeft, Download } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { getShotExport, shotRawUrl } from "@/api/client";
import type { DeviceShotNotes, ShotDiagnosticsBlob, ShotPhase } from "@/api/types";
import { AnalysisPanel } from "@/components/analysis/AnalysisPanel";
import { ShotChart } from "@/components/charts/ShotChart";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { AssignToSet } from "@/components/shots/AssignToSet";
import {
  ChannelingCard,
  ComplianceCard,
  PhaseTable,
  ResistanceCard,
  TemperatureCard,
  WeightCard,
} from "@/components/shots/DiagnosticsCards";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { RatingStars } from "@/components/shots/RatingStars";
import { ScoreBadge } from "@/components/shots/ScoreBadge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useShot, useShotSamples } from "@/hooks/useArchive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { availableSeries, DEFAULT_SERIES, SHOT_SERIES } from "@/lib/shotChart";
import {
  ANALYSIS_ANCHOR,
  ASSIGN_ANCHOR,
  exitReasonLabel,
  formatGrams,
  formatRatio,
  formatSeconds,
  formatTime,
  humanizeKey,
  profileName,
} from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * One shot, in full.
 *
 * Composed top to bottom in the order somebody reads a shot: what it was, what
 * the curves did, how cleanly it was executed, what each diagnostic says, which
 * phase it happened in, what the machine's own notes recorded, and finally the
 * raw header for anybody checking the archive against the device. The judgement,
 * the Set and the analysis sit after the score card — see the comment there.
 *
 * Code-split (`App.tsx` lazy-loads it) because this is the only route that
 * needs Chart.js, and a visit that only lists shots should not download it.
 */
export function ShotDetailPage() {
  const params = useParams();
  const shotId = Number.parseInt(params.shotId ?? "", 10);
  const shot = useShot(Number.isFinite(shotId) ? shotId : undefined);
  // The full curve, not a sparkline: this is the page the samples exist for.
  // Not fetched until the row says it is worth fetching: a quarantined shot
  // has no samples at all, and asking before the row arrives would request
  // them for every one of them.
  const samples = useShotSamples(Number.isFinite(shotId) ? shotId : undefined, {
    enabled: shot.isSuccess && !shot.data.shot.quarantined,
  });
  const [visible, setVisible] = useState<string[]>(DEFAULT_SERIES);
  const { hash } = useLocation();

  // The shots list's "needs a Set" menu offers only a few Sets and sends the
  // rest here with `#set`, and its Analyse column links an analysed shot here
  // with `#analysis`. Both panels are far down a long page, and landing at the
  // top of it would leave the reader to find the thing the link promised. It
  // waits for the shot, because until then the panels do not exist.
  const arrived = shot.isSuccess;
  useEffect(() => {
    const anchor = hash.slice(1);
    if (!arrived || (anchor !== ASSIGN_ANCHOR && anchor !== ANALYSIS_ANCHOR)) return;
    document.getElementById(anchor)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [arrived, hash]);

  useQueryErrorToast(shot.error, "Could not load this shot");

  const rows = useMemo(() => samples.data?.samples ?? [], [samples.data]);
  const present = useMemo(() => availableSeries(rows), [rows]);

  if (shot.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (shot.isError || !shot.data) {
    return (
      <div className="space-y-4">
        <BackLink />
        <EmptyState
          icon={AlertTriangle}
          title="No such shot"
          description={shot.error?.message ?? "The archive has no shot with that id."}
        />
      </div>
    );
  }

  const row = shot.data.shot;
  const notes = shot.data.notes;
  const diagnostics = (row.diagnostics ?? {}) as ShotDiagnosticsBlob;
  const phases = (row.phases ?? []) as ShotPhase[];
  const hasPressure = diagnostics.has_pressure !== false;
  // The judgement's dose first: the machine's notes card is a mirror of what
  // was typed on the machine, and the archive's own copy is the one the user
  // edits here.
  const ratio = formatRatio(
    shot.data.judgement?.dose_in_g ?? notes?.dose_in_g,
    shot.data.judgement?.dose_out_g ?? row.volume_g ?? null,
  );

  return (
    <div className="space-y-4">
      <BackLink />
      <PageHeader
        title={profileName(row)}
        subtitle={`${formatTime(row.started_at)} · shot ${row.device_id}`}
        actions={
          <div className="flex items-center gap-2">
            <ScoreBadge score={row.execution_score ?? null} className="text-sm" />
            <RatingStars
              rating={shot.data.judgement?.rating ?? row.rating ?? row.index_rating ?? null}
            />
            {/* Scoped to the shot's own Set, so the chat starts knowing what
                this shot was an attempt at rather than asking. */}
            <DiscussButton
              setId={row.set_badge?.set_id ?? null}
              question={`What do you make of shot ${row.id}?`}
            />
          </div>
        }
      />

      <ShotFacts
        facts={[
          ["Duration", formatSeconds(row.duration_ms)],
          ["Yield", formatGrams(row.volume_g)],
          ["Ratio", ratio ?? "dose unknown"],
          ["Exit reason", exitReasonLabel(row.final_exit_reason)],
          ["Scale", row.scale_connected ? "connected" : "not connected"],
          [
            "Brew delay",
            row.brew_delay_ms == null ? "—" : `${(row.brew_delay_ms / 1000).toFixed(2)} s`,
          ],
          ["Source", row.source === "import" ? "imported file" : "device sync"],
        ]}
      />

      {row.quarantined ? <QuarantineNotice reason={row.quarantine_reason} id={row.id} /> : null}

      {!row.quarantined ? (
        <SectionCard
          title="Curves"
          description={
            hasPressure
              ? "Actual signals solid, the profile's targets dashed, with the machine's own phase boundaries behind them."
              : undefined
          }
          actions={
            <div className="flex items-center gap-2">
              <DownloadButtons id={row.id} deviceId={row.device_id} />
            </div>
          }
        >
          {samples.isPending ? (
            <Skeleton className="h-72 w-full" />
          ) : rows.length === 0 ? (
            <p className="text-muted-foreground text-sm">This shot has no stored samples.</p>
          ) : (
            <>
              {!hasPressure ? <NoPressureNotice /> : null}
              <SeriesToggles visible={visible} present={present} onChange={setVisible} />
              <ShotChart
                samples={rows}
                phases={phases}
                visible={visible}
                finalExitReason={row.final_exit_reason}
                durationMs={row.duration_ms}
              />
              {samples.data?.sample_interval_ms ? (
                <p className="mt-1 text-muted-foreground text-xs">
                  {rows.length} samples at {samples.data.sample_interval_ms} ms — the header's own
                  interval, not the nominal 250 ms.
                </p>
              ) : null}
            </>
          )}
        </SectionCard>
      ) : null}

      <ExecutionScoreCard row={row} diagnostics={diagnostics} />

      {/* Between the machine's own verdict on the shot and the numbers behind
          it: what you thought, which Set it belongs to, and what the model made
          of the two together. The analysis comes last of the three because it
          reads both — advice given before you have said how it tasted is worth
          markedly less, and the order says so. */}
      <JudgementForm shotId={row.id} judgement={shot.data.judgement} />
      <section id={ASSIGN_ANCHOR} className="scroll-mt-20">
        <AssignToSet
          shotId={row.id}
          setVersion={shot.data.set_version}
          judgement={shot.data.judgement}
        />
      </section>
      {!row.quarantined ? (
        <section id={ANALYSIS_ANCHOR} className="scroll-mt-20">
          <AnalysisPanel
            shotId={row.id}
            analyses={shot.data.analyses ?? []}
            hasSet={shot.data.set_version != null}
            profileVersionId={row.profile_version_id}
          />
        </section>
      ) : null}

      {!row.quarantined ? (
        <div className="grid gap-4 md:grid-cols-2">
          <ResistanceCard diagnostics={diagnostics} />
          <ChannelingCard diagnostics={diagnostics} />
          <TemperatureCard diagnostics={diagnostics} />
          <ComplianceCard diagnostics={diagnostics} />
          <WeightCard diagnostics={diagnostics} />
        </div>
      ) : null}

      {!row.quarantined ? <PhaseTable phases={phases} /> : null}

      {notes ? <NotesPanel notes={notes} /> : null}

      <RawHeaderDetails row={row} />
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/shots"
      className="inline-flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      All shots
    </Link>
  );
}

function ShotFacts({ facts }: { facts: Array<[string, string]> }) {
  return (
    <dl
      className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-border bg-card/40 p-3 sm:grid-cols-4"
      data-testid="shot-facts"
    >
      {facts.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted-foreground text-xs">{label}</dt>
          <dd className="text-sm tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A shot whose bytes we could not read is still a shot.
 *
 * It is in the archive with its raw bytes, because the machine has deleted its
 * copy and a parser fix next month can re-derive it — so the one thing this
 * page must offer is the download.
 */
function QuarantineNotice({ reason, id }: { reason?: string | null; id: number }) {
  return (
    <SectionCard
      title="Quarantined"
      description="The bytes are stored but could not be parsed, so there are no samples and no diagnostics. Nothing is lost: a fix to the parser can re-derive this shot from the file below."
      actions={
        <Button asChild variant="outline" size="sm">
          <a href={shotRawUrl(id)} download>
            <Download className="size-3.5" aria-hidden="true" />
            Download .slog
          </a>
        </Button>
      }
    >
      <p className="font-mono text-sm" data-testid="quarantine-reason">
        {reason ?? "No reason was recorded."}
      </p>
    </SectionCard>
  );
}

function NoPressureNotice() {
  return (
    <p
      className="mb-2 rounded-md border border-border bg-muted/50 p-2 text-muted-foreground text-sm"
      data-testid="no-pressure-notice"
    >
      This machine has no pressure sensor — a Standard board reports a hard zero for pressure and
      flow. The pressure-derived diagnostics did not run rather than reporting confident nonsense.
    </p>
  );
}

function SeriesToggles({
  visible,
  present,
  onChange,
}: {
  visible: string[];
  present: Set<string>;
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="mb-2 flex flex-wrap gap-1.5" data-testid="series-toggles">
      {SHOT_SERIES.map((spec) => {
        const on = visible.includes(spec.key);
        const available = present.has(spec.key);
        return (
          <button
            key={spec.key}
            type="button"
            disabled={!available}
            aria-pressed={on}
            onClick={() =>
              onChange(on ? visible.filter((key) => key !== spec.key) : [...visible, spec.key])
            }
            className={cn(
              "rounded-full border px-2 py-0.5 text-xs transition-colors",
              on ? "border-foreground/30 bg-muted" : "border-border text-muted-foreground",
              !available && "cursor-not-allowed opacity-40",
            )}
            title={available ? undefined : "The firmware never recorded this signal for this shot"}
          >
            <span
              aria-hidden="true"
              className="mr-1 inline-block h-0.5 w-3 align-middle"
              style={{ background: `var(--chart-${spec.color + 1})` }}
            />
            {spec.label}
          </button>
        );
      })}
    </div>
  );
}

function DownloadButtons({ id, deviceId }: { id: number; deviceId: string }) {
  const [busy, setBusy] = useState(false);

  async function downloadJson() {
    setBusy(true);
    try {
      const payload = await getShotExport(id);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `shot-${deviceId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button asChild variant="outline" size="sm">
        <a href={shotRawUrl(id)} download>
          <Download className="size-3.5" aria-hidden="true" />
          .slog
        </a>
      </Button>
      <Button variant="outline" size="sm" onClick={downloadJson} disabled={busy}>
        <Download className="size-3.5" aria-hidden="true" />
        JSON
      </Button>
    </>
  );
}

/**
 * The score, and what it cost.
 *
 * The components come from the diagnostics blob (the shots UI stores them); a shot
 * derived before that has only the number and its one-line reason, which is
 * what the fallback shows.
 */
function ExecutionScoreCard({
  row,
  diagnostics,
}: {
  row: { execution_score?: number | null; execution_reason?: string | null };
  diagnostics: ShotDiagnosticsBlob;
}) {
  const score = diagnostics.score;
  const components = Object.entries(score?.components ?? {});
  return (
    <SectionCard
      title="Execution score"
      description="How cleanly the machine executed this shot. It says nothing about whether the coffee tasted good — that is the cup rating, and conflating the two would let a well-pulled shot of stale beans drag down the diagnostic signal."
      actions={<ScoreBadge score={row.execution_score ?? null} className="text-sm" />}
    >
      <p className="text-sm">{score?.reason ?? row.execution_reason ?? "No score was computed."}</p>
      {score ? (
        <p className="mt-1 text-muted-foreground text-xs">Confidence: {score.confidence}</p>
      ) : null}
      {components.length > 0 ? (
        <ul className="mt-3 space-y-1" data-testid="score-components">
          {components.map(([key, penalty]) => (
            <li key={key} className="flex items-baseline justify-between gap-3 text-sm">
              <span>{humanizeKey(key)}</span>
              <span className="text-status-bad-text tabular-nums">{penalty.toFixed(2)}</span>
            </li>
          ))}
        </ul>
      ) : score ? (
        <p className="mt-2 text-muted-foreground text-sm">
          No penalties: nothing in the telemetry counted against this shot.
        </p>
      ) : null}
    </SectionCard>
  );
}

/**
 * The device's own notes, mirrored.
 *
 * Read-only *here*: this is what the machine holds, and the way to change it is
 * to edit the judgement above and press "Sync notes to machine",
 * which is behind two switches that are both off by default.
 */
function NotesPanel({ notes }: { notes: DeviceShotNotes }) {
  const entries: Array<[string, string]> = [
    ["Bean", notes.bean_type ?? ""],
    ["Dose in", notes.dose_in_g == null ? "" : `${notes.dose_in_g} g`],
    ["Dose out", notes.dose_out_g == null ? "" : `${notes.dose_out_g} g`],
    ["Ratio", notes.ratio == null ? "" : `1:${notes.ratio}`],
    ["Grind", notes.grind_setting ?? ""],
    ["Balance", notes.balance_taste ?? ""],
  ];
  return (
    <SectionCard
      title="Device notes"
      description="What the machine's own notes card holds for this shot. Editable only through the judgement above, and only with notes write-back switched on."
      actions={<RatingStars rating={notes.rating ?? null} />}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3" data-testid="device-notes">
        {entries.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground text-xs">{label}</dt>
            <dd className="text-sm">{value || "—"}</dd>
          </div>
        ))}
      </dl>
      {notes.notes ? <p className="mt-3 whitespace-pre-wrap text-sm">{notes.notes}</p> : null}
    </SectionCard>
  );
}

/** The header fields, for anybody checking the archive against the machine. */
function RawHeaderDetails({
  row,
}: {
  row: {
    slog_version?: number | null;
    sample_interval_ms?: number | null;
    fields_mask?: number | null;
    sample_count: number;
    raw_bytes?: number;
    start_epoch: number;
    profile_id_on_device: string;
    profile_version_id?: number | null;
    synced_at: string;
    updated_at?: string;
  };
}) {
  return (
    <details className="rounded-lg border border-border p-3" data-testid="raw-header">
      <summary className="cursor-pointer text-sm">Raw header</summary>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        {(
          [
            [".slog version", row.slog_version],
            ["Sample interval", row.sample_interval_ms ? `${row.sample_interval_ms} ms` : null],
            ["Fields mask", row.fields_mask == null ? null : `0x${row.fields_mask.toString(16)}`],
            ["Samples", row.sample_count],
            ["Stored bytes", row.raw_bytes],
            ["Start epoch", row.start_epoch],
            ["Profile id on device", row.profile_id_on_device || null],
            [
              "Profile version",
              row.profile_version_id ? (
                <Link
                  key="profile-version"
                  to={`/profiles#version-${row.profile_version_id}`}
                  className="underline underline-offset-2"
                >{`#${row.profile_version_id}`}</Link>
              ) : (
                "not linked"
              ),
            ],
            ["Synced at", formatTime(row.synced_at)],
          ] as Array<[string, React.ReactNode]>
        ).map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground text-xs">{label}</dt>
            <dd className="font-mono text-xs">{value ?? "—"}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
