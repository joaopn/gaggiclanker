import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ShotChecksCard } from "@/components/shots/ShotChecksCard";
import { showClaim } from "@/lib/claimSpan";
import { claim } from "@/test/claimFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import {
  leverFields,
  leverSignedFields,
  leverSignedNoScaleFields,
  turboFields,
} from "@/test/shotFieldsFixture";

/**
 * The Curve check card, against documents the server really serves
 * (`scripts/build_web_shot_fields_fixture.py`): the lever shot read against a confirmed
 * signature, the same on a machine with no scale, a turbo whose signature expects fast flow,
 * and the lever read without one.
 */

function card(fields: typeof leverSignedFields) {
  return renderWithQueryClient(
    <ShotChecksCard checks={fields.checks.items} signature={fields.signature} />,
  );
}

const titles = (list: HTMLElement) =>
  within(list)
    .getAllByTestId("check-line")
    .map((line) => line.querySelector("p")?.textContent);

describe("ShotChecksCard", () => {
  it("lists red, then amber, then grey as served, and keeps the held and the review's apart", () => {
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

  it("collapses what only the review can check, and says so", async () => {
    const user = userEvent.setup({ delay: null });
    card(leverSignedFields);

    const toggle = screen.getByTestId("checks-unchecked-toggle");
    expect(toggle).toHaveTextContent("1 checked by the review");
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
    const first = leverSignedFields.checks.items[0];
    renderWithQueryClient(
      <ShotChecksCard
        checks={[first, { ...first, expectation_id: (first.expectation_id ?? 0) + 1 }]}
        signature={leverSignedFields.signature}
      />,
    );
    spy.mockRestore();

    expect(screen.getAllByTestId("check-line")).toHaveLength(2);
    expect(errors.flat().join(" ")).not.toMatch(/same key/);
  });

  it("describes the card as it is: warnings stand alone, a signature can mark one expected", () => {
    card(leverSignedFields);

    const text = screen.getByText(/What this shot was held against/).textContent ?? "";
    expect(text).toMatch(/by numbers alone/);
    expect(text).toMatch(/checked without knowing what the profile is for/);
    expect(text).toMatch(/expected \(grey\)/);
    expect(text).toMatch(/red \(critical\) or amber \(important\)/);
  });

  it("cuts a long phase name inside its line and never the fault beside it", () => {
    const long = "Pre-infusion with a long soak and a long name";
    renderWithQueryClient(
      <ShotChecksCard
        checks={[{ ...leverSignedFields.checks.items[0], phase: long }]}
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

  it("is titled Curve check and says nothing a model wrote", () => {
    card(leverSignedFields);
    expect(screen.getByText("Curve check")).toBeInTheDocument();
    expect(screen.queryByText(/the review says/)).toBeNull();
    expect(screen.queryByTestId("check-unverified")).toBeNull();
  });

  describe("a free-text expectation the review answers", () => {
    const base = leverSignedFields.checks.items.find((check) => check.kind === "free_text");
    if (!base) throw new Error("the fixture has no free-text check");
    const answered = claim({
      id: 77,
      kind: "free_text",
      expectation_id: base.expectation_id,
      held: false,
    });

    it("is listed as checked by the review, never as failed or held", () => {
      card(leverSignedFields);
      const group = screen.getByTestId("checks-unchecked");
      expect(group).toHaveTextContent("1 checked by the review");
      expect(within(screen.getByTestId("check-list")).queryByText(base.sentence)).toBeNull();
    });

    it("links to the model's answer, and the link asks the page to show that claim", async () => {
      const user = userEvent.setup({ delay: null });
      const onShowClaim = vi.fn();
      renderWithQueryClient(
        <ShotChecksCard
          checks={leverSignedFields.checks.items}
          signature={leverSignedFields.signature}
          claims={[answered]}
          onShowClaim={onShowClaim}
        />,
      );
      await user.click(screen.getByTestId("checks-unchecked-toggle"));
      const control = screen.getByTestId("check-claim-link");
      // Not an anchor: an href would navigate and close the row.
      expect(control.tagName).toBe("BUTTON");
      expect(control).not.toHaveAttribute("href");
      await user.click(control);
      expect(onShowClaim).toHaveBeenCalledWith(77);
    });

    it("draws no link when there is no answer, when the answer was rejected, or nothing can show it", () => {
      const props = {
        checks: leverSignedFields.checks.items,
        signature: leverSignedFields.signature,
      };
      const { rerender } = renderWithQueryClient(<ShotChecksCard {...props} claims={[answered]} />);
      expect(screen.queryByTestId("check-claim-link")).toBeNull();
      rerender(
        <ShotChecksCard
          {...props}
          claims={[{ ...answered, status: "rejected" }]}
          onShowClaim={() => undefined}
        />,
      );
      expect(screen.queryByTestId("check-claim-link")).toBeNull();
      rerender(<ShotChecksCard {...props} claims={[]} onShowClaim={() => undefined} />);
      expect(screen.queryByTestId("check-claim-link")).toBeNull();
    });

    it("showClaim scrolls the claim into view by the least distance and focuses it", () => {
      document.body.innerHTML = '<ul><li id="claim-77" tabindex="-1">the claim</li></ul>';
      const element = document.getElementById("claim-77") as HTMLElement;
      const scrolled = vi.spyOn(element, "scrollIntoView").mockImplementation(() => undefined);
      showClaim(77);
      expect(scrolled).toHaveBeenCalledWith({ block: "nearest", behavior: "smooth" });
      expect(element).toHaveFocus();
      expect(() => showClaim(999)).not.toThrow();
    });
  });
});
