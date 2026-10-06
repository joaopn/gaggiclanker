import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReviewBadge } from "@/components/shots/ReviewBadge";
import { readingBlock } from "@/test/readingFixtures";
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

describe("ReviewBadge reading states", () => {
  const entries = { badge: leverSignedFields.badge, warnings: leverSignedFields.warnings };

  it("says Review, neutral and outlined, for an unread shot with nothing to say", () => {
    render(
      <ReviewBadge
        badge="Review"
        warnings={[]}
        reading={readingBlock()}
        onPress={() => undefined}
      />,
    );
    const badge = screen.getByTestId("review-badge");
    expect(badge).toHaveTextContent("Review");
    expect(badge).toHaveAttribute("data-tone", "muted");
    expect(badge).toHaveAttribute("data-filled", "no");
    expect(badge).toHaveClass("bg-transparent");
  });

  it("keeps the checks' entries, filled, for an unread shot that has some", () => {
    render(<ReviewBadge {...entries} reading={readingBlock()} />);
    const badge = screen.getByTestId("review-badge");
    expect(badge).toHaveTextContent("ramp: early yield +4");
    expect(badge).toHaveAttribute("data-tone", "bad");
    expect(badge).toHaveAttribute("data-filled", "yes");
  });

  it("says Reading… with a spinner while one runs, grey", () => {
    render(
      <ReviewBadge badge="Reading…" warnings={[]} reading={readingBlock({ state: "running" })} />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("Reading…");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
    expect(screen.getByTestId("review-badge-spinner")).toBeInTheDocument();
    // Nothing is confirmed of a reading that has not finished: outlined, not filled.
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-filled", "no");
  });

  it("says Failed to run, grey, with the reason in the tooltip", () => {
    render(
      <ReviewBadge
        badge="Failed to run"
        warnings={[]}
        reading={readingBlock({ state: "failed", reason: "the provider timed out" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("Failed to run");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
    expect(screen.getByTestId("review-badge-wrap")).toHaveAttribute(
      "title",
      "Failed to run: the provider timed out",
    );
  });

  it("is outlined while claims are unanswered and filled once none is", () => {
    const { rerender } = render(
      <ReviewBadge
        {...entries}
        reading={readingBlock({ state: "read", verdict: "entries", unanswered: 2 })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-filled", "no");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "bad");
    rerender(
      <ReviewBadge
        {...entries}
        reading={readingBlock({ state: "read", verdict: "entries", unanswered: 0 })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-filled", "yes");
  });

  it("says As intended in green and No signature in grey, each outlined until answered", () => {
    const { rerender } = render(
      <ReviewBadge
        badge="As intended"
        warnings={[]}
        reading={readingBlock({ state: "read", verdict: "as_intended", unanswered: 1 })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("As intended");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "good");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-filled", "no");
    rerender(
      <ReviewBadge
        badge="As intended"
        warnings={[]}
        reading={readingBlock({ state: "read", verdict: "as_intended" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-filled", "yes");
    rerender(
      <ReviewBadge
        badge="No signature"
        warnings={[]}
        reading={readingBlock({ state: "read", verdict: "no_signature" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("No signature");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
  });

  it("marks an unverified entry in the tooltip, and ends it with the summary", () => {
    render(
      <ReviewBadge
        badge="decline: unstable"
        warnings={[{ ...LEVER_WARNINGS[1], phase: "decline", fault: "unstable", unverified: true }]}
        reading={readingBlock({
          state: "read",
          verdict: "entries",
          unanswered: 1,
          summary: "A fast ramp and a wandering decline.",
        })}
      />,
    );
    const lines = (screen.getByTestId("review-badge-wrap").getAttribute("title") ?? "").split("\n");
    expect(lines[0]).toMatch(/^decline: unstable — .*\(unverified\)$/);
    expect(lines[0]).not.toMatch(/\.\s*\(unverified\)/);
    // The screen-reader sentence puts the stop after the mark, not before it.
    expect(screen.getByTestId("review-badge-list").textContent).toMatch(/\(unverified\)\./);
    expect(screen.getByTestId("review-badge-list").textContent).not.toMatch(/\.\s*\(unverified\)/);
    expect(lines[lines.length - 1]).toBe("A fast ramp and a wandering decline.");
  });

  it("draws nothing for a shot nobody can read and nothing is wrong with", () => {
    const { container } = render(
      <ReviewBadge badge={null} warnings={[]} reading={readingBlock({ state: "not_readable" })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ReviewBadge as a button", () => {
  it("is a plain label with a screen-reader sentence unless the table gives it a press", () => {
    render(
      <ReviewBadge
        badge="As intended"
        warnings={[]}
        reading={readingBlock({ state: "read", verdict: "as_intended" })}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByTestId("review-badge-list")).toHaveClass("sr-only");
    // An unread shot has nothing to show where it cannot be pressed.
    const { container } = render(
      <ReviewBadge badge="Review" warnings={[]} reading={readingBlock()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("is a real button with a hidden description when it can be pressed", () => {
    render(
      <ReviewBadge
        badge="Review"
        warnings={[]}
        reading={readingBlock()}
        onPress={() => undefined}
      />,
    );
    const button = screen.getByRole("button", { name: "Review" });
    expect(button).toBe(screen.getByTestId("review-badge"));
    const list = screen.getByTestId("review-badge-list");
    expect(list).toHaveClass("hidden");
    expect(list).not.toHaveClass("sr-only");
    expect(button).toHaveAccessibleDescription(/Press to have a model read this shot/);
    // The description is a sibling, never inside the button.
    expect(button.contains(list)).toBe(false);
  });

  it("is never a button on a shot nobody can read", () => {
    render(
      <ReviewBadge
        badge="ramp: early yield"
        warnings={[leverSignedFields.warnings[0]]}
        reading={readingBlock({ state: "not_readable" })}
        onPress={() => undefined}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("presses once per click and never lets the row behind it see the click", async () => {
    const onPress = vi.fn();
    const rowClick = vi.fn();
    render(
      // biome-ignore lint/a11y/noStaticElementInteractions: stands in for the row's toggle
      // biome-ignore lint/a11y/useKeyWithClickEvents: stands in for the row's toggle
      <div onClick={rowClick}>
        <ReviewBadge badge="Review" warnings={[]} reading={readingBlock()} onPress={onPress} />
      </div>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
    expect(rowClick).not.toHaveBeenCalled();
  });

  it("is inert while a reading runs: click, Enter and Space do nothing", async () => {
    const onPress = vi.fn();
    const rowClick = vi.fn();
    render(
      // biome-ignore lint/a11y/noStaticElementInteractions: stands in for the row's toggle
      // biome-ignore lint/a11y/useKeyWithClickEvents: stands in for the row's toggle
      <div onClick={rowClick}>
        <ReviewBadge
          badge="Reading…"
          warnings={[]}
          reading={readingBlock({ state: "running" })}
          onPress={onPress}
        />
      </div>,
    );
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(button);
    button.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    fireEvent.click(button);
    expect(onPress).not.toHaveBeenCalled();
    expect(rowClick).not.toHaveBeenCalled();
  });

  it("keeps the phase-truncates, fault-whole structure as a button", () => {
    const phase = "Final push to the cup, long";
    render(
      <ReviewBadge
        badge={`${phase}: early yield +1`}
        warnings={[
          { ...LEVER_WARNINGS[0], phase, fault: "early yield", severity: "red" },
          LEVER_WARNINGS[1],
        ]}
        reading={readingBlock()}
        onPress={() => undefined}
        className="w-24"
      />,
    );
    const button = screen.getByRole("button");
    const [box, count] = Array.from(button.children);
    expect(box).toHaveAttribute("data-testid", "review-badge-text");
    expect(screen.getByTestId("review-badge-fault")).toHaveClass("shrink-0", "truncate");
    expect(count).toHaveTextContent("+1");
    expect(count).toHaveClass("shrink-0");
  });
});

describe("ReviewBadge without the served text", () => {
  it("never says No signature unless the verdict is that", () => {
    const entries = readingBlock({ state: "read", verdict: "entries", unanswered: 2 });
    const { rerender } = render(<ReviewBadge badge={null} warnings={[]} reading={entries} />);
    expect(screen.getByTestId("review-badge")).not.toHaveTextContent("No signature");
    expect(screen.getByTestId("review-badge")).toHaveTextContent("Failures loading…");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
    rerender(
      <ReviewBadge
        badge={null}
        warnings={[]}
        reading={readingBlock({ state: "read", verdict: "no_signature" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("No signature");
    rerender(
      <ReviewBadge
        badge={undefined}
        warnings={undefined}
        reading={readingBlock({ state: "read", verdict: "as_intended" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("As intended");
  });
});
