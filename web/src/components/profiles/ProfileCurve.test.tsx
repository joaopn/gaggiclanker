import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfileCurve } from "@/components/profiles/ProfileCurve";

// The chart's module is evaluated only when something imports it, so a count here is a count of
// downloads of the chart's code, whatever else ran before.
const loaded = vi.hoisted(() => ({ times: 0 }));
vi.mock("@/components/charts/ProfileCurveChart", () => {
  loaded.times += 1;
  return {
    ProfileCurveChart: ({ title }: { title?: string }) => (
      <figure data-testid="profile-curve">
        <p data-testid="profile-curve-title">{title}</p>
      </figure>
    ),
  };
});

const pro = { type: "pro", phases: [{ duration: 4, pump: { target: "flow", flow: 4 } }] };

describe("ProfileCurve", () => {
  it("draws nothing, and never loads the chart, for a profile that is not pro", () => {
    for (const profile of [{ ...pro, type: "standard" }, { phases: [] }, null, undefined]) {
      const { container, unmount } = render(<ProfileCurve profile={profile} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
    expect(loaded.times).toBe(0);
  });

  it("loads the chart for a pro profile and hands it the title as plain text", async () => {
    render(<ProfileCurve profile={pro} title="<b>Active version</b>" />);
    expect(await screen.findByTestId("profile-curve")).toBeInTheDocument();
    expect(screen.getByTestId("profile-curve-title")).toHaveTextContent("<b>Active version</b>");
    expect(loaded.times).toBe(1);
  });
});
