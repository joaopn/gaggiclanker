import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DeviceNotesCard } from "@/components/shots/DeviceNotesCard";
import { SETTINGS_PAGES } from "@/lib/settingsPages";
import { DeviceWritesWarning } from "@/pages/settings/RegistryPage";

/**
 * The machine is only ever written with profiles. The copy that describes what
 * "Device writes enabled" allows must not send anyone to a feature that is gone.
 */
const GONE = /send(ing)? (your )?(judgement|notes)|clean(ing)? up|deleting shots|storage/i;

describe("copy about what is written to the machine", () => {
  it("the writes warning says a profile is the only thing written", () => {
    render(
      <MemoryRouter>
        <DeviceWritesWarning />
      </MemoryRouter>,
    );
    const warning = screen.getByTestId("device-writes-warning");
    expect(warning).toHaveTextContent(/only thing this box ever writes to the machine/);
    expect(warning.textContent ?? "").not.toMatch(GONE);
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
