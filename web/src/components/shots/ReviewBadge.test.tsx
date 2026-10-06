import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReviewBadge } from "@/components/shots/ReviewBadge";
import { leverSignedFields, turboFields } from "@/test/shotFieldsFixture";
import { LEVER_BADGE, LEVER_WARNINGS } from "@/test/warningFixtures";

describe("ReviewBadge", () => {
  it("says the first warning, and how many more there are", () => {
    render(<ReviewBadge badge={LEVER_BADGE} warnings={LEVER_WARNINGS} />);

    const badge = screen.getByTestId("review-badge");
    expect(badge).toHaveTextContent("ramp: fast flow +2");
    expect(badge).toHaveAttribute("data-tone", "warn");
  });

  it("says a single warning without a count", () => {
    render(<ReviewBadge badge="decline: skipped" warnings={[LEVER_WARNINGS[1]]} />);

    expect(screen.getByTestId("review-badge")).toHaveTextContent(/^decline: skipped/);
    expect(screen.getByTestId("review-badge")).not.toHaveTextContent("+");
  });

  it("lists every warning with its sentence on hover, and for a screen reader", () => {
    render(<ReviewBadge badge={LEVER_BADGE} warnings={LEVER_WARNINGS} />);

    const lines = (screen.getByTestId("review-badge-wrap").getAttribute("title") ?? "").split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toMatch(/^ramp: fast flow — The scale flow averaged 4\.00 g\/s/);
    expect(lines[1]).toMatch(/^decline: skipped — The shot stopped on its volumetric target/);
    expect(lines[2]).toMatch(/^Shot: over target — The final weight, 42\.2 g/);
    // Read once: the badge is described by the hidden sentence, and carries no
    // title of its own that a screen reader would add a second time.
    const badge = screen.getByTestId("review-badge");
    expect(badge).not.toHaveAttribute("title");
    expect(badge).toHaveAttribute("aria-describedby", screen.getByTestId("review-badge-list").id);
    expect(screen.getByTestId("review-badge-list")).toHaveTextContent(
      /ramp: fast flow — .*\. decline: skipped — .*\. Shot: over target — /,
    );
    // A sentence that already ends in a full stop gets no second one.
    expect(screen.getByTestId("review-badge-list").textContent).not.toMatch(/\.\./);
    // Not a list inside the badge's own element, so the badge can be a button.
    expect(badge.querySelector("ul, ol, li")).toBeNull();
    expect(screen.getByTestId("review-badge-list").tagName).toBe("SPAN");
  });

  it("adds a full stop only to a line that has none", () => {
    render(
      <ReviewBadge
        badge="a: b +1"
        warnings={[
          { ...LEVER_WARNINGS[0], phase: "a", fault: "b", detail: "It ran" },
          { ...LEVER_WARNINGS[1], phase: "c", fault: "d", detail: "It stopped." },
        ]}
      />,
    );
    expect(screen.getByTestId("review-badge-list")).toHaveTextContent(
      "a: b — It ran. c: d — It stopped.",
    );
  });

  it("draws nothing for a shot with no warnings: a missing warning is not a verdict", () => {
    const { container } = render(<ReviewBadge badge={null} warnings={[]} />);
    expect(container).toBeEmptyDOMElement();

    const { container: absent } = render(<ReviewBadge badge={undefined} warnings={undefined} />);
    expect(absent).toBeEmptyDOMElement();
  });

  it("colours a critical failure red, by its own severity and not the others'", () => {
    render(
      <ReviewBadge
        badge="decline: skipped"
        warnings={[{ ...LEVER_WARNINGS[1], severity: "red" }]}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "bad");
  });

  it("takes its colour from the first entry's severity: red, amber or grey", () => {
    const { rerender } = render(
      <ReviewBadge badge={leverSignedFields.badge} warnings={leverSignedFields.warnings} />,
    );
    // The served list starts with a failed critical expectation: red, though amber ones follow.
    expect(screen.getByTestId("review-badge")).toHaveTextContent("ramp: early yield +4");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "bad");

    rerender(<ReviewBadge badge={LEVER_BADGE} warnings={LEVER_WARNINGS} />);
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "warn");

    // Grey: a warning the signature expects. Nothing in the list is amber.
    rerender(<ReviewBadge badge={turboFields.badge} warnings={turboFields.warnings} />);
    expect(screen.getByTestId("review-badge")).toHaveTextContent("main: fast flow");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");

    // The order decides, not the worst of the list: a grey entry first is grey.
    rerender(
      <ReviewBadge
        badge="a: b +1"
        warnings={[
          { ...turboFields.warnings[0], phase: "a", fault: "b" },
          { ...LEVER_WARNINGS[0], severity: "red" },
        ]}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
  });

  it("cuts only the phase name, never the fault word or the count", () => {
    const phase = "Final push to the cup, long";
    render(
      <ReviewBadge
        badge={`${phase}: early yield +1`}
        warnings={[
          { ...LEVER_WARNINGS[0], phase, fault: "early yield", severity: "red" },
          LEVER_WARNINGS[1],
        ]}
        className="w-24"
      />,
    );

    // The structure that gives the rule (layout is measured in Chromium, which jsdom has not):
    // one flexible box clips what is left, holding the phase (the only part that shrinks) and the
    // fault word (its own width, never shrunk by sharing a shortfall with the phase); the count
    // sits outside the box and never shrinks.
    const badge = screen.getByTestId("review-badge");
    const [box, count] = Array.from(badge.children);
    expect(box).toHaveAttribute("data-testid", "review-badge-text");
    expect(box).toHaveClass("flex", "min-w-0", "flex-1", "overflow-hidden");
    const name = screen.getByTestId("review-badge-phase");
    const fault = screen.getByTestId("review-badge-fault");
    expect(name.parentElement).toBe(box);
    expect(fault.parentElement).toBe(box);
    expect(name).toHaveClass("truncate", "min-w-0");
    expect(name).toHaveTextContent(phase);
    expect(fault).toHaveTextContent(": early yield");
    // No shrink factor on the fault: a factor shares the shortfall out by width, so the fault
    // would lose a fraction of a pixel (an ellipsis) whenever the phase is cut.
    expect(fault).toHaveClass("shrink-0", "max-w-full", "truncate");
    expect(fault.className).not.toMatch(/(^|\s)shrink(\s|$|-\[)/);
    expect(name.className).not.toMatch(/shrink/);
    expect(count).toHaveTextContent("+1");
    expect(count).toHaveClass("shrink-0");
    expect(badge).toHaveClass("shrink", "overflow-hidden");
    expect(screen.getByTestId("review-badge-wrap")).toHaveClass("overflow-hidden");
  });

  it("truncates a long phase name inside its column and never the count", () => {
    render(
      <ReviewBadge
        badge="a very long phase name indeed: skipped +2"
        warnings={LEVER_WARNINGS}
        className="w-24"
      />,
    );
    const [name, count] = Array.from(screen.getByTestId("review-badge").children);
    expect(name).toHaveClass("truncate");
    expect(name).toHaveTextContent("a very long phase name indeed: skipped");
    // Outside the span that is cut: it stays visible when the name does not fit.
    expect(count).toHaveTextContent("+2");
    expect(count).not.toHaveClass("truncate");
    expect(count).toHaveClass("shrink-0");
    expect(screen.getByTestId("review-badge")).toHaveClass("max-w-full");
  });
});
