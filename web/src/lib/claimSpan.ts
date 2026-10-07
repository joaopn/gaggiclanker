import { useCallback, useMemo, useRef, useState } from "react";

/** The span of one claim, in seconds into the shot. `reviewId` is the reading it belongs to. */
export type ClaimSpan = { reviewId: number; claimId: number; start: number; end: number };

/**
 * What the Reading card does with the chart: hover or focus a claim and its span is drawn, a
 * click pins it so a phone (which has no hover) can see it, and a second click lets it go. One
 * span at a time: the one under the pointer, else the pinned one.
 */
export type ClaimSpanControls = {
  /** Called with the span of the claim under the pointer or focus, and `null` when it leaves. */
  look: (span: ClaimSpan | null) => void;
  /** Pins the span, or lets it go when it is the pinned one already. */
  pin: (span: ClaimSpan) => void;
  /** The claim whose span is pinned, for a pressed state on its button. */
  pinnedClaimId: number | null;
};

/**
 * The shared state of the shot page: the Reading card writes it, the Curves card reads it.
 *
 * `inForceId` is the reading whose claims can be on screen: a span that belongs to an older
 * reading (a re-read has just replaced it) is never drawn, so a stale box cannot outlive its
 * claim.
 */
export function useClaimSpan(
  inForceId: number | null | undefined,
  chartId?: string,
  scope?: string | number,
): {
  shown: { start: number; end: number } | null;
  controls: ClaimSpanControls;
} {
  const [looked, setLooked] = useState<ClaimSpan | null>(null);
  const [pinned, setPinned] = useState<ClaimSpan | null>(null);
  // A pin belongs to one shot. The shot page's route is reused across shots and a cached shot
  // comes back with the same reading in force, so a pin kept in state would return with it: the
  // scope's identity is held in state and the span let go when it changes, during render.
  const [scopeSeen, setScopeSeen] = useState(scope);
  if (scopeSeen !== scope) {
    setScopeSeen(scope);
    setPinned(null);
    setLooked(null);
  }
  const pinnedRef = useRef<ClaimSpan | null>(null);
  pinnedRef.current = pinned;
  const look = useCallback((span: ClaimSpan | null) => setLooked(span), []);
  const pin = useCallback(
    (span: ClaimSpan) => {
      setPinned((current) => (current?.claimId === span.claimId ? null : span));
      // The chart may be anywhere but in view: a column of claims below it on a phone, a tall
      // card with the chart 880 px above on a desktop. A pin that cannot be seen is no pin, so
      // the chart is brought into view, by the least scrolling (`nearest`: none when it is
      // already there), at any width. Only the press that pins; letting a span go leaves the
      // reader where they are.
      if (pinnedRef.current?.claimId !== span.claimId) revealChart(chartId);
    },
    [chartId],
  );
  const current = [looked, pinned].find((span) => span && span.reviewId === inForceId) ?? null;
  const pinnedClaimId = pinned && pinned.reviewId === inForceId ? pinned.claimId : null;
  const shown = current ? { start: current.start, end: current.end } : null;
  const controls = useMemo(() => ({ look, pin, pinnedClaimId }), [look, pin, pinnedClaimId]);
  return { shown, controls };
}

/** Scrolls the chart card into view by the least distance, and not at all when it is in view. */
function revealChart(chartId: string | undefined): void {
  if (!chartId || typeof document === "undefined") return;
  document.getElementById(chartId)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/**
 * Brings one claim of the Review box into view and puts focus on it: the least scrolling that shows
 * it (`nearest`, none when it is in view already). The caller has opened the box first.
 */
export function showClaim(claimId: number): void {
  if (typeof document === "undefined") return;
  const element = document.getElementById(`claim-${claimId}`);
  if (!element) return;
  element.scrollIntoView({ block: "nearest", behavior: "smooth" });
  element.focus({ preventScroll: true });
}
