import { type RefObject, useCallback, useEffect, useState } from "react";

/**
 * Which slice of a long list is worth rendering.
 *
 * The archive is the point of this project and it grows for ever: a thousand
 * shots is a normal year, and a thousand table rows — each with a sparkline,
 * a score badge and four badges of its own — is tens of thousands of DOM nodes
 * that the browser lays out on every scroll frame. Only the rows in the
 * viewport are mounted; the rest are two spacer divs.
 *
 * Hand-rolled rather than a virtualiser dependency because the requirement
 * here is narrow — one scroll container, one fixed row height, no dynamic
 * measurement — and a fixed-height window is twenty lines. What it costs is
 * that every row must genuinely be `rowHeight` tall, which is why the row
 * component sets it rather than letting content decide.
 *
 * With one exception, and only one: a row can be open, with a panel below it
 * whose height the caller measures and passes in as `expanded`. One extra
 * block at a known index is still arithmetic — every row above it is where it
 * was, every row below it is that many pixels further down — so it stays
 * arithmetic rather than becoming a measured list. Two open rows would not be;
 * the table allows one.
 */

/**
 * What to assume the container is when the browser will not say.
 *
 * `clientHeight` is 0 before layout and always 0 in jsdom. Rendering nothing
 * in that case would mean a test — and the first paint — sees an empty table,
 * so the fallback is a plausible viewport instead. It is replaced by the real
 * measurement on the first resize or scroll.
 */
export const FALLBACK_VIEWPORT_PX = 720;

export type VirtualWindow = {
  start: number;
  end: number;
  paddingTop: number;
  paddingBottom: number;
};

/** One open row: its index in the list, and the height of the panel under it. */
export type ExpandedRow = { index: number; height: number };

export function useVirtualRows(
  count: number,
  options: {
    rowHeight: number;
    containerRef: RefObject<HTMLElement | null>;
    /** Rows rendered beyond each edge, so a fast scroll does not show blanks. */
    overscan?: number;
    /** The open row, if any. Its panel is in the window arithmetic. */
    expanded?: ExpandedRow | null;
  },
): VirtualWindow {
  const { rowHeight, containerRef, overscan = 6, expanded = null } = options;
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(FALLBACK_VIEWPORT_PX);

  const measure = useCallback(() => {
    const element = containerRef.current;
    if (!element) return;
    setScrollTop(element.scrollTop);
    if (element.clientHeight > 0) setViewport(element.clientHeight);
  }, [containerRef]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    measure();
    element.addEventListener("scroll", measure, { passive: true });
    // The container is a flex child of the page, so it changes height when the
    // window does and when the filter bar wraps onto a second line.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => measure());
    observer?.observe(element);
    return () => {
      element.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, [containerRef, measure]);

  return virtualWindow({ count, rowHeight, scrollTop, viewport, overscan, expanded });
}

/**
 * The window itself, as a pure function of the numbers — exported for its own
 * test, because jsdom has no layout and the arithmetic is the whole behaviour.
 *
 * The open row's panel counts as part of that row: a scroll position inside
 * the panel is "on" the open row, so the row stays mounted while its panel is
 * on screen, and the panel's height lands in `paddingTop` once the row has
 * scrolled out above the window, or in `paddingBottom` while it is still below
 * it. Without that, the spacers would be short by one panel as soon as the open
 * row left the window: the scrollbar would jump, and the rows below it would be
 * rendered a panel's height away from where the scroll position says they are.
 */
export function virtualWindow({
  count,
  rowHeight,
  scrollTop,
  viewport,
  overscan,
  expanded = null,
}: {
  count: number;
  rowHeight: number;
  scrollTop: number;
  viewport: number;
  overscan: number;
  expanded?: ExpandedRow | null;
}): VirtualWindow {
  const open =
    expanded !== null && expanded.index >= 0 && expanded.index < count && expanded.height > 0
      ? expanded
      : null;
  const extra = open?.height ?? 0;
  const openIndex = open?.index ?? -1;

  // Which row covers pixel `y` of the list, the open row's panel included.
  const indexAt = (y: number): number => {
    const openBottom = (openIndex + 1) * rowHeight;
    if (open === null || y < openBottom) return Math.floor(y / rowHeight);
    if (y < openBottom + extra) return openIndex;
    return Math.floor((y - extra) / rowHeight);
  };

  const first = indexAt(Math.max(0, scrollTop));
  const last = indexAt(Math.max(0, scrollTop) + viewport);
  const start = Math.min(count, Math.max(0, first - overscan));
  const end = Math.min(count, Math.max(start, last + 1 + overscan));
  return {
    start,
    end,
    paddingTop: start * rowHeight + (open !== null && openIndex < start ? extra : 0),
    paddingBottom:
      Math.max(0, (count - end) * rowHeight) + (open !== null && openIndex >= end ? extra : 0),
  };
}

/**
 * Whether an element has been on screen yet.
 *
 * Used to defer a row's sparkline fetch until the row is actually looked at:
 * a thousand rows must not mean a thousand requests for a curve nobody sees.
 * "Has been", not "is" — once a row has been seen its sparkline stays, because
 * unmounting a drawn curve only to re-fetch it on the way back up is worse
 * than keeping it.
 *
 * Where `IntersectionObserver` does not exist (jsdom, and very old browsers)
 * everything counts as visible. The fallback is deliberately the eager one:
 * a missing observer must degrade to "the page works", not "the page is blank".
 */
export function useHasBeenVisible(ref: RefObject<Element | null>): boolean {
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (seen) return;
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      // A row's curve is fetched a little before it arrives, so it is drawn by
      // the time it is read rather than a moment after.
      { rootMargin: "200px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, seen]);

  return seen;
}
