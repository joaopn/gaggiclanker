import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChecksBlock, ShotListRow } from "@/api/types";
import { CompareDrawer } from "@/components/shots/CompareDrawer";
import { checksBlock } from "@/test/claimFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import { LEVER_BADGE, LEVER_WARNINGS } from "@/test/warningFixtures";

// Samples never arrive here: the caption must already be right while they load.
vi.mock("@/hooks/useArchive", () => ({
  useShotSamples: () => ({ isPending: true, data: undefined }),
}));

const row = (
  id: number,
  hasScale: boolean,
  state: ChecksBlock["state"] = "unchecked",
): ShotListRow =>
  ({
    id,
    device_id: `0001${id}`,
    profile_label: `Profile ${id}`,
    started_at: "2026-04-03T12:06:14.000Z",
    duration_ms: 30000,
    volume_g: 36,
    has_scale: hasScale,
    checks: checksBlock({ state }),
  }) as unknown as ShotListRow;

function caption(shots: ShotListRow[]): string {
  renderWithQueryClient(<CompareDrawer shots={shots} onRemove={() => {}} onClose={() => {}} />);
  return screen.getByText(/dashed, on a shared elapsed-time axis/).textContent ?? "";
}

describe("CompareDrawer caption", () => {
  it("names the cup flow from the first paint when every shot had a scale", () => {
    expect(caption([row(1, true), row(2, true)])).toBe(
      "Pressure solid, cup flow dashed, on a shared elapsed-time axis.",
    );
  });

  it("names the puck flow when one of the shots had no scale", () => {
    expect(caption([row(1, true), row(2, false)])).toBe(
      "Pressure solid, puck flow dashed, on a shared elapsed-time axis.",
    );
  });

  it("draws nothing in the Curve check for a shot that passed or one nothing was checked for", () => {
    const withEntries: ShotListRow = {
      ...row(3, true),
      checks: checksBlock({ badge: LEVER_BADGE, entries: LEVER_WARNINGS, state: "entries" }),
    };
    caption([row(1, true, "pass"), row(2, true, "unchecked"), withEntries]);

    expect(screen.queryByTestId("check-pass")).toBeNull();
    expect(screen.queryByTestId("check-unchecked")).toBeNull();
    // The one with entries is drawn, so the empty answers above are not an empty drawer.
    expect(screen.getAllByTestId("check-badge")).toHaveLength(1);
  });
});
