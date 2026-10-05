import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReviewBadge } from "@/components/shots/ReviewBadge";
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
