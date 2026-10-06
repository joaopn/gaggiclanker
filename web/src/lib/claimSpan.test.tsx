import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useClaimSpan } from "@/lib/claimSpan";

const span = (reviewId: number, claimId: number, start = 1, end = 2) => ({
  reviewId,
  claimId,
  start,
  end,
});

describe("useClaimSpan", () => {
  it("shows the span under the pointer, else the pinned one", () => {
    const { result } = renderHook(() => useClaimSpan(1));
    act(() => result.current.controls.pin(span(1, 10, 5, 6)));
    expect(result.current.shown).toEqual({ start: 5, end: 6 });
    act(() => result.current.controls.look(span(1, 11, 7, 8)));
    expect(result.current.shown).toEqual({ start: 7, end: 8 });
    act(() => result.current.controls.look(null));
    expect(result.current.shown).toEqual({ start: 5, end: 6 });
    act(() => result.current.controls.pin(span(1, 10)));
    expect(result.current.shown).toBeNull();
  });

  it("never shows a span of a reading that is no longer in force", () => {
    const { result, rerender } = renderHook(({ id }) => useClaimSpan(id), {
      initialProps: { id: 1 },
    });
    act(() => result.current.controls.pin(span(1, 10)));
    act(() => result.current.controls.look(span(1, 11)));
    rerender({ id: 2 });
    expect(result.current.shown).toBeNull();
    expect(result.current.controls.pinnedClaimId).toBeNull();
  });

  describe("pinning", () => {
    const scrolled: Array<[string, unknown]> = [];
    function setup() {
      scrolled.length = 0;
      const chart = document.createElement("div");
      chart.id = "the-chart";
      document.body.appendChild(chart);
      vi.spyOn(chart, "scrollIntoView").mockImplementation(function scroll(
        this: Element,
        arg?: boolean | ScrollIntoViewOptions,
      ) {
        scrolled.push([this.id, arg]);
      });
    }
    afterEach(() => {
      document.getElementById("the-chart")?.remove();
    });

    it("brings the chart into view by the least scrolling when a claim is pinned, at any width", () => {
      setup();
      const { result } = renderHook(() => useClaimSpan(1, "the-chart"));
      act(() => result.current.controls.pin(span(1, 10)));
      expect(scrolled).toEqual([["the-chart", { block: "nearest", behavior: "smooth" }]]);
      // Letting go leaves the reader where they are; pinning another brings it back.
      act(() => result.current.controls.pin(span(1, 10)));
      expect(scrolled).toHaveLength(1);
      act(() => result.current.controls.pin(span(1, 11)));
      expect(scrolled).toHaveLength(2);
    });

    it("does nothing hovering", () => {
      setup();
      const { result } = renderHook(() => useClaimSpan(1, "the-chart"));
      act(() => result.current.controls.look(span(1, 10)));
      expect(scrolled).toEqual([]);
    });

    it("lets a pin go when the scope (the shot) changes, and does not bring it back", () => {
      const { result, rerender } = renderHook(({ shot }) => useClaimSpan(1, undefined, shot), {
        initialProps: { shot: 129 },
      });
      act(() => result.current.controls.pin(span(1, 10, 3, 4)));
      expect(result.current.shown).toEqual({ start: 3, end: 4 });
      rerender({ shot: 130 });
      expect(result.current.shown).toBeNull();
      // Back to the first shot, whose reading in force is the same: still no pin.
      rerender({ shot: 129 });
      expect(result.current.shown).toBeNull();
      expect(result.current.controls.pinnedClaimId).toBeNull();
    });
  });
});
