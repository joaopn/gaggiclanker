import { screen } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it } from "vitest";
import { VersionPrediction } from "@/components/sets/VersionPrediction";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { version } from "@/test/setsFixtures";

const predicted = version({
  version_no: 2,
  prediction: "less bitter, a shorter shot",
  compares_to_version_id: 21,
  compares_to_version_no: 1,
});

describe("VersionPrediction", () => {
  it("does not render the text before the shot has been decided about", () => {
    renderWithQueryClient(<VersionPrediction shotId={1} version={predicted} decision={null} />);

    // Not merely hidden: a prediction sitting in the DOM while you taste is a
    // prediction that told you what to taste.
    expect(screen.queryByText("less bitter, a shorter shot")).not.toBeInTheDocument();
    expect(screen.getByTestId("version-prediction-row")).toHaveAttribute("data-shown", "no");
    // It still says a prediction exists, and against what.
    expect(screen.getByTestId("version-prediction-row")).toHaveTextContent("compared to v1");
  });

  it("reveals it for this view when asked", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionPrediction shotId={1} version={predicted} decision={null} />);

    const button = screen.getByRole("button", { name: "Show prediction" });
    // The region the button names exists before it is opened, so a reader can
    // follow `aria-controls` to it.
    const region = document.getElementById(button.getAttribute("aria-controls") ?? "");
    expect(region).not.toBeNull();

    await user.click(button);

    expect(screen.getByText("less bitter, a shorter shot")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show prediction" })).not.toBeInTheDocument();
  });

  it("shows it without a click once the shot carries a decision", () => {
    renderWithQueryClient(
      <VersionPrediction shotId={1} version={predicted} decision={"improve"} />,
    );

    expect(screen.getByText("less bitter, a shorter shot")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show prediction" })).not.toBeInTheDocument();
  });

  it("moves focus to what it revealed", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionPrediction shotId={1} version={predicted} decision={null} />);

    await user.click(screen.getByRole("button", { name: "Show prediction" }));

    // The button is gone; without this focus would fall back to the body and a
    // keyboard user would start again from the top of the page.
    expect(screen.getByText("less bitter, a shorter shot")).toHaveFocus();
  });

  it("hides itself again when the shot under it changes", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(
      <VersionPrediction shotId={1} version={predicted} decision={null} />,
    );
    await user.click(screen.getByRole("button", { name: "Show prediction" }));
    expect(screen.getByText("less bitter, a shorter shot")).toBeInTheDocument();

    // The same element, a different shot: React reuses the component, and a
    // reveal that carried over would show the next prediction unasked.
    rerender(
      <VersionPrediction
        shotId={2}
        version={version({
          id: 99,
          version_no: 3,
          prediction: "sweeter, the same time",
          compares_to_version_no: 2,
        })}
        decision={null}
      />,
    );

    expect(screen.queryByText("sweeter, the same time")).not.toBeInTheDocument();
    expect(screen.getByTestId("version-prediction-row")).toHaveAttribute("data-shown", "no");
    expect(screen.getByRole("button", { name: "Show prediction" })).toBeInTheDocument();
  });

  it("hides itself again when the same shot is moved to another version", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(
      <VersionPrediction shotId={1} version={predicted} decision={null} />,
    );
    await user.click(screen.getByRole("button", { name: "Show prediction" }));

    rerender(
      <VersionPrediction
        shotId={1}
        version={version({ id: 98, version_no: 4, prediction: "a different claim" })}
        decision={null}
      />,
    );

    expect(screen.queryByText("a different claim")).not.toBeInTheDocument();
  });

  /**
   * The same two resets again, under the double render the app really uses.
   *
   * `web/src/main.tsx` mounts StrictMode, so `npm run dev` renders every
   * component twice and throws the first pass away. A guard that remembers the
   * previous identity in a ref is written by that discarded pass: the second
   * pass then finds the identity already recorded and leaves the reveal
   * standing. These two fail against a ref-based guard and pass against one
   * held in state.
   */
  describe("under StrictMode's double render", () => {
    it("hides itself again when the shot under it changes", async () => {
      const user = setupUser();
      const { rerender } = renderWithQueryClient(
        <StrictMode>
          <VersionPrediction shotId={1} version={predicted} decision={null} />
        </StrictMode>,
      );
      await user.click(screen.getByRole("button", { name: "Show prediction" }));
      expect(screen.getByText("less bitter, a shorter shot")).toBeInTheDocument();

      rerender(
        <StrictMode>
          <VersionPrediction
            shotId={2}
            version={version({
              id: 99,
              version_no: 3,
              prediction: "sweeter, the same time",
              compares_to_version_no: 2,
            })}
            decision={null}
          />
        </StrictMode>,
      );

      expect(screen.queryByText("sweeter, the same time")).not.toBeInTheDocument();
      expect(screen.getByTestId("version-prediction-row")).toHaveAttribute("data-shown", "no");
    });

    it("hides itself again when the same shot is moved to another version", async () => {
      const user = setupUser();
      const { rerender } = renderWithQueryClient(
        <StrictMode>
          <VersionPrediction shotId={1} version={predicted} decision={null} />
        </StrictMode>,
      );
      await user.click(screen.getByRole("button", { name: "Show prediction" }));

      rerender(
        <StrictMode>
          <VersionPrediction
            shotId={1}
            version={version({ id: 98, version_no: 4, prediction: "a different claim" })}
            decision={null}
          />
        </StrictMode>,
      );

      expect(screen.queryByText("a different claim")).not.toBeInTheDocument();
    });
  });

  it("renders nothing at all for a version that predicted nothing", () => {
    renderWithQueryClient(<VersionPrediction shotId={1} version={version()} decision={null} />);
    expect(screen.queryByTestId("version-prediction-row")).not.toBeInTheDocument();

    // And nothing for a shot that is not in a Set.
    renderWithQueryClient(<VersionPrediction shotId={1} version={null} decision={"keep"} />);
    expect(screen.queryByTestId("version-prediction-row")).not.toBeInTheDocument();
  });
});
