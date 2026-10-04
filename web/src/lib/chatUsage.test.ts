import { describe, expect, it } from "vitest";
import { answerLine, contextFooter, formatTokens, latestUsage } from "@/lib/chatUsage";

describe("formatTokens", () => {
  it.each([
    [0, "0"],
    [842, "842"],
    [999, "999"],
    [1000, "1k"],
    [1049, "1k"],
    [9600, "9.6k"],
    [9949, "9.9k"],
    [9950, "10k"],
    [10_000, "10k"],
    [55_432, "55k"],
    [200_000, "200k"],
    [999_499, "999k"],
    [999_500, "1M"],
    [1_000_000, "1M"],
    [1_500_000, "1.5M"],
    [1_549_999, "1.5M"],
    [12_400_000, "12M"],
  ])("%i is %s", (count, text) => {
    expect(formatTokens(count)).toBe(text);
  });
});

const claudeCode = {
  prompt_tokens: 85_950,
  completion_tokens: 9600,
  context_tokens: 55_000,
  context_window: 200_000,
  requests: 10,
  per_request: [
    { context: 28_000, cache_read: 20_000, out: 100 },
    { context: 55_000, cache_read: 50_600, out: 90 },
  ],
};

describe("contextFooter", () => {
  it("names the size, the window and the cached share of the last request", () => {
    expect(contextFooter(claudeCode)).toBe("Context 55k of 200k tokens (92% cached)");
  });

  it("leaves out the window where the provider reports none", () => {
    expect(contextFooter({ ...claudeCode, context_window: undefined })).toBe(
      "Context 55k tokens (92% cached)",
    );
    expect(contextFooter({ ...claudeCode, context_window: null })).toBe(
      "Context 55k tokens (92% cached)",
    );
  });

  it("leaves out the percentage where no cache figure was reported", () => {
    const usage = { ...claudeCode, per_request: [{ context: 28_000 }, { context: 55_000 }] };
    expect(contextFooter(usage)).toBe("Context 55k of 200k tokens");
  });

  it("shows 0% when the provider said nothing was cached", () => {
    const usage = { context_tokens: 1443, per_request: [{ context: 1443, cache_read: 0 }] };
    expect(contextFooter(usage)).toBe("Context 1.4k tokens (0% cached)");
  });

  it("is absent with no usage, or only the old summed totals", () => {
    expect(contextFooter(null)).toBeNull();
    expect(contextFooter(undefined)).toBeNull();
    expect(contextFooter({ prompt_tokens: 1200, completion_tokens: 80 })).toBeNull();
  });

  it("reads the size from the last request when the run kept no separate key", () => {
    expect(contextFooter({ per_request: [{ context: 800 }, { context: 900 }] })).toBe(
      "Context 900 tokens",
    );
  });
});

describe("latestUsage", () => {
  it("takes the newest run that reported a size, skipping a failed one without usage", () => {
    const runs = [{ usage: { context_tokens: 10 } }, { usage: claudeCode }, { usage: null }];
    expect(latestUsage(runs)).toBe(claudeCode);
    expect(latestUsage([{ usage: null }])).toBeNull();
  });
});

describe("answerLine", () => {
  it("words requests, the first and last context, and the output", () => {
    expect(answerLine(claudeCode)).toBe("10 requests · context 28k → 55k · 9.6k out");
  });

  it("says request in the singular, with one context", () => {
    const usage = {
      requests: 1,
      context_tokens: 28_000,
      completion_tokens: 400,
      per_request: [{ context: 28_000 }],
    };
    expect(answerLine(usage)).toBe("1 request · context 28k · 400 out");
  });

  it("drops what the provider did not report", () => {
    expect(answerLine({ completion_tokens: 80 })).toBe("80 out");
    expect(answerLine(null)).toBeNull();
    expect(answerLine({})).toBeNull();
  });

  it("collapses a range whose ends read the same once shown", () => {
    const usage = {
      requests: 2,
      context_tokens: 21_400,
      per_request: [{ context: 21_146 }, { context: 21_400 }],
    };
    expect(answerLine(usage)).toBe("2 requests · context 21k");
  });

  it("shows one context when the first and last requests were the same size", () => {
    const usage = { requests: 2, per_request: [{ context: 900 }, { context: 900 }] };
    expect(answerLine(usage)).toBe("2 requests · context 900");
  });
});
