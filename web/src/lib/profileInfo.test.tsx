import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import { endsWhen, profileHeadline } from "@/lib/profileInfo";
import lever from "../../../tests/fixtures/profiles/docs-cremina-lever.json";
import medium from "../../../tests/fixtures/profiles/docs-medium-18g.json";

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

  it("says the target, not what ends the shot, for Medium 18g 1:2 (Hammer runs after 36 g)", () => {
    expect(endsWhen(medium)).toBe("target 36 g");
    expect(endsWhen(medium)).not.toMatch(/ends/);
  });
});
