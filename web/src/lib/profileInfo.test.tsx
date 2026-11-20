import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import { endsWhen, profileHeadline } from "@/lib/profileInfo";
import lever from "../../../tests/fixtures/profiles/docs-cremina-lever.json";
import medium from "../../../tests/fixtures/profiles/docs-medium-18g.json";
import ratioInexact from "../../../tests/fixtures/ratio_profile/ratio-2-1.json";
import ratioWithDose from "../../../tests/fixtures/ratio_profile/ratio-18g.json";
import ratioNoDose from "../../../tests/fixtures/ratio_profile/ratio-no-dose.json";
import ratioTie from "../../../tests/fixtures/ratio_profile/ratio-tie.json";

const phase = (duration: number, targets: unknown[] = []) => ({ duration, targets });

describe("what ends the shot", () => {
  it("is the volume of the last phase that has a volumetric target, as the firmware reads it", () => {
    const profile = {
      type: "pro",
      phases: [
        phase(5, [{ type: "pressure", operator: "gte", value: 4 }]),
        phase(30, [{ type: "volumetric", operator: "gte", value: 40 }]),
      ],
    };
    expect(endsWhen(profile)).toBe("target 40 g");
    expect(profileHeadline(profile)).toBe("pro · 2 phases · target 40 g");
  });

  it("names the volume even when the last phase has none, never the last phase's duration", () => {
    const profile = {
      phases: [phase(8, [{ type: "volumetric", operator: "gte", value: 10 }]), phase(1)],
    };
    expect(endsWhen(profile)).toBe("target 10 g");
  });

  it("a later volumetric target overrides an earlier one", () => {
    const profile = {
      phases: [
        phase(8, [{ type: "volumetric", value: 20 }]),
        phase(8, [{ type: "volumetric", value: 36 }]),
        phase(1),
      ],
    };
    expect(endsWhen(profile)).toBe("target 36 g");
  });

  it("reads 36 g for the Medium 18g 1:2 and the Cremina lever fixtures", () => {
    expect(endsWhen(medium)).toBe("target 36 g");
    expect(profileHeadline(medium)).toBe("standard · 5 phases · target 36 g");
    expect(endsWhen(lever)).toBe("target 36 g");
  });

  it("ends on the last phase's duration when no phase has a volume", () => {
    expect(endsWhen({ phases: [phase(9), phase(25)] })).toBe("ends after 25 s");
  });

  it("names a non-volume stop on the last phase, and says when there is none", () => {
    expect(endsWhen({ phases: [phase(9, [{ type: "flow", operator: "lte", value: 1 }])] })).toBe(
      "ends on flow ≤ 1",
    );
    expect(endsWhen({ phases: [] })).toBe("no stop condition");
    expect(endsWhen({ phases: [phase(0)] })).toBe("no stop condition");
  });
});

describe("the one-line summary and the landed profile summary read the same stop", () => {
  const fixtures = import.meta.glob<Record<string, unknown>>(
    "../../../tests/fixtures/profiles/*.json",
    { eager: true, import: "default" },
  );
  const lastVolume = (texts: string[]) => {
    let found: string | undefined;
    for (const text of texts) found = /volumetric ≥ (\d+(?:\.\d+)?)/.exec(text)?.[1] ?? found;
    return found;
  };

  it.each(Object.entries(fixtures))("%s", (_path, profile) => {
    const { unmount } = render(<ProfileSummary profile={profile} />);
    const phases = screen.queryAllByTestId("profile-summary-phase").map((e) => e.textContent ?? "");
    const ends = screen.getByTestId("profile-summary-ends").textContent ?? "";
    unmount();
    const headline = endsWhen(profile);

    const target = lastVolume(phases);
    if (target !== undefined) {
      // A target exists: the headline names it, as the target, and it is the summary's last one.
      expect(headline).toBe(`target ${target} g`);
    } else {
      // None: both name how the last phase ends.
      expect(headline).not.toMatch(/^target/);
      const after = /^ends after (\d+(?:\.\d+)?) s$/.exec(headline)?.[1];
      if (after !== undefined) expect(ends).toContain(`after ${after} s`);
      const on = /^ends on (\w+) (\S+) (\S+)$/.exec(headline);
      if (on) expect(ends).toContain(`${on[1]} ${on[2]} ${on[3]}`);
      if (headline === "no stop condition") expect(ends).not.toMatch(/after \d/);
    }
  });
});

describe("a ratio stop (firmware 1.10)", () => {
  // The server's `profile_recipe` is pinned on the same two files (`test_profile_recipe.py`): 36 g
  // with the dose, no yield without it.
  it("is a weight of ratio times dose, and the yield the server states", () => {
    expect(endsWhen(ratioWithDose)).toBe("target 36 g");
    expect(profileHeadline(ratioWithDose)).toBe("standard · 1 phase · target 36 g");
    render(<ProfileSummary profile={ratioWithDose} />);
    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends: ratio ≥ 2 (36 g with the 18 g dose), or after 28 s when volume is not measured.",
    );
    expect(screen.getByTestId("profile-summary-phase")).toHaveTextContent(
      "ends when ratio ≥ 2 (36 g with the 18 g dose)",
    );
  });

  it("rounds an exact tie to the even tenth, as the server does (2.5 x 14.5 is 36.2)", () => {
    expect(endsWhen(ratioTie)).toBe("target 36.2 g");
  });

  it("rounds an inexact product to a tenth of a gram, as the server does (2.1 x 18 is 37.8)", () => {
    expect(endsWhen(ratioInexact)).toBe("target 37.8 g");
  });

  it("with no dose never stops the shot, and says so", () => {
    expect(endsWhen(ratioNoDose)).toBe("ratio ≥ 2 — no dose set, so it never stops the shot");
    render(<ProfileSummary profile={ratioNoDose} />);
    expect(screen.getByTestId("profile-summary-ends")).toHaveTextContent(
      "Shot ends when the last phase ends: ratio ≥ 2 — no dose set, so it never stops the shot, or after 28 s.",
    );
  });
});
