import { AlertTriangle, ArrowLeft, Download } from "lucide-react";
import { useEffect } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { shotRawUrl } from "@/api/client";
import type { ShotDiagnosticsBlob, ShotPhase } from "@/api/types";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { VersionPrediction } from "@/components/sets/VersionPrediction";
import { AssignToSet } from "@/components/shots/AssignToSet";
import { DeviceNotesCard } from "@/components/shots/DeviceNotesCard";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { ProfileAutomatch } from "@/components/shots/ProfileAutomatch";
import { RatingStars } from "@/components/shots/RatingStars";
import { ReviewCard } from "@/components/shots/ReviewCard";
import { ShotChecksCard } from "@/components/shots/ShotChecksCard";
import { ShotCurvesCard } from "@/components/shots/ShotCurvesCard";
import { ShotPhasesCard } from "@/components/shots/ShotPhasesCard";
import { ShotContextCard, ShotWideCard } from "@/components/shots/ShotWideCards";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useShot, useShotFields, useShotSamples } from "@/hooks/useArchive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useClaimSpan } from "@/lib/claimSpan";
import { ASSIGN_ANCHOR, formatTime, profileName, REVIEW_ANCHOR } from "@/lib/shots";

/**
 * One shot, in full.
 *
 * Composed top to bottom in the order somebody works through a shot: what it
 * was, what you thought of it, what the curves did, what a model's reading of them
 * claims (right under the curves, because each claim points into them), what each
 * diagnostic says, which phase it happened in, which Set it belongs to, what the
 * machine's own notes recorded, and finally the raw header for anybody checking
 * the archive against the device.
 *
 * Code-split (`App.tsx` lazy-loads it) because this is the only route that
 * needs Chart.js, and a visit that only lists shots should not download it.
 */
/** The Curves card's element id: a pinned claim scrolls it into view on a narrow screen. */
const CHART_CARD_ID = "shot-curves";

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
  // What the page's cards, and its facts row, are built from: asked for with the
  // shot, not after it, and waited for with it. The warnings card leads the page
  // and a card that arrives late shifts the judgement under it by its own height.
  const fields = useShotFields(Number.isFinite(shotId) ? shotId : undefined);
  const { hash } = useLocation();
  // What the Reading card marks on the chart: hovering, focusing or pinning a claim. The page
  // holds it because the two cards are siblings; it is above the early returns because it is a hook.
  const claimSpan = useClaimSpan(shot.data?.reading?.in_force_id, CHART_CARD_ID, shotId);

  // The shots list's "needs a Set" menu offers only a few Sets and sends the
  // rest here with `#set`, and a link to a shot's review comes here with
  // `#review`. Both panels are far down a long page, and landing at the top of
  // it would leave the reader to find the thing the link promised. It waits
  // for the shot, because until then the panels do not exist.
  const arrived = shot.isSuccess;
  useEffect(() => {
    const anchor = hash.slice(1);
    // A claim's own anchor (`#claim-12`, from the Checks card) lands on that claim.
    const claim = /^claim-\d+$/.test(anchor);
    if (!arrived || (anchor !== ASSIGN_ANCHOR && anchor !== REVIEW_ANCHOR && !claim)) return;
    document.getElementById(anchor)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [arrived, hash]);

  useQueryErrorToast(shot.error, "Could not load this shot");

  if (shot.isPending || (fields.isPending && !shot.isError)) {
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
  // The facts that the shot-wide numbers repeat are those numbers: the server's
  // own rounding and its own ratio (the judgement's dose, else the version's),
  // never worked out again here.
  const served = (key: string): string => {
    const field = fields.data?.shot.find((item) => item.key === key);
    return field ? field.text : "—";
  };

  return (
    <div className="space-y-4">
      <BackLink />
      <PageHeader
        title={profileName(row)}
        subtitle={`${formatTime(row.started_at)} · shot ${row.device_id}`}
        actions={
          <div className="flex items-center gap-2">
            <RatingStars
              rating={shot.data.judgement?.rating ?? row.rating ?? row.index_rating ?? null}
            />
            {/* The conversation about the version this shot was pulled under,
                so the chat starts on the change this shot is evidence for
                rather than asking what it was an attempt at. */}
            <DiscussButton
              setId={row.set_badge?.set_id ?? null}
              versionId={row.set_version_id ?? null}
              question={`What do you make of shot ${row.id}?`}
            />
            <ProfileAutomatch shotId={row.id} filed={row.set_version_id != null} />
          </div>
        }
      />

      <ShotFacts
        facts={[
          ["Duration", served("shot_time")],
          ["Yield", served("yield")],
          ["Ratio", served("ratio")],
          ["Exit reason", served("exit_reason")],
          ["Scale", row.scale_connected ? "connected" : "not connected"],
          [
            "Brew delay",
            row.brew_delay_ms == null ? "—" : `${(row.brew_delay_ms / 1000).toFixed(2)} s`,
          ],
          ["Source", row.source === "import" ? "imported file" : "device sync"],
        ]}
      />

      {row.quarantined ? <QuarantineNotice reason={row.quarantine_reason} id={row.id} /> : null}

      {/* What is plainly wrong comes first, above everything that asks for a
          verdict: no card at all when there is nothing to say. */}
      <ShotChecksCard checks={fields.data?.checks} signature={fields.data?.signature} />

      {/* What you thought comes first, straight under the facts: recording it
          is what a shot page is opened for, and it should not wait below a
          chart. The reading comes under the curves it points into, made without
          your judgement. */}
      {/* Keyed by the shot: this route is reused across `/shots/:shotId`, and
          a revealed prediction must not survive the change of subject. */}
      <VersionPrediction
        key={row.id}
        shotId={row.id}
        version={shot.data.set_version}
        decision={shot.data.judgement?.decision ?? null}
      />
      <JudgementForm shotId={row.id} judgement={shot.data.judgement} />
      {/* The curves on a row of their own below the judgement, never beside
          it: the chart needs the page's full width to be read. */}
      {!row.quarantined ? (
        <ShotCurvesCard
          shotId={row.id}
          deviceId={row.device_id}
          samples={samples.data}
          pending={samples.isPending}
          phases={phases}
          hasPressure={hasPressure}
          finalExitReason={row.final_exit_reason}
          durationMs={row.duration_ms}
          highlight={claimSpan.shown}
          chartId={CHART_CARD_ID}
        />
      ) : null}

      {/* The reading, straight under the curve its claims point into: hovering a claim marks
          its span there. */}
      {!row.quarantined ? (
        <section id={REVIEW_ANCHOR} className="scroll-mt-20">
          {/* Keyed by the shot: this route is reused across `/shots/:shotId`, and an open "Read
              again?" question, a revealed stance or a request in flight must not carry over to
              the next shot, whose Read again would start a reading nobody asked for. */}
          <ReviewCard
            key={row.id}
            shotId={row.id}
            badge={fields.data?.badge}
            warnings={fields.data?.warnings}
            reviews={shot.data.reviews ?? []}
            reading={shot.data.reading}
            checks={fields.data?.checks}
            span={claimSpan.controls}
            decision={shot.data.judgement?.decision ?? null}
            hasPrediction={Boolean(shot.data.set_version?.prediction)}
          />
        </section>
      ) : null}

      {/* The numbers, straight under the curve they are read against: each
          phase, then the shot as a whole. Every word is the server's. */}
      {!row.quarantined ? (
        fields.isError ? (
          <p className="text-muted-foreground text-sm" data-testid="fields-error">
            Could not load this shot's numbers: {fields.error.message}
          </p>
        ) : fields.data ? (
          <>
            <ShotPhasesCard fields={fields.data} />
            <ShotWideCard fields={fields.data} />
          </>
        ) : null
      ) : null}

      <section id={ASSIGN_ANCHOR} className="scroll-mt-20">
        <AssignToSet
          shotId={row.id}
          setVersion={shot.data.set_version}
          judgement={shot.data.judgement}
        />
      </section>
      {notes ? <DeviceNotesCard notes={notes} /> : null}

      {/* The rest of the shot-wide numbers: context, collapsed, last. */}
      {!row.quarantined && fields.data ? <ShotContextCard fields={fields.data} /> : null}

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
