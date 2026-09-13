import { describe, expect, it } from "vitest";
import { type ExpandedRow, virtualWindow } from "@/hooks/useVirtualRows";

/**
 * The window arithmetic, on numbers. jsdom lays nothing out, so the table's
 * own tests can only see "some rows, not all of them"; whether the spacers add
 * up — which is what keeps a scrollbar honest and rows where the scroll
 * position says they are — is checked here.
 */

const ROW = 50;
const base = { count: 100, rowHeight: ROW, viewport: 500, overscan: 2 };

/** Total height the list claims: both spacers plus the rendered rows and panel. */
function total(
  window: ReturnType<typeof virtualWindow>,
  expanded: ExpandedRow | null = null,
): number {
  const rendered = (window.end - window.start) * ROW;
  const panel =
    expanded !== null && expanded.index >= window.start && expanded.index < window.end
      ? expanded.height
      : 0;
  return window.paddingTop + rendered + panel + window.paddingBottom;
}

describe("virtualWindow", () => {
  it("renders the viewport plus the overscan, with nothing open", () => {
    const window = virtualWindow({ ...base, scrollTop: 1000 });
    // Row 20 is at the top, row 30 at the bottom edge.
    expect(window).toEqual({ start: 18, end: 33, paddingTop: 900, paddingBottom: 3350 });
    expect(total(window)).toBe(100 * ROW);
  });

  it("clamps at both ends of the list", () => {
    expect(virtualWindow({ ...base, scrollTop: 0 }).start).toBe(0);
    const bottom = virtualWindow({ ...base, scrollTop: 100 * ROW - 500 });
    expect(bottom.end).toBe(100);
    expect(bottom.paddingBottom).toBe(0);
  });

  it("puts an open row's panel in the bottom spacer while the row is still below the window", () => {
    const expanded = { index: 80, height: 300 };
    const window = virtualWindow({ ...base, scrollTop: 1000, expanded });
    expect(window.start).toBe(18);
    expect(window.end).toBe(33);
    expect(window.paddingTop).toBe(900);
    expect(window.paddingBottom).toBe(3350 + 300);
    expect(total(window, expanded)).toBe(100 * ROW + 300);
  });

  it("keeps the open row mounted while only its panel is on screen", () => {
    // Row 10 occupies 500..550, its panel 550..1350. Scrolled to 1000, the
    // viewport starts inside the panel: row 10 must still be rendered, or the
    // panel under the reader's eyes would unmount.
    const expanded = { index: 10, height: 800 };
    const window = virtualWindow({ ...base, overscan: 0, scrollTop: 1000, expanded });
    expect(window.start).toBe(10);
    // Row 11 starts where the panel ends, at 1350, so the bottom edge at
    // 1000 + 500 = 1500 is the top of row 14.
    expect(window.end).toBe(15);
    expect(window.paddingTop).toBe(500);
    expect(total(window, expanded)).toBe(100 * ROW + 800);
  });

  it("moves the panel into the top spacer once the open row has scrolled out above", () => {
    const expanded = { index: 5, height: 400 };
    // Row 30 now starts at 30 * 50 + 400 = 1900.
    const window = virtualWindow({ ...base, scrollTop: 1900, expanded });
    expect(window.start).toBe(28);
    expect(window.paddingTop).toBe(28 * ROW + 400);
    expect(window.end).toBe(43);
    expect(total(window, expanded)).toBe(100 * ROW + 400);
  });

  it("keeps the total height constant as the list scrolls past an open row", () => {
    const expanded = { index: 40, height: 650 };
    for (let scrollTop = 0; scrollTop <= 100 * ROW + 650 - 500; scrollTop += 37) {
      const window = virtualWindow({ ...base, scrollTop, expanded });
      expect(total(window, expanded)).toBe(100 * ROW + 650);
      // And the rendered block always covers the viewport.
      const top = window.paddingTop;
      const bottom = total(window, expanded) - window.paddingBottom;
      expect(top).toBeLessThanOrEqual(scrollTop);
      expect(bottom).toBeGreaterThanOrEqual(Math.min(scrollTop + 500, 100 * ROW + 650));
    }
  });

  it("ignores an open row that is not in the list or has not been measured", () => {
    const plain = virtualWindow({ ...base, scrollTop: 1000 });
    expect(
      virtualWindow({ ...base, scrollTop: 1000, expanded: { index: 100, height: 300 } }),
    ).toEqual(plain);
    expect(
      virtualWindow({ ...base, scrollTop: 1000, expanded: { index: -1, height: 300 } }),
    ).toEqual(plain);
    expect(virtualWindow({ ...base, scrollTop: 1000, expanded: { index: 80, height: 0 } })).toEqual(
      plain,
    );
  });

  it("renders nothing for an empty list", () => {
    expect(virtualWindow({ ...base, count: 0, scrollTop: 0 })).toEqual({
      start: 0,
      end: 0,
      paddingTop: 0,
      paddingBottom: 0,
    });
  });
});
