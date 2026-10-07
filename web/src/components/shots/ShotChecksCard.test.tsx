import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ShotChecksCard, ShotRowChecksCard } from "@/components/shots/ShotChecksCard";
import { claim } from "@/test/readingFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import {
  leverFields,
  leverSignedFields,
  leverSignedNoScaleFields,
  turboFields,
} from "@/test/shotFieldsFixture";

/**
 * The Checks card, against documents the server really serves
 * (`scripts/build_web_shot_fields_fixture.py`): the lever shot read against a confirmed
 * signature, the same on a machine with no scale, a turbo whose signature expects fast flow,
 * and the lever read without one.
 */

function card(fields: typeof leverSignedFields) {
  return renderWithQueryClient(
    <ShotChecksCard checks={fields.checks} signature={fields.signature} />,
  );
}

function Probe() {
  const { pathname, search, hash } = useLocation();
  return <p data-testid="where">{`${pathname}${search}${hash}`}</p>;
}

const titles = (list: HTMLElement) =>
  within(list)
    .getAllByTestId("check-line")
    .map((line) => line.querySelector("p")?.textContent);

describe("ShotChecksCard", () => {
  it("lists red, then amber, then grey as served, and keeps the held and the reading's apart", () => {
    card(leverSignedFields);

    const list = screen.getByTestId("check-list");
    expect(titles(list)).toEqual([
      "ramp: early yield",
      "decline: skipped",
      "soak: early yield",
      "Shot: over target",
      "ramp: fast flow",
    ]);
    expect(
      within(list)
        .getAllByTestId("check-line")
        .map((line) => line.getAttribute("data-severity")),
    ).toEqual(["red", "red", "amber", "amber", "grey"]);
    // Held and unchecked come after, each collapsed, and neither is among the lines in front.
    const held = screen.getByTestId("checks-held");
    const unchecked = screen.getByTestId("checks-unchecked");
    expect(list.compareDocumentPosition(held) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(held.compareDocumentPosition(unchecked) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(held).toHaveAttribute("data-open", "no");
    expect(unchecked).toHaveAttribute("data-open", "no");
  });

  it("collapses the held ones under their count, and opens them on request", async () => {
    const user = userEvent.setup({ delay: null });
    card(leverSignedFields);

    const toggle = screen.getByTestId("checks-held-toggle");
    expect(toggle).toHaveTextContent("1 held");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const lines = within(screen.getByTestId("checks-held")).getAllByTestId("check-line");
    expect(lines).toHaveLength(1);
    // The list is in the document but hidden, so nothing that passed is shown in front.
    expect(lines[0].closest("ul")).toHaveAttribute("hidden");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(lines[0].closest("ul")).not.toHaveAttribute("hidden");
    expect(lines[0]).toHaveTextContent("preinfusion: held");
    expect(lines[0]).toHaveTextContent("0 % of target, at most 5 % of target");
  });

  it("collapses what only the reading can check, and says so", async () => {
    const user = userEvent.setup({ delay: null });
    card(leverSignedFields);

    const toggle = screen.getByTestId("checks-unchecked-toggle");
    expect(toggle).toHaveTextContent("1 checked by the reading");
    const line = within(screen.getByTestId("checks-unchecked")).getByTestId("check-line");
    expect(line.closest("ul")).toHaveAttribute("hidden");
    await user.click(toggle);
    expect(line).toHaveTextContent("pressure and flow fall together through the decline");
  });

  it("gives each failed measure's value against its limit, and what was measured beneath", () => {
    card(leverSignedFields);

    const first = within(screen.getByTestId("check-list")).getAllByTestId("check-line")[0];
    expect(first).toHaveTextContent("117.2 % of target, at most 15 % of target");
    expect(first).toHaveTextContent("cup weight at the end of the ramp");
    // A phase that must begin has no number: its own sentence says it never did.
    const decline = within(screen.getByTestId("check-list")).getAllByTestId("check-line")[1];
    expect(decline).toHaveTextContent("it never began");
  });

  it("lists a check that could not be measured with its reason, and counts it neither way", () => {
    card(leverSignedNoScaleFields);

    const list = screen.getByTestId("check-list");
    const unmeasured = within(list)
      .getAllByTestId("check-line")
      .filter((line) => line.getAttribute("data-status") === "unmeasured");
    expect(unmeasured).toHaveLength(3);
    expect(unmeasured[0]).toHaveTextContent("preinfusion: not measured");
    expect(unmeasured[0]).toHaveTextContent(
      "the shot has no scale, and the weight is never estimated",
    );
    expect(unmeasured[0]).toHaveAttribute("data-severity", "none");
    // The one that did fail still does, and what is not measured is not "held".
    expect(titles(list)[0]).toBe("decline: skipped");
    expect(screen.getByTestId("checks-held-toggle")).toHaveTextContent("1 held");
  });

  it("shows a turbo's expected fast flow in grey and nothing in amber", () => {
    card(turboFields);

    const lines = screen.getAllByTestId("check-line");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveAttribute("data-severity", "grey");
    expect(lines[0]).toHaveTextContent("main: fast flow");
    expect(screen.queryByText(/over target/)).toBeNull();
  });

  it("says a shot was read without a signature, and links to the profile version's card", () => {
    card(leverFields);

    const line = screen.getByTestId("signature-state");
    expect(line).toHaveTextContent("Read without a signature.");
    expect(screen.getByTestId("signature-link")).toHaveAttribute(
      "href",
      `/profiles#version-${leverFields.signature.profile_version_id}`,
    );
  });

  it("still draws the card, with only that line, for a shot with a profile and nothing else to say", () => {
    renderWithQueryClient(<ShotChecksCard checks={[]} signature={leverFields.signature} />);

    expect(screen.getByTestId("signature-state")).toBeInTheDocument();
    expect(screen.queryByTestId("check-list")).toBeNull();
  });

  it("names the signature a shot was read against, with a link to it", () => {
    card(leverSignedFields);

    const line = screen.getByTestId("signature-state");
    expect(line).toHaveTextContent(
      "Read against the profile's signature: confirmed, 6 expectations",
    );
    expect(screen.getByTestId("signature-link")).toHaveAttribute("href", "/profiles#version-1");
  });

  it("keeps two lines that read alike apart, with no duplicate-key warning", () => {
    const errors: unknown[][] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args) => {
      errors.push(args);
    });
    const [first] = leverSignedFields.warnings;
    renderWithQueryClient(
      <ShotRowChecksCard
        warnings={[first, { ...first, expectation_id: (first.expectation_id ?? 0) + 1 }]}
      />,
    );
    spy.mockRestore();

    expect(screen.getAllByTestId("check-line")).toHaveLength(2);
    expect(errors.flat().join(" ")).not.toMatch(/same key/);
  });

  it("describes the card as it is: warnings stand alone, a signature can mark one expected", () => {
    card(leverSignedFields);

    const text = screen.getByText(/What this shot was held against/).textContent ?? "";
    expect(text).toMatch(/checked without knowing what the profile is for/);
    expect(text).toMatch(/expected \(grey\)/);
    expect(text).toMatch(/red \(critical\) or amber \(important\)/);
  });

  it("cuts a long phase name inside its line and never the fault beside it", () => {
    const long = "Pre-infusion with a long soak and a long name";
    renderWithQueryClient(
      <ShotChecksCard
        checks={[{ ...leverSignedFields.checks[0], phase: long }]}
        signature={leverSignedFields.signature}
      />,
    );

    const phase = screen.getByTestId("check-phase");
    expect(phase).toHaveClass("truncate", "max-w-[50%]", "shrink-0");
    expect(phase).toHaveAttribute("title", long);
    expect(screen.getByTestId("check-title")).toHaveTextContent(`${long}: early yield`);
    expect(phase.nextElementSibling).toHaveTextContent(": early yield");
    expect(phase.nextElementSibling).not.toHaveClass("truncate");
  });

  it("a shots-list row's card has only the entries the row carries", () => {
    renderWithQueryClient(<ShotRowChecksCard warnings={leverSignedFields.warnings} />);

    expect(screen.getAllByTestId("check-line")).toHaveLength(5);
    expect(screen.queryByTestId("checks-held")).toBeNull();
    expect(
      renderWithQueryClient(<ShotRowChecksCard warnings={[]} />).container,
    ).toBeEmptyDOMElement();
  });

  describe("a free-text expectation the reading has answered", () => {
    const base = leverSignedFields.checks.find((check) => check.kind === "free_text");
    if (!base) throw new Error("the fixture has no free-text check");
    const failed = {
      ...base,
      status: "failed",
      held: false,
      tier: "important",
      color: "amber",
      detail: `${base.sentence}; the reading says it failed: pressure keeps rising while flow falls.`,
    };
    const answered = claim({
      id: 77,
      kind: "free_text",
      expectation_id: base.expectation_id,
      held: false,
    });
    const checks = (list: typeof leverSignedFields.checks) => (
      <ShotChecksCard checks={list} signature={leverSignedFields.signature} claims={[answered]} />
    );

    it("lists a failed result in its tier colour, with the fault word and what the reading said", () => {
      renderWithQueryClient(
        checks([...leverSignedFields.checks.filter((c) => c !== base), failed]),
      );
      const lines = within(screen.getByTestId("check-list")).getAllByTestId("check-line");
      const line = lines.find((el) => el.textContent?.includes("the reading says it failed"));
      if (!line) throw new Error("no line for the failed result");
      expect(line).toHaveAttribute("data-severity", "amber");
      expect(line).toHaveTextContent("Shot: unstable");
      expect(line).toHaveTextContent("pressure keeps rising while flow falls");
      // It left the group that only a reading can check.
      expect(screen.queryByTestId("checks-unchecked")).toBeNull();
    });

    it("links to its claim in the review", () => {
      renderWithQueryClient(checks([failed]));
      const line = screen.getByText(/the reading says it failed/).closest("li");
      if (!line) throw new Error("no line");
      expect(within(line).queryByTestId("check-unverified")).toBeNull();
      expect(within(line).getByTestId("check-claim-link")).toHaveAttribute("href", "/#claim-77");
    });

    it("keeps a held result in the held group, linked all the same", async () => {
      const user = userEvent.setup({ delay: null });
      renderWithQueryClient(
        checks([
          {
            ...failed,
            status: "held",
            held: true,
            color: null,
            detail: `${base.sentence}; the reading says it held: it did.`,
          },
        ]),
      );
      const group = screen.getByTestId("checks-held");
      await user.click(screen.getByTestId("checks-held-toggle"));
      expect(within(group).getByTestId("check-claim-link")).toBeInTheDocument();
      expect(group).toHaveTextContent("the reading says it held");
    });

    it("draws no link when the claim is not on the page", () => {
      renderWithQueryClient(
        <ShotChecksCard checks={[failed]} signature={leverSignedFields.signature} />,
      );
      expect(screen.queryByTestId("check-claim-link")).toBeNull();
    });

    it("in the open row, the link is a button that scrolls the row's own claim into view and focuses it", async () => {
      const user = userEvent.setup({ delay: null });
      const [first] = leverSignedFields.warnings;
      const entry = { ...first, expectation_id: base.expectation_id };
      renderWithQueryClient(
        <>
          <Probe />
          <ShotRowChecksCard warnings={[entry]} claims={[answered]} />
          {/* The row's own Reading card, with this claim in it. */}
          <ul>
            <li id="claim-77" tabIndex={-1} data-testid="the-claim">
              the claim
            </li>
          </ul>
        </>,
        { initialEntries: ["/shots?sort=review&order=desc"] },
      );
      // Not an anchor: an href would navigate and close the row.
      const control = screen.getByTestId("check-claim-link");
      expect(control.tagName).toBe("BUTTON");
      expect(control).not.toHaveAttribute("href");
      const claimEl = screen.getByTestId("the-claim");
      const scrolled = vi.spyOn(claimEl, "scrollIntoView").mockImplementation(() => undefined);
      const other = vi
        .spyOn(Element.prototype, "scrollIntoView")
        .mockImplementation(() => undefined);

      await user.click(control);

      expect(scrolled).toHaveBeenCalledTimes(1);
      expect(scrolled).toHaveBeenCalledWith({ block: "nearest", behavior: "smooth" });
      expect(other).not.toHaveBeenCalled();
      expect(claimEl).toHaveFocus();
      // The URL, and with it the list's sort, is untouched.
      expect(screen.getByTestId("where")).toHaveTextContent("/shots?sort=review&order=desc");
      other.mockRestore();
    });

    it("draws no link in the open row when the claim is not on screen", () => {
      const [first] = leverSignedFields.warnings;
      const entry = { ...first, expectation_id: base.expectation_id };
      renderWithQueryClient(<ShotRowChecksCard warnings={[entry]} />);
      expect(screen.queryByTestId("check-claim-link")).toBeNull();
    });
  });
});
