import { describe, expect, it } from "vitest";
import { endsWhen, profileHeadline } from "@/lib/profileInfo";

const phase = (duration: number, targets: unknown[] = []) => ({ duration, targets });

describe("what ends the shot", () => {
  it("is how the last phase ends, not an earlier phase's exit", () => {
    const profile = {
      type: "pro",
      phases: [
        phase(5, [{ type: "pressure", operator: "gte", value: 4 }]),
        phase(30, [{ type: "volumetric", operator: "gte", value: 40 }]),
      ],
    };
    expect(endsWhen(profile)).toBe("ends at 40 g");
    expect(profileHeadline(profile)).toBe("pro · 2 phases · ends at 40 g");
  });

  it("ignores a volume stop that only ends an earlier phase", () => {
    const profile = {
      phases: [phase(8, [{ type: "volumetric", operator: "gte", value: 10 }]), phase(25)],
    };
    expect(endsWhen(profile)).toBe("ends after 25 s");
  });

  it("names a non-volume stop on the last phase, and says when there is none", () => {
    expect(endsWhen({ phases: [phase(9, [{ type: "flow", operator: "lte", value: 1 }])] })).toBe(
      "ends on flow ≤ 1",
    );
    expect(endsWhen({ phases: [] })).toBe("no stop condition");
    expect(endsWhen({ phases: [phase(0)] })).toBe("no stop condition");
  });
});
