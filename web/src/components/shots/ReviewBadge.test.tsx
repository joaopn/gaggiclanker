import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReviewBadge } from "@/components/shots/ReviewBadge";
import { reviewBlock } from "@/test/claimFixtures";
import { LEVER_WARNINGS } from "@/test/warningFixtures";

const faults = [
  { ...LEVER_WARNINGS[1], phase: "decline", fault: "unstable", severity: "amber" },
  { ...LEVER_WARNINGS[0], phase: "ramp", fault: "fast flow", severity: "amber" },
];

const reviewed = (overrides = {}) =>
  reviewBlock({
    state: "reviewed",
    verdict: "entries",
    badge: "decline: unstable +1",
    entries: faults,
    summary: "A fast ramp and a wandering decline.",
    ...overrides,
  });

describe("ReviewBadge", () => {
  it("is a Review button for a shot nobody has reviewed, and nothing where nothing can be pressed", () => {
    const onStart = vi.fn();
    const { container, rerender } = render(
      <ReviewBadge review={reviewBlock()} onStart={onStart} />,
    );
    const button = screen.getByRole("button", { name: "Review" });
    expect(button.tagName).toBe("BUTTON");
    fireEvent.click(button);
    expect(onStart).toHaveBeenCalledTimes(1);

    rerender(<ReviewBadge review={reviewBlock()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws nothing for a shot nobody can review", () => {
    const { container } = render(
      <ReviewBadge review={reviewBlock({ state: "not_reviewable" })} onStart={() => undefined} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(render(<ReviewBadge review={undefined} />).container).toBeEmptyDOMElement();
  });

  it("says Reviewing… with a spinner while one runs: grey, inert, no button", async () => {
    const onStart = vi.fn();
    render(
      <ReviewBadge
        review={reviewBlock({ state: "running", badge: "Reviewing…" })}
        onStart={onStart}
        onOpen={onStart}
      />,
    );
    const badge = screen.getByTestId("review-badge");
    expect(badge).toHaveTextContent("Reviewing…");
    expect(badge).toHaveAttribute("data-tone", "muted");
    expect(badge).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByTestId("review-badge-spinner")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    await userEvent.click(badge);
    expect(onStart).not.toHaveBeenCalled();
  });

  it("says Failed to run, grey, the reason in the tooltip, with a Retry button", () => {
    const onStart = vi.fn();
    render(
      <ReviewBadge
        review={reviewBlock({
          state: "failed",
          badge: "Failed to run",
          reason: "the provider timed out",
        })}
        onStart={onStart}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("Failed to run");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
    expect(screen.getByTestId("review-badge-wrap")).toHaveAttribute(
      "title",
      "Failed to run: the provider timed out",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  it("names the model's first fault and counts the rest, in the first fault's tone", () => {
    render(<ReviewBadge review={reviewed()} />);
    const badge = screen.getByTestId("review-badge");
    expect(badge).toHaveTextContent("decline: unstable +1");
    expect(badge).toHaveAttribute("data-tone", "warn");
    expect(screen.getByTestId("review-badge-phase")).toHaveTextContent("decline");
    expect(screen.getByTestId("review-badge-fault")).toHaveTextContent(": unstable");
  });

  it("colours a critical failure red", () => {
    render(
      <ReviewBadge
        review={reviewed({
          badge: "decline: unstable",
          entries: [{ ...faults[0], severity: "red" }],
        })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "bad");
  });

  it("says As intended in green and No faults in grey", () => {
    const { rerender } = render(
      <ReviewBadge
        review={reviewBlock({
          state: "reviewed",
          verdict: "as_intended",
          badge: "As intended",
        })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("As intended");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "good");
    rerender(
      <ReviewBadge
        review={reviewBlock({ state: "reviewed", verdict: "no_faults", badge: "No faults" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent("No faults");
    expect(screen.getByTestId("review-badge")).toHaveAttribute("data-tone", "muted");
  });

  it("opens the review when a reviewed badge is pressed, and the press never reaches the row", async () => {
    const onOpen = vi.fn();
    const row = vi.fn();
    render(
      // biome-ignore lint/a11y/useKeyWithClickEvents: a stand-in for the row's own toggle.
      // biome-ignore lint/a11y/noStaticElementInteractions: a stand-in for the row's own toggle.
      <div onClick={row}>
        <ReviewBadge review={reviewed()} onOpen={onOpen} />
      </div>,
    );
    const badge = screen.getByRole("button", { name: /decline\s*: unstable/ });
    await userEvent.click(badge);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(row).not.toHaveBeenCalled();
  });

  it("starts a review without the press reaching the row", async () => {
    const onStart = vi.fn();
    const row = vi.fn();
    render(
      // biome-ignore lint/a11y/useKeyWithClickEvents: a stand-in for the row's own toggle.
      // biome-ignore lint/a11y/noStaticElementInteractions: a stand-in for the row's own toggle.
      <div onClick={row}>
        <ReviewBadge review={reviewBlock()} onStart={onStart} />
      </div>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(row).not.toHaveBeenCalled();
  });

  it("lists the faults with their sentences on hover and ends the tooltip with the summary", () => {
    render(<ReviewBadge review={reviewed()} />);
    const lines = (screen.getByTestId("review-badge-wrap").getAttribute("title") ?? "").split("\n");
    expect(lines[0]).toMatch(/^decline: unstable — /);
    expect(lines[1]).toMatch(/^ramp: fast flow — /);
    expect(lines[lines.length - 1]).toBe(
      "The model's summary: A fast ramp and a wandering decline.",
    );
  });

  it("labels the summary under Failed to run as the earlier review's", () => {
    render(
      <ReviewBadge
        review={reviewBlock({
          state: "failed",
          badge: "Failed to run",
          reason: "timed out",
          summary: "A clean lever shot.",
        })}
      />,
    );
    const lines = (screen.getByTestId("review-badge-wrap").getAttribute("title") ?? "").split("\n");
    expect(lines).toEqual(["Failed to run: timed out", "Last review: A clean lever shot."]);
  });

  it("describes a button badge in hidden text, and a plain one in screen-reader text, once", () => {
    const { rerender } = render(<ReviewBadge review={reviewed()} onOpen={() => undefined} />);
    const list = screen.getByTestId("review-badge-list");
    expect(list).toHaveClass("hidden");
    expect(screen.getByRole("button", { name: /decline\s*: unstable/ })).toHaveAttribute(
      "aria-describedby",
      list.id,
    );
    rerender(<ReviewBadge review={reviewed()} />);
    expect(screen.getByTestId("review-badge-list")).toHaveClass("sr-only");
    expect(screen.getByTestId("review-badge")).not.toHaveAttribute("title");
  });

  it("cuts only the phase name, never the fault word or the count", () => {
    const phase = "Final push to the cup, long";
    render(
      <ReviewBadge
        review={reviewed({
          badge: `${phase}: early yield +1`,
          entries: [{ ...faults[0], phase, fault: "early yield" }, faults[1]],
        })}
        className="w-24"
      />,
    );
    const badge = screen.getByTestId("review-badge");
    const [box, count] = Array.from(badge.children);
    expect(box).toHaveAttribute("data-testid", "review-badge-text");
    expect(box).toHaveClass("flex", "min-w-0", "flex-1", "overflow-hidden");
    expect(screen.getByTestId("review-badge-phase")).toHaveClass("truncate", "min-w-0");
    const fault = screen.getByTestId("review-badge-fault");
    expect(fault).toHaveClass("shrink-0", "max-w-full", "truncate");
    expect(fault.className).not.toMatch(/(^|\s)shrink(\s|$|-\[)/);
    expect(count).toHaveTextContent("+1");
    expect(count).toHaveClass("shrink-0");
  });

  it("never shows a deterministic check: it renders only the review block it is given", () => {
    render(
      <ReviewBadge
        review={reviewBlock({ state: "reviewed", verdict: "no_faults", badge: "No faults" })}
      />,
    );
    expect(screen.getByTestId("review-badge")).toHaveTextContent(/^No faults$/);
  });
});
