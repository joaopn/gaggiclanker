import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VersionPrediction } from "@/components/recipes/VersionPrediction";
import { version } from "@/test/recipesFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

const predicted = version({
  version_major: 2,
  prediction: "less bitter, a shorter shot",
  compares_to_version_id: 21,
  compares_to_version_label: "v1",
});

describe("VersionPrediction", () => {
  it("shows the prediction, and what it is compared to, with nothing to click first", () => {
    renderWithQueryClient(<VersionPrediction version={predicted} />);

    expect(screen.getByText("less bitter, a shorter shot")).toBeInTheDocument();
    expect(screen.getByTestId("version-prediction-row")).toHaveTextContent("compared to v1");
    // No reveal and no hidden state, anywhere.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("version-prediction-row")).not.toHaveAttribute("data-shown");
    expect(screen.queryByText(/hidden until/i)).not.toBeInTheDocument();
  });

  it("names the version it is the prediction of", () => {
    renderWithQueryClient(<VersionPrediction version={predicted} />);

    expect(screen.getByTestId("version-prediction-row")).toHaveTextContent(
      "Version prediction for v2",
    );
  });

  it("renders nothing for a version that predicted nothing, and nothing with no Recipe", () => {
    const { container } = renderWithQueryClient(<VersionPrediction version={version()} />);
    expect(container).toBeEmptyDOMElement();

    const none = renderWithQueryClient(<VersionPrediction version={null} />);
    expect(none.container).toBeEmptyDOMElement();
  });

  it("never carries the evidence: that is the Recipe page's log, not the shot", () => {
    renderWithQueryClient(<VersionPrediction version={predicted} />);

    expect(screen.queryByRole("button", { name: "Evidence" })).not.toBeInTheDocument();
    expect(screen.queryByText(/the spread/)).not.toBeInTheDocument();
  });
});
