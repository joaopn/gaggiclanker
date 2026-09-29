import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ResistanceSource, ShotDiagnosticsBlob } from "@/api/types";
import { ResistanceCard } from "@/components/shots/DiagnosticsCards";

function blob(source: ResistanceSource | undefined): ShotDiagnosticsBlob {
  return {
    diagnostics: {
      has_pressure: true,
      resistance: {
        source,
        avg: 2.1,
        std: 0.3,
        slope: -0.03,
        peak: 2.9,
        peak_timing_pct: 0.1,
        annotations: { level: "MODERATE" },
      },
    },
  };
}

describe("ResistanceCard source", () => {
  it("says the machine's own measurement was used", () => {
    render(<ResistanceCard diagnostics={blob("machine")} />);

    expect(screen.getByTestId("resistance-source")).toHaveTextContent("from the machine");
    expect(screen.getByTestId("band-level")).toHaveTextContent("2.1");
  });

  it("says ours was computed when the shot has no machine value", () => {
    render(<ResistanceCard diagnostics={blob("computed")} />);

    expect(screen.getByTestId("resistance-source")).toHaveTextContent(
      "computed from pressure and flow",
    );
  });

  it("says nothing about a source a stored shot never recorded", () => {
    render(<ResistanceCard diagnostics={blob(undefined)} />);

    expect(screen.queryByTestId("resistance-source")).toBeNull();
    expect(screen.getByTestId("band-level")).toBeInTheDocument();
  });
});
