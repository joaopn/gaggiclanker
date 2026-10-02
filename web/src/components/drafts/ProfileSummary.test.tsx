import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import cremina from "../../../../tests/fixtures/profiles/docs-cremina-lever.json";
import lever from "../../../../tests/fixtures/profiles/firmware-lever.json";
import lmleva from "../../../../tests/fixtures/profiles/firmware-lmleva.json";

type Json = Record<string, unknown>;

function phaseLines(): string[] {
  return screen.getAllByTestId("profile-summary-phase").map((line) => line.textContent ?? "");
}

describe("ProfileSummary", () => {
  it("reads the firmware lever profile phase by phase and says how the shot really ends", () => {
    render(<ProfileSummary profile={lever as Json} />);

    expect(screen.getByTestId("profile-summary")).toHaveTextContent(
      "type pro · 86.5 °C · 5 phases",
    );
    // Only the last phase's targets and duration end the shot; the pressure target in the second
    // phase only ends that phase.
    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends: volumetric ≥ 36, or after 50 s.",
    );
    expect(phaseLines()).toEqual([
      "preinfusion start — 2 s · pump pressure 1.1 bar",
      "preinfusion — 3 s · pump pressure 1.1 bar · ends when pressure ≥ 3",
      "soak — 10 s · pump pressure 1.1 bar",
      "ramp — 10 s · pump pressure 9 bar · transition ease-in-out over 10s, adaptive",
      "ramp-down — 50 s · pump pressure 3 bar · transition linear over 50s, adaptive · ends when volumetric ≥ 36",
    ]);
  });

  it("says a standard profile ends on its volume while the machine measures it", () => {
    render(<ProfileSummary profile={cremina as Json} />);

    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends: volumetric ≥ 36, or after 10 s when volume is not measured.",
    );
    const lines = phaseLines();
    expect(lines).toHaveLength(8);
    expect(lines[0]).toBe("Pre-infusion & Soak — 15 s · pump pressure 1.1 bar");
    // Its duration does not end a phase with a volume target while volume is measured.
    expect(lines[4]).toBe(
      "Ramp down - 8 — 10 s when volume is not measured · pump pressure 8 bar · ends when volumetric ≥ 6",
    );
    expect(lines[3]).toBe("Ramp up - 9 — 2 s · pump pressure 9 bar");
  });

  it("lists every OR-combined target of a phase", () => {
    render(<ProfileSummary profile={lmleva as Json} />);

    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends: pumped ≥ 100 or volumetric ≥ 36, or after 58 s.",
    );
    expect(phaseLines()[1]).toBe(
      "fill — 20 s · pump pressure 1.8 bar · ends when pumped ≥ 100 or pressure ≥ 1",
    );
  });

  it("shows a simple pump as a power percentage, with its unit", () => {
    render(
      <ProfileSummary
        profile={{
          type: "pro",
          temperature: 90,
          phases: [{ name: "Full", duration: 20, pump: 100 }],
        }}
      />,
    );

    expect(phaseLines()).toEqual(["Full — 20 s · pump at 100%"]);
    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends: after 20 s.",
    );
  });

  it("does not show a volumetric target of zero, which the firmware ignores", () => {
    render(
      <ProfileSummary
        profile={{
          type: "pro",
          phases: [
            {
              name: "Brew",
              duration: 30,
              pump: 100,
              targets: [{ type: "volumetric", operator: "gte", value: 0 }],
            },
          ],
        }}
      />,
    );

    expect(phaseLines()[0]).not.toContain("ends when");
    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent("after 30 s.");
  });

  it("copes with a profile that states little", () => {
    render(<ProfileSummary profile={{ phases: [{ duration: 5 }] }} />);

    expect(screen.getByTestId("profile-summary")).toHaveTextContent("1 phase");
    expect(screen.getByTestId("profile-summary")).not.toHaveTextContent("type");
    expect(screen.getByTestId("profile-summary")).not.toHaveTextContent("°C");
    expect(within(screen.getByTestId("profile-summary")).getByText(/Phase 1/)).toBeInTheDocument();
  });

  it("has a sentence for a profile with no phases and for no profile at all", () => {
    const { unmount } = render(<ProfileSummary profile={{ type: "pro", temperature: 93 }} />);
    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends.",
    );
    unmount();

    render(<ProfileSummary profile={null} />);
    expect(screen.getByText("The profile is not available.")).toBeInTheDocument();
  });
});
