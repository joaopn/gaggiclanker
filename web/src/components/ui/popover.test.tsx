import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "@/components/ui/button";
import { anchoredPosition, Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { setupUser } from "@/test/renderWithQueryClient";

/**
 * The popover primitive, which is this project's own rather than radix's — see
 * the note at the top of `popover.tsx` and in `web/README.md`.
 *
 * The positioning is tested as arithmetic rather than through the DOM because
 * jsdom reports every rectangle as zero, so a rendered panel cannot tell a
 * clamp from a no-op. Everything else — focus, Escape, click-outside, the
 * scroll that closes an anchored panel — is real behaviour and is driven
 * through the DOM.
 */

const VIEWPORT = { width: 1000, height: 800 };

/** An anchor `height` tall whose top is `top` from the top of the viewport. */
function anchor(top: number, height = 24, left = 100, width = 28) {
  return { top, bottom: top + height, left, right: left + width };
}

describe("anchoredPosition", () => {
  it("hangs below the anchor when there is room", () => {
    const style = anchoredPosition(anchor(100), 290, "start", VIEWPORT);
    expect(style.top).toBe(128);
    expect(style.left).toBe(100);
    expect(style.visibility).toBe("visible");
  });

  it("flips above the anchor when the panel would run off the bottom", () => {
    // A row two thirds down a tall list: below is 718 + 290 = off the screen,
    // and a fixed panel cannot be scrolled to, because neither the page's
    // scroll nor the list's moves it.
    const style = anchoredPosition(anchor(694), 290, "start", VIEWPORT);
    expect(style.top).toBe(694 - 4 - 290);
    expect(Number(style.top) + 290).toBeLessThanOrEqual(VIEWPORT.height);
  });

  it("clamps into the viewport when it fits neither above nor below", () => {
    // A panel taller than the space on either side of its anchor. It is pinned
    // to the bottom margin and scrolls its own content — the class that does
    // that is on the panel.
    const style = anchoredPosition(anchor(300), 700, "start", VIEWPORT);
    expect(style.top).toBe(VIEWPORT.height - 700 - 8);
    expect(Number(style.top)).toBeGreaterThanOrEqual(8);
  });

  it("never puts the top above the margin, however tall the panel", () => {
    const style = anchoredPosition(anchor(700), 5000, "start", VIEWPORT);
    expect(Number(style.top)).toBeGreaterThanOrEqual(8);
  });

  it("measures from the right edge when aligned to the end", () => {
    const style = anchoredPosition(anchor(100, 24, 940, 28), 100, "end", VIEWPORT);
    expect(style.right).toBe(VIEWPORT.width - 968);
    expect(style.left).toBeUndefined();
  });

  it("keeps an anchor hard against an edge off the edge", () => {
    expect(anchoredPosition(anchor(100, 24, -40), 100, "start", VIEWPORT).left).toBe(8);
    expect(anchoredPosition(anchor(100, 24, 980, 40), 100, "end", VIEWPORT).right).toBe(8);
  });
});

function Fixture({ anchored = false }: { anchored?: boolean }) {
  return (
    <div>
      <button type="button" data-testid="outside">
        elsewhere
      </button>
      <Popover>
        <PopoverTrigger asChild>
          <Button data-testid="trigger">Open</Button>
        </PopoverTrigger>
        <PopoverContent anchored={anchored} aria-label="A panel" data-testid="panel">
          <button type="button" data-testid="inside">
            do a thing
          </button>
        </PopoverContent>
      </Popover>
    </div>
  );
}

describe("Popover", () => {
  it("moves focus into the panel on open and back to the trigger on Escape", async () => {
    const user = setupUser();
    render(<Fixture />);

    await user.click(screen.getByTestId("trigger"));
    expect(screen.getByTestId("inside")).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("trigger")).toHaveFocus();
  });

  it("returns focus to the trigger when the panel closes itself", async () => {
    // A Save or Cancel button inside the panel flips the state and is then
    // removed; without this, focus lands on `<body>` and a keyboard user has
    // lost their place in a thousand-row list.
    const user = setupUser();
    render(<Fixture />);

    await user.click(screen.getByTestId("trigger"));
    await user.click(screen.getByTestId("trigger"));

    expect(screen.queryByTestId("panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("trigger")).toHaveFocus();
  });

  it("leaves focus alone when the click that closed it went somewhere else", async () => {
    const user = setupUser();
    render(<Fixture />);

    await user.click(screen.getByTestId("trigger"));
    await user.click(screen.getByTestId("outside"));

    expect(screen.queryByTestId("panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("outside")).toHaveFocus();
  });

  it("names the panel and says the trigger is expanded", async () => {
    const user = setupUser();
    render(<Fixture />);

    expect(screen.getByTestId("trigger")).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByTestId("trigger"));

    expect(screen.getByRole("dialog", { name: "A panel" })).toBeInTheDocument();
    expect(screen.getByTestId("trigger")).toHaveAttribute("aria-expanded", "true");
  });

  it("closes an anchored panel as soon as anything scrolls", async () => {
    // It is positioned once against a rectangle that is about to move, and in
    // a virtualised list the row under it is about to be unmounted.
    const user = setupUser();
    render(<Fixture anchored />);

    await user.click(screen.getByTestId("trigger"));
    expect(screen.getByTestId("panel")).toBeInTheDocument();

    fireEvent.scroll(document.body);

    expect(screen.queryByTestId("panel")).not.toBeInTheDocument();
  });

  it("does not close an anchored panel that is scrolling its own content", async () => {
    const user = setupUser();
    render(<Fixture anchored />);

    await user.click(screen.getByTestId("trigger"));
    fireEvent.scroll(screen.getByTestId("panel"));

    expect(screen.getByTestId("panel")).toBeInTheDocument();
  });

  it("leaves a panel that is not anchored alone when the page scrolls", async () => {
    // The filter panel is positioned by CSS against its own wrapper, so it
    // travels with the page and has no reason to close.
    const user = setupUser();
    render(<Fixture />);

    await user.click(screen.getByTestId("trigger"));
    fireEvent.scroll(document.body);

    expect(screen.getByTestId("panel")).toBeInTheDocument();
  });
});
