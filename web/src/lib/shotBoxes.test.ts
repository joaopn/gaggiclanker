import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BOXES_KEY, DEFAULT_BOXES, forgetBoxes, parseBoxes, setBox, useBox } from "@/lib/shotBoxes";

describe("parseBoxes", () => {
  it("opens the judgement and folds the rest when nothing is stored", () => {
    expect(parseBoxes(null)).toEqual({
      judgement: true,
      curves: false,
      review: false,
      check: false,
    });
    expect(DEFAULT_BOXES.judgement).toBe(true);
  });

  it("believes a boolean for a box that exists, and nothing else", () => {
    expect(parseBoxes('{"curves":true,"review":"yes","nope":true,"check":1}')).toEqual({
      ...DEFAULT_BOXES,
      curves: true,
    });
    for (const raw of ["{{{", "[]", "null", "7", '"curves"']) {
      expect(parseBoxes(raw), raw).toEqual(DEFAULT_BOXES);
    }
  });
});

describe("useBox", () => {
  it("follows a choice made anywhere, and stores it under one key", () => {
    const a = renderHook(() => useBox("review"));
    const b = renderHook(() => useBox("review"));
    expect(a.result.current[0]).toBe(false);

    act(() => a.result.current[1](true));

    expect(a.result.current[0]).toBe(true);
    expect(b.result.current[0]).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(BOXES_KEY) ?? "{}")).toEqual({
      ...DEFAULT_BOXES,
      review: true,
    });
  });

  it("sees a choice another tab stored", () => {
    const box = renderHook(() => useBox("check"));
    window.localStorage.setItem(BOXES_KEY, JSON.stringify({ ...DEFAULT_BOXES, check: true }));
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: BOXES_KEY }));
    });
    expect(box.result.current[0]).toBe(true);
  });

  it("goes on working for the tab when storage cannot be written", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    const box = renderHook(() => useBox("curves"));
    act(() => setBox("curves", true));
    expect(box.result.current[0]).toBe(true);
    forgetBoxes();
  });

  it("goes back to the defaults when storage is cleared", () => {
    const box = renderHook(() => useBox("judgement"));
    act(() => box.result.current[1](false));
    expect(box.result.current[0]).toBe(false);
    window.localStorage.clear();
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: null }));
    });
    expect(box.result.current[0]).toBe(true);
  });
});
