import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AnswerText } from "@/components/chat/citations";

function renderAnswer(text: string) {
  return render(
    <MemoryRouter>
      <AnswerText text={text} />
    </MemoryRouter>,
  );
}

describe("AnswerText renders markdown", () => {
  it.fails("renders bold as a strong element", () => {
    const { container } = renderAnswer("Grind **finer** next time.");
    expect(container.querySelector("strong")?.textContent).toBe("finer");
    expect(container.textContent).not.toContain("**");
  });

  it.fails("renders inline code as a code element", () => {
    const { container } = renderAnswer("Set `grind 12` today.");
    expect(container.querySelector("code")?.textContent).toBe("grind 12");
    expect(container.textContent).not.toContain("`");
  });

  it.fails("renders a heading", () => {
    renderAnswer("## Next steps\n\nGrind finer.");
    expect(screen.getByRole("heading", { name: "Next steps" })).toBeTruthy();
  });

  it.fails("renders a numbered list as an ol", () => {
    const { container } = renderAnswer("1. First\n2. Second");
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it.fails("renders a GFM table", () => {
    const { container } = renderAnswer("| Shot | Yield |\n|---|---|\n| 1 | 36 |\n| 2 | 38 |");
    const cells = [...container.querySelectorAll("td")].map((cell) => cell.textContent);
    expect(cells).toEqual(["1", "36", "2", "38"]);
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.textContent).not.toContain("|");
  });
});
