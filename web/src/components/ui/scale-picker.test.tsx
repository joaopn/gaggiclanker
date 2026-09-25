import { render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ScalePicker } from "@/components/ui/scale-picker";
import { setupUser } from "@/test/renderWithQueryClient";

function Harness({
  initial = null,
  onChange,
}: {
  initial?: number | null;
  onChange?: (value: number | null) => void;
}) {
  const [value, setValue] = useState<number | null>(initial);
  return (
    <ScalePicker
      label="Acidity"
      name="acidity"
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

function pressed(): string[] {
  return screen
    .getAllByRole("button")
    .filter((button) => button.getAttribute("aria-pressed") === "true")
    .map((button) => button.textContent ?? "");
}

describe("ScalePicker", () => {
  it("is one field named by its legend, with five steps", () => {
    render(<Harness />);

    const group = screen.getByRole("group", { name: "Acidity" });
    expect(group.tagName).toBe("FIELDSET");
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
    ]);
    expect(pressed()).toEqual([]);
    expect(group).toHaveTextContent("not stated");
  });

  it("picks the step clicked, and clicking it again clears it", async () => {
    const user = setupUser();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Acidity 4 of 5" }));
    expect(onChange).toHaveBeenLastCalledWith(4);
    expect(pressed()).toEqual(["4"]);

    // Another step moves the value rather than clearing it.
    await user.click(screen.getByRole("button", { name: "Acidity 2 of 5, currently 4" }));
    expect(onChange).toHaveBeenLastCalledWith(2);

    await user.click(screen.getByRole("button", { name: "Clear the acidity" }));
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(pressed()).toEqual([]);
  });

  it("fills the steps up to the value, so it reads as a level", () => {
    render(<Harness initial={3} />);

    const filled = screen
      .getAllByRole("button")
      .filter((button) => button.className.includes("bg-primary"))
      .map((button) => button.textContent);
    expect(filled).toEqual(["1", "2", "3"]);
    expect(pressed()).toEqual(["3"]);
  });

  it("reaches every step with the keyboard", async () => {
    const user = setupUser();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);

    for (let step = 1; step <= 5; step += 1) await user.tab();
    await user.keyboard(" ");

    expect(onChange).toHaveBeenLastCalledWith(5);
  });

  it("never submits the form it sits in", async () => {
    const user = setupUser();
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Harness />
      </form>,
    );

    await user.click(screen.getByRole("button", { name: "Acidity 3 of 5" }));

    expect(onSubmit).not.toHaveBeenCalled();
  });
});
