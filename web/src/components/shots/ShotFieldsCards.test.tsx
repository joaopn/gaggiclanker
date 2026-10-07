import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotChecksCard } from "@/components/shots/ShotChecksCard";
import { ShotPhasesCard } from "@/components/shots/ShotPhasesCard";
import { ShotContextCard, ShotWideCard } from "@/components/shots/ShotWideCards";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import {
  leverFields,
  leverNoPressureFields,
  leverNoScaleFields,
  realFields,
} from "@/test/shotFieldsFixture";

/**
 * The shot page's cards, against documents the server really serves
 * (`scripts/build_web_shot_fields_fixture.py`): a constructed lever shot built
 * from a real fixture, the same shot as a machine with no scale and with no
 * pressure sensor record it (channels zeroed, flag cleared), and the exported
 * shot. Their absent fields are absent in the document, never zero.
 */

describe("ShotChecksCard", () => {
  it("says each warning as phase: fault with its sentence, most severe first as served", () => {
    renderWithQueryClient(
      <ShotChecksCard checks={leverFields.checks.items} signature={leverFields.signature} />,
    );

    const lines = screen.getAllByTestId("check-line");
    expect(lines.map((line) => line.querySelector("p")?.textContent)).toEqual([
      "ramp: fast flow",
      "decline: skipped",
      "Shot: over target",
    ]);
    expect(lines[1]).toHaveTextContent("before decline began");
    expect(lines[0]).toHaveAttribute("data-severity", "amber");
  });

  it("draws no card, and no all-clear, for a shot with no checks and no profile", () => {
    const { container } = renderWithQueryClient(
      <ShotChecksCard checks={realFields.checks.items} signature={realFields.signature} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(
      renderWithQueryClient(<ShotChecksCard checks={undefined} signature={undefined} />).container,
    ).toBeEmptyDOMElement();
  });
});

describe("ShotPhasesCard", () => {
  it("has a row per phase of the shot, in its order, with how each ended", () => {
    render(<ShotPhasesCard fields={leverFields} />);

    const rows = screen.getAllByTestId("phase-row");
    expect(rows.map((row) => row.querySelector("td")?.textContent)).toEqual([
      "preinfusionpreinfusion",
      "soakpreinfusion",
      "rampbrew",
    ]);
    expect(within(rows[2]).getByTestId("phase-cell-ended")).toHaveTextContent("Volumetric target");
    expect(within(rows[0]).getByTestId("phase-cell-ended")).toHaveTextContent("Duration");
  });

  it("orders the columns by what matters: the phase, how it ended, the cup, then the rest", () => {
    render(<ShotPhasesCard fields={leverFields} />);

    const heads = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(heads.slice(0, 4)).toEqual(["Phase", "Ended by", "Cup at end", "Start"]);
    expect(heads.indexOf("Pressure")).toBeGreaterThan(heads.indexOf("Cup at end"));
    expect(heads.indexOf("Firmware analyzer")).toBeGreaterThan(heads.indexOf("Cup at end"));
    const row = screen.getAllByTestId("phase-row")[2];
    const cells = Array.from(row.children).map((td) => td.getAttribute("data-testid"));
    expect(cells.slice(1, 4)).toEqual(["phase-cell-ended", "phase-cell-cup", "phase-cell-timing"]);
  });

  it("puts the cup at the end of the ramp, with its share of the target, in the ramp's row", () => {
    render(<ShotPhasesCard fields={leverFields} />);

    const ramp = screen.getAllByTestId("phase-row")[2];
    const cup = within(ramp).getByTestId("phase-cell-cup");
    expect(cup).toHaveTextContent("42.2 g");
    expect(within(cup).getByTestId("phase-phase_cup_share")).toHaveTextContent("117.2 %");
    expect(within(cup).getByTestId("phase-phase_cup_gained")).toHaveTextContent("38.2 g");
  });

  it("marks a phase the shot never reached as its own row, after the ones it reached", () => {
    render(<ShotPhasesCard fields={leverFields} />);

    const body = screen.getAllByTestId("phase-row")[0].parentElement as HTMLElement;
    const rows = Array.from(body.children);
    expect(rows).toHaveLength(4);
    const last = rows[3];
    expect(last).toHaveAttribute("data-testid", "phase-not-reached");
    expect(last).toHaveTextContent("decline");
    expect(last).toHaveTextContent("not reached");
    // The share is only the reached phases' to show.
    expect(last).not.toHaveTextContent("%");
  });

  it("lists the unreached phases in the profile's order", () => {
    // The served list is in the profile's order; two names, one with a comma.
    const fields = {
      ...leverFields,
      shot: leverFields.shot.map((field) =>
        field.key === "phases_not_reached"
          ? {
              ...field,
              value: [
                { phase_number: 3, name: "decline, slow" },
                { phase_number: 4, name: "tail" },
              ],
            }
          : field,
      ),
    };
    render(<ShotPhasesCard fields={fields} />);

    const unreached = screen.getAllByTestId("phase-not-reached");
    expect(unreached.map((row) => row.querySelector("td")?.textContent)).toEqual([
      "decline, slow",
      "tail",
    ]);
  });

  it("tells two unreached phases of the same name apart by their number", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fields = {
      ...leverFields,
      shot: leverFields.shot.map((field) =>
        field.key === "phases_not_reached"
          ? {
              ...field,
              value: [
                { phase_number: 3, name: "hold" },
                { phase_number: 4, name: "hold" },
              ],
            }
          : field,
      ),
    };
    render(<ShotPhasesCard fields={fields} />);

    expect(screen.getAllByTestId("phase-not-reached")).toHaveLength(2);
    // Keyed by number: the same name twice is not a duplicate key.
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("shows no share of a target for a shot in no Set, and no unreached rows when none", () => {
    render(<ShotPhasesCard fields={realFields} />);

    expect(screen.queryByTestId("phase-phase_cup_share")).not.toBeInTheDocument();
    expect(screen.queryByTestId("phase-not-reached")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("phase-row")).toHaveLength(4);
    expect(screen.queryByText(/% of target/)).not.toBeInTheDocument();
  });

  it("keeps the firmware analyzer's values in a cell of their own", () => {
    render(<ShotPhasesCard fields={realFields} />);

    const cells = screen.getAllByTestId("phase-cell-firmware");
    expect(cells).toHaveLength(4);
    const ramp = cells[2];
    expect(ramp).toHaveTextContent("machine puck resistance (s·√bar/mL)");
    expect(ramp).toHaveTextContent("avg 2.11 (start 3.51, end 2.28, min 0.79, max 7.47)");
  });

  it("says once that a version 5 log cannot say why a phase ended", () => {
    render(<ShotPhasesCard fields={realFields} />);

    expect(screen.getByTestId("phase-log-note")).toHaveTextContent(
      "does not record why a phase ended: every phase reads Unknown",
    );
    const ended = screen.getAllByTestId("phase-cell-ended");
    expect(ended.every((cell) => cell.textContent === "Unknown")).toBe(true);
  });

  it("has no scale columns for a machine with no scale, and no zero in their place", () => {
    render(<ShotPhasesCard fields={leverNoScaleFields} />);

    expect(screen.queryByText("Cup at end")).not.toBeInTheDocument();
    expect(screen.queryByText("Scale flow")).not.toBeInTheDocument();
    expect(screen.queryByTestId("phase-cell-cup")).not.toBeInTheDocument();
    expect(screen.queryByTestId("phase-cell-scale-flow")).not.toBeInTheDocument();
    expect(screen.queryByTestId("phase-phase_cup_end")).not.toBeInTheDocument();
    // The rest of the row is still there.
    expect(screen.getAllByTestId("phase-row")).toHaveLength(3);
    expect(screen.getByText("Pressure")).toBeInTheDocument();
    expect(screen.queryByText(/\b0\.0 g\b/)).not.toBeInTheDocument();
  });

  it("has no pressure or firmware columns for a machine with no pressure sensor", () => {
    render(<ShotPhasesCard fields={leverNoPressureFields} />);

    expect(screen.queryByText("Pressure")).not.toBeInTheDocument();
    expect(screen.queryByText("Resistance")).not.toBeInTheDocument();
    expect(screen.queryByText("Firmware analyzer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("phase-phase_pressure")).not.toBeInTheDocument();
    // The cup is still the scale's, and still read.
    expect(
      within(screen.getAllByTestId("phase-row")[2]).getByTestId("phase-cell-cup"),
    ).toHaveTextContent("42.2 g");
  });

  it("draws a dash, not a zero, where a phase has no value its neighbours have", () => {
    // Resistance is only worked out for the brew phase.
    render(<ShotPhasesCard fields={leverFields} />);

    const [first, , ramp] = screen.getAllByTestId("phase-row");
    expect(within(first).getByTestId("phase-cell-resistance")).toHaveTextContent(/^—$/);
    expect(within(ramp).getByTestId("phase-cell-resistance")).toHaveTextContent("1.90");
  });

  it("lists the fields no column names under the row's own disclosure", () => {
    render(<ShotPhasesCard fields={leverFields} />);

    const more = within(screen.getAllByTestId("phase-row")[2]).getByTestId("phase-more");
    // The first drip fell in the ramp and the sample count is a field too.
    expect(more).toHaveTextContent("first drip");
    expect(more).toHaveTextContent("samples");
  });
});

describe("ShotWideCard and ShotContextCard", () => {
  it("leads with the outcome, the timing and the ratio; the context is its own card", () => {
    render(<ShotWideCard fields={leverFields} />);

    const shot = screen.getByText("The shot").closest("[data-slot=card]") as HTMLElement;
    expect(within(shot).getByTestId("shot-field-shot_time")).toBeInTheDocument();
    expect(within(shot).getByTestId("shot-field-yield")).toHaveTextContent("42.2 g");
    expect(within(shot).getByTestId("shot-field-yield_share")).toHaveTextContent(
      "117.2 % of the 36 g target yield",
    );
    // The version's dose against the yield, as the chat is told it.
    expect(within(shot).getByTestId("shot-field-ratio")).toHaveTextContent("1:2.34");
    expect(within(shot).getByTestId("shot-field-exit_reason")).toHaveTextContent(
      "Volumetric target",
    );
    expect(within(shot).getByTestId("shot-field-first_drip")).toBeInTheDocument();
    // The phases the shot never reached are the phase table's, and the context is not here.
    expect(screen.queryByTestId("shot-field-phases_not_reached")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-context")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-field-average_temperature")).not.toBeInTheDocument();
  });

  it("keeps the context collapsed until it is opened", () => {
    render(<ShotContextCard fields={leverFields} />);

    const toggle = screen.getByRole("button", { name: /Context/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("shot-context")).not.toBeVisible();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("shot-context")).toBeVisible();
    expect(
      within(screen.getByTestId("shot-context")).getByTestId("shot-field-average_temperature"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("shot-field-resistance_level")).toHaveTextContent(
      "1.90, from the machine",
    );
  });

  it("shows the machine's own analyzer values and no band anywhere", () => {
    render(<ShotContextCard fields={leverFields} />);

    expect(screen.getByTestId("shot-field-machine_puck_resistance")).toHaveTextContent("avg 1.00");
    expect(screen.getByTestId("shot-field-liquid_resistance")).toHaveTextContent("avg 2.72");
    expect(
      screen.queryByText(/excellent|poor|very low|moderate|channeling/i),
    ).not.toBeInTheDocument();
  });

  it("has no yield for a machine with no scale, and says nothing in its place", () => {
    render(<ShotWideCard fields={leverNoScaleFields} />);
    render(<ShotContextCard fields={leverNoScaleFields} />);

    expect(screen.queryByTestId("shot-field-yield")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-field-yield_share")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-field-ratio")).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.0 g/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-field-weight_rate")).not.toBeInTheDocument();
  });

  it("has no pressure for a machine with no pressure sensor", () => {
    render(<ShotWideCard fields={leverNoPressureFields} />);
    render(<ShotContextCard fields={leverNoPressureFields} />);

    expect(screen.queryByTestId("shot-field-peak_pressure")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-field-machine_puck_resistance")).not.toBeInTheDocument();
    expect(screen.getByTestId("shot-field-yield")).toBeInTheDocument();
  });
});
