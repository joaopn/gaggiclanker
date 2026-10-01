import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WRITES_ON_SENTENCE } from "@/components/DeviceWritesSwitch";
import { DeviceNotesCard } from "@/components/shots/DeviceNotesCard";
import { SETTINGS_PAGES } from "@/lib/settingsPages";

/**
 * The machine is only ever written with profiles. The copy that describes what
 * the writes switch allows must not send anyone to a feature that is gone.
 */
const GONE = /send(ing)? (your )?(judgement|notes)|clean(ing)? up|deleting shots|storage/i;

describe("copy about what is written to the machine", () => {
  it("the writes switch's confirmation says profiles are all a pull writes", () => {
    expect(WRITES_ON_SENTENCE).toMatch(/profiles match the board/);
    expect(WRITES_ON_SENTENCE).toMatch(/never removes or overwrites a profile of yours/);
    expect(WRITES_ON_SENTENCE).not.toMatch(GONE);
  });

  it("the Machine access subtitle says the same", () => {
    const machine = SETTINGS_PAGES.find((page) => page.id === "machine");
    expect(machine?.description).toMatch(/only thing it ever writes to the machine is a profile/);
    expect(machine?.description ?? "").not.toMatch(GONE);
  });

  it("the device notes card is what was typed on the machine, never sent back", () => {
    render(<DeviceNotesCard notes={{ rating: 4 } as never} />);
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/typed on the machine/);
    expect(text).toMatch(/never sent back/);
    expect(text).not.toMatch(GONE);
  });
});
