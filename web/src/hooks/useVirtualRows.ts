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

export function useVirtualRows(
  count: number,
  options: {
    rowHeight: number;
    containerRef: RefObject<HTMLElement | null>;
    /** Rows rendered beyond each edge, so a fast scroll does not show blanks. */
    overscan?: number;
  },
): VirtualWindow {
  const { rowHeight, containerRef, overscan = 6 } = options;
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

  const visibleCount = Math.ceil(viewport / rowHeight) + overscan * 2;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(count, start + visibleCount);
  return {
    start,
    end,
    paddingTop: start * rowHeight,
    paddingBottom: Math.max(0, (count - end) * rowHeight),
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
