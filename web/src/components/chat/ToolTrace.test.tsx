import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ToolTrace } from "@/components/chat/ToolTrace";

const PERMISSIONS = { propose_recipe_version: "propose", get_recipe: "read" };

function trace(name: string) {
  return [{ id: "1", name, arguments: {}, ok: true, content: "{}", durationMs: 3 }];
}

describe("the permission label on a tool call", () => {
  it("says proposal for the tool under its current name", () => {
    render(
      <MemoryRouter>
        <ToolTrace entries={trace("propose_recipe_version")} permissions={PERMISSIONS} />
      </MemoryRouter>,
    );
    expect(screen.getByText("proposal")).toBeInTheDocument();
  });

  it("says proposal for a call stored under the name the tool had before the rename", () => {
    render(
      <MemoryRouter>
        <ToolTrace entries={trace("propose_set_version")} permissions={PERMISSIONS} />
      </MemoryRouter>,
    );
    expect(screen.getByText("propose_set_version")).toBeInTheDocument();
    expect(screen.getByText("proposal")).toBeInTheDocument();
  });

  it("labels a stored read call with nothing", () => {
    render(
      <MemoryRouter>
        <ToolTrace entries={trace("get_set")} permissions={PERMISSIONS} />
      </MemoryRouter>,
    );
    expect(screen.queryByText("proposal")).toBeNull();
  });
});
