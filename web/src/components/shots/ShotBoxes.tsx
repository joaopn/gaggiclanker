import type { ReactNode } from "react";
import type {
  ShotDetailData,
  ShotDiagnosticsBlob,
  ShotFieldsData,
  ShotPhase,
  ShotSamplesData,
} from "@/api/types";
import { VersionPrediction } from "@/components/sets/VersionPrediction";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { inForceClaims, ReviewBox } from "@/components/shots/ReviewBox";
import { ShotChecksCard } from "@/components/shots/ShotChecksCard";
import { ShotCurvesCard } from "@/components/shots/ShotCurvesCard";
import { type ClaimSpanControls, showClaim, useClaimSpan } from "@/lib/claimSpan";
import { setBox, useBox } from "@/lib/shotBoxes";

/**
 * The boxes of a shot, in a fixed order, the same in both places a shot is opened: the shot page
 * and the shots list's open row. One component renders them, so the two cannot drift apart.
 *
 * ```
 *  ▼ Your judgement      open by default
 *    Version prediction  hidden until the shot has a decision; not a box that folds
 *  ▶ Curves              closed by default
 *  ▶ Review              closed by default
 *  ▶ Curve check         closed by default
 * ```
 *
 * Each box's open or closed state is remembered per browser (`lib/shotBoxes`), shared by both
 * places and every shot. Folded, the judgement form and the Review box stay mounted (what was
 * typed, a question asked, a review in flight are still there when they open); the Curves box
 * mounts its chart only while open. A claim pinned in the Review box opens Curves, and a link
 * from the Curve check opens Review: a box is never left folded around the thing a person just
 * asked to see.
 *
 * A quarantined shot has no curve to draw and nothing to review: it keeps the judgement, the
 * prediction and what the Curve check can say, and `whenQuarantined` stands where Curves would.
 */

/** What the callers need beside the boxes: whether Curves is open, and the shared claim span. */
export function useShotBoxState(
  inForceId: number | null | undefined,
  chartId: string,
  scope: number,
): { curvesOpen: boolean; claimSpan: ReturnType<typeof useClaimSpan> } {
  const [curvesOpen, setCurves] = useBox("curves");
  const claimSpan = useClaimSpan(inForceId, chartId, scope, () => {
    if (curvesOpen) return false;
    setCurves(true);
    return true;
  });
  return { curvesOpen, claimSpan };
}

/** Opens the Review box and then brings one of its claims into view and focuses it. */
export function showClaimInReview(claimId: number): void {
  setBox("review", true);
  // The claim is not on screen until the box has opened.
  window.requestAnimationFrame(() => showClaim(claimId));
}

export type SamplesState = {
  data: ShotSamplesData | undefined;
  isPending: boolean;
  isError: boolean;
  error?: Error | null;
};

export function ShotBoxes({
  detail,
  fields,
  samples,
  chartId,
  claimSpan,
  reviewId,
  whenQuarantined,
}: {
  detail: ShotDetailData;
  fields: ShotFieldsData | undefined;
  /** The full curve; only asked for while Curves is open. */
  samples: SamplesState;
  /** The Curves box's element id, which a pinned claim scrolls into view. */
  chartId: string;
  claimSpan: { shown: { start: number; end: number } | null; controls: ClaimSpanControls };
  /** The Review box's element id (the page links to `#review`); the open row has none. */
  reviewId?: string;
  /** Stands where Curves would, for a shot whose bytes never parsed. */
  whenQuarantined?: ReactNode;
}) {
  const [judgementOpen, setJudgement] = useBox("judgement");
  const [curvesOpen, setCurves] = useBox("curves");
  const [reviewOpen, setReview] = useBox("review");
  const [checkOpen, setCheck] = useBox("check");

  const row = detail.shot;
  const diagnostics = (row.diagnostics ?? {}) as ShotDiagnosticsBlob;
  const claims = inForceClaims(detail.reviews, detail.review?.in_force_id);

  return (
    <>
      <JudgementForm
        shotId={row.id}
        judgement={detail.judgement}
        // Where the doses start when the person has recorded none: the Set version's dose (when
        // the shot is filed) and the scale's yield (when it has a scale).
        prefill={{
          doseIn: detail.set_version?.dose_g ?? null,
          doseOut: row.scale_connected ? (row.volume_g ?? null) : null,
        }}
        open={judgementOpen}
        onOpenChange={setJudgement}
      />
      {/* Keyed by the shot: a revealed prediction must not survive a change of subject. */}
      <VersionPrediction
        key={row.id}
        shotId={row.id}
        version={detail.set_version}
        decision={detail.judgement?.decision ?? null}
      />
      {row.quarantined ? (
        whenQuarantined
      ) : samples.isError && curvesOpen ? (
        <p className="text-muted-foreground text-sm" data-testid="curves-error">
          Could not load the curve: {samples.error?.message}
        </p>
      ) : (
        <ShotCurvesCard
          shotId={row.id}
          deviceId={row.device_id}
          samples={samples.data}
          pending={samples.isPending}
          phases={(row.phases ?? []) as ShotPhase[]}
          hasPressure={diagnostics.has_pressure !== false}
          finalExitReason={row.final_exit_reason}
          durationMs={row.duration_ms}
          highlight={claimSpan.shown}
          chartId={chartId}
          open={curvesOpen}
          onOpenChange={setCurves}
        />
      )}
      {row.quarantined ? null : (
        <ReviewBox
          // Keyed by the shot: the page's route is reused across shots, and an open "Review
          // again?" question or a request in flight must not carry over to the next shot.
          key={`review-${row.id}`}
          id={reviewId}
          shotId={row.id}
          reviews={detail.reviews ?? []}
          review={detail.review}
          checks={fields?.checks.items}
          span={claimSpan.controls}
          open={reviewOpen}
          onOpenChange={setReview}
        />
      )}
      <ShotChecksCard
        checks={fields?.checks.items}
        signature={fields?.signature}
        claims={claims}
        onShowClaim={showClaimInReview}
        open={checkOpen}
        onOpenChange={setCheck}
      />
    </>
  );
}
