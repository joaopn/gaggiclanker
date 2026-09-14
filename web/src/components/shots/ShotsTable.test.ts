import { describe, expect, it } from "vitest";
import { revealDistance } from "@/components/shots/ShotsTable";

describe("revealDistance", () => {
  // A list from 100 to 600 on screen, its sticky header ending at 130.
  const list = { listTop: 130, listBottom: 600 };

  it("does not scroll when the panel already fits", () => {
    expect(revealDistance({ ...list, rowTop: 200, panelBottom: 550 })).toBe(0);
  });

  it("scrolls just enough to show the bottom of a panel that overflows", () => {
    expect(revealDistance({ ...list, rowTop: 400, panelBottom: 680 })).toBe(80);
  });

  it("stops with the row just under the header when the panel is taller than the list", () => {
    // Showing the whole panel would take 900 px and put the row off the top.
    expect(revealDistance({ ...list, rowTop: 450, panelBottom: 1500 })).toBe(320);
  });

  it("never scrolls up, even for a row already partly under the header", () => {
    expect(revealDistance({ ...list, rowTop: 110, panelBottom: 900 })).toBe(0);
  });
});
