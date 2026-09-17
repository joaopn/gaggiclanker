import { render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Combobox, matchingOptions } from "@/components/ui/combobox";
import { setupUser } from "@/test/renderWithQueryClient";

const ROASTERS = ["Assembly", "Hasbean", "Round Hill", "Square Mile"];

/** A form around the combobox, so Enter's submit and Escape's bubbling are real. */
function Harness({
  onSubmit,
  onOuterKeyDown,
  initial = "",
}: {
  onSubmit: (value: string) => void;
  onOuterKeyDown?: (key: string) => void;
  initial?: string;
}) {
  const [value, setValue] = useState(initial);
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a test's stand-in for a dialog that listens for Escape
    <div onKeyDown={(event) => onOuterKeyDown?.(event.key)}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(value);
        }}
      >
        <label htmlFor="roaster">Roaster</label>
        <Combobox
          id="roaster"
          value={value}
          onValueChange={setValue}
          options={ROASTERS}
          listLabel="Roasters already recorded"
        />
        <button type="submit">Save</button>
      </form>
    </div>
  );
}

function listbox() {
  return screen.getByRole("listbox", { hidden: true });
}

describe("matchingOptions", () => {
  it("matches a substring without case, and leaves out an exact match", () => {
    expect(matchingOptions(ROASTERS, "h")).toEqual(["Hasbean", "Round Hill"]);
    expect(matchingOptions(ROASTERS, "hasbean")).toEqual([]);
    expect(matchingOptions(ROASTERS, "")).toEqual(ROASTERS);
    expect(matchingOptions(["a", "ab", "abc"], "", 2)).toEqual(["a", "ab"]);
  });
});

describe("Combobox", () => {
  it("is closed until something is typed, then filters what it offers", async () => {
    const user = setupUser();
    render(<Harness onSubmit={vi.fn()} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.click(input);
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(listbox()).not.toBeVisible();
    expect(input).toHaveAttribute("aria-controls", listbox().id);
    expect(input).toHaveAttribute("aria-autocomplete", "list");

    await user.type(input, "h");
    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Hasbean",
      "Round Hill",
    ]);

    await user.type(input, "il");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Round Hill",
    ]);
  });

  it("picks an option with the mouse", async () => {
    const user = setupUser();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.type(input, "squ");
    await user.click(screen.getByRole("option", { name: "Square Mile" }));

    expect(input).toHaveValue("Square Mile");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("picks with the arrows and Enter without submitting the form", async () => {
    const user = setupUser();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.type(input, "h");
    await user.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute(
      "aria-activedescendant",
      screen.getByRole("option", { name: "Hasbean" }).id,
    );
    expect(screen.getByRole("option", { name: "Hasbean" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Both ends wrap: down past the last comes back to the first, up past the
    // first goes to the last.
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("option", { name: "Hasbean" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("option", { name: "Round Hill" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.keyboard("{ArrowUp}");
    await user.keyboard("{Enter}");

    expect(input).toHaveValue("Hasbean");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).not.toHaveAttribute("aria-activedescendant");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("opens on ArrowDown when closed", async () => {
    const user = setupUser();
    render(<Harness onSubmit={vi.fn()} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.click(input);
    await user.keyboard("{ArrowDown}");

    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("option")).toHaveLength(ROASTERS.length);
  });

  it("submits on Enter when no option is active, with the free text as typed", async () => {
    const user = setupUser();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.type(input, "Has Bean & Co");
    await user.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledWith("Has Bean & Co");
  });

  it("closes on Escape without letting the key reach anything around it", async () => {
    const user = setupUser();
    const outer = vi.fn();
    render(<Harness onSubmit={vi.fn()} onOuterKeyDown={outer} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.type(input, "h");
    outer.mockClear();
    await user.keyboard("{Escape}");

    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveValue("h");
    expect(outer).not.toHaveBeenCalledWith("Escape");

    // With the list already closed, Escape is somebody else's again.
    await user.keyboard("{Escape}");
    expect(outer).toHaveBeenCalledWith("Escape");
  });

  it("closes when focus leaves", async () => {
    const user = setupUser();
    render(<Harness onSubmit={vi.fn()} />);
    const input = screen.getByRole("combobox", { name: "Roaster" });

    await user.type(input, "h");
    await user.tab();

    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(listbox()).not.toBeVisible();
  });
});
