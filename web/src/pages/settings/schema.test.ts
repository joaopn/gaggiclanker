import { describe, expect, it } from "vitest";
import type { SettingsMap } from "@/api/types";
import {
  buildSettingsSchema,
  humanizeKey,
  sectionFor,
  toFormValues,
  toPatch,
} from "@/pages/settings/schema";

const settings: SettingsMap = {
  host: {
    key: "host",
    type: "string",
    secret: false,
    value: "10.0.0.5",
    default: "",
    override: "10.0.0.5",
    source: "database",
    description: "",
  },
  enabled: {
    key: "enabled",
    type: "bool",
    secret: false,
    value: true,
    default: true,
    override: null,
    source: "default",
    description: "",
  },
  interval: {
    key: "interval",
    type: "int",
    secret: false,
    value: 60,
    default: 60,
    override: null,
    source: "environment",
    description: "",
  },
  apiKey: {
    key: "apiKey",
    type: "string",
    secret: true,
    configured: true,
    hint: "sk-p",
    source: "database",
    description: "",
  },
};

describe("toFormValues", () => {
  it("stringifies non-booleans and blanks every secret", () => {
    expect(toFormValues(settings)).toEqual({
      host: "10.0.0.5",
      enabled: true,
      interval: "60",
      apiKey: "",
    });
  });
});

describe("toPatch", () => {
  it("is empty when nothing changed", () => {
    expect(toPatch(settings, toFormValues(settings))).toEqual({});
  });

  it("sends only the changed keys, typed as the registry declares them", () => {
    const values = { ...toFormValues(settings), interval: "30", enabled: false };
    expect(toPatch(settings, values)).toEqual({ interval: 30, enabled: false });
  });

  it("treats a blank secret as 'keep' and a filled one as a write", () => {
    expect(toPatch(settings, { ...toFormValues(settings), apiKey: "" })).toEqual({});
    expect(toPatch(settings, { ...toFormValues(settings), apiKey: "sk-new" })).toEqual({
      apiKey: "sk-new",
    });
  });
});

describe("buildSettingsSchema", () => {
  const schema = buildSettingsSchema(settings);

  it("accepts the current values", () => {
    expect(schema.safeParse(toFormValues(settings)).success).toBe(true);
  });

  it("rejects a non-integer in an int field", () => {
    const result = schema.safeParse({ ...toFormValues(settings), interval: "1.5" });
    expect(result.success).toBe(false);
  });

  it("rejects a string where a bool is declared", () => {
    const result = schema.safeParse({ ...toFormValues(settings), enabled: "yes" });
    expect(result.success).toBe(false);
  });
});

describe("labels and sections", () => {
  it("turns a camelCase registry key into a sentence", () => {
    expect(humanizeKey("devicePollIntervalSeconds")).toBe("Device poll interval seconds");
    expect(humanizeKey("gaggimateHost")).toBe("Gaggimate host");
  });

  it("routes a key to a section by prefix, defaulting to General", () => {
    expect(sectionFor("gaggimateHost")).toBe("device");
    expect(sectionFor("deviceSyncEnabled")).toBe("device");
    expect(sectionFor("llmModel")).toBe("llm");
    expect(sectionFor("somethingNew")).toBe("general");
  });
});
