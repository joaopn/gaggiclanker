import { screen } from "@testing-library/react";
import { Coffee } from "lucide-react";
import { describe, expect, it } from "vitest";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

describe("PageHeader", () => {
  it("renders the title as the page heading, with the optional slots", () => {
    renderWithQueryClient(
      <PageHeader
        title="Shots"
        subtitle="Everything the machine pulled"
        actions={<button type="button">New</button>}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Shots" })).toBeInTheDocument();
    expect(screen.getByText("Everything the machine pulled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New" })).toBeInTheDocument();
  });

  it("omits the subtitle row when there is none", () => {
    renderWithQueryClient(<PageHeader title="Shots" />);
    expect(screen.getByRole("heading", { name: "Shots" })).toBeInTheDocument();
  });
});

describe("SectionCard", () => {
  it("renders its title, description and children", () => {
    renderWithQueryClient(
      <SectionCard title="Machine" description="How we reach it">
        <p>body</p>
      </SectionCard>,
    );
    expect(screen.getByText("Machine")).toBeInTheDocument();
    expect(screen.getByText("How we reach it")).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("explains the emptiness rather than leaving a blank panel", () => {
    renderWithQueryClient(
      <EmptyState
        icon={Coffee}
        title="No shots yet"
        description="Sync lands later."
        action={<button type="button">Retry</button>}
      />,
    );
    expect(screen.getByText("No shots yet")).toBeInTheDocument();
    expect(screen.getByText("Sync lands later.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
