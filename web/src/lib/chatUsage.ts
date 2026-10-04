/**
 * How a chat run's token figures are worded on screen.
 *
 * The server stores them on each run (`usage`); every figure is a count of
 * tokens. Two numbers must not be confused: `prompt_tokens` is the input billed
 * over all the run's requests (each tool round re-sends the conversation), and
 * `context_tokens` is the last request's input, which is how big the
 * conversation was. Only the second is shown as a size.
 *
 * A part is worded only when the provider reported it. A missing figure is left
 * out; it is never shown as zero.
 */

export type RunUsage = Record<string, unknown> | null | undefined;

type PerRequest = { context?: number; cache_read?: number; out?: number };

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function perRequest(usage: RunUsage): PerRequest[] {
  const raw = usage?.per_request;
  return Array.isArray(raw) ? (raw as PerRequest[]) : [];
}

/**
 * Tokens, short: under 1000 as is, then one decimal below 10k ("9.6k"), then
 * whole thousands ("55k"). A trailing ".0" is dropped ("1k", not "1.0k").
 */
export function formatTokens(count: number): string {
  if (count < 1000) return String(Math.round(count));
  if (count < 10_000) return `${(Math.round(count / 100) / 10).toString()}k`;
  return `${Math.round(count / 1000)}k`;
}

/** The size of the conversation at the run's last request, or null if unreported. */
export function contextOf(usage: RunUsage): number | null {
  const stored = num(usage?.context_tokens);
  if (stored !== null) return stored;
  const last = perRequest(usage).at(-1);
  return num(last?.context);
}

/**
 * `Context 55k of 200k tokens (92% cached)`.
 *
 * "of N" only where the provider reports a window (Claude Code); the share is
 * the last request's cache read over its size and only where the cache figure
 * was reported. Null (no footer) when the size is unknown, which is also what a
 * run stored before these figures existed gives.
 */
export function contextFooter(usage: RunUsage): string | null {
  const context = contextOf(usage);
  if (context === null) return null;
  const window = num(usage?.context_window);
  let text = `Context ${formatTokens(context)}`;
  if (window !== null && window > 0) text += ` of ${formatTokens(window)}`;
  text += " tokens";
  const read = num(perRequest(usage).at(-1)?.cache_read);
  if (read !== null && context > 0) {
    text += ` (${Math.round((read / context) * 100)}% cached)`;
  }
  return text;
}

/** The latest usage worth a footer: the newest run that reported a size. */
export function latestUsage(runs: Array<{ usage?: RunUsage }>): RunUsage {
  for (let index = runs.length - 1; index >= 0; index--) {
    const usage = runs[index].usage;
    if (contextOf(usage) !== null) return usage;
  }
  return null;
}

/**
 * `10 requests · context 28k → 55k · 9.6k out`, or `1 request · context 28k · 0.4k out`.
 *
 * Requests are API requests (not the CLI's turn count); "context a → b" is the
 * first and last request's size. Each part is dropped when unreported.
 */
export function answerLine(usage: RunUsage): string | null {
  const parts: string[] = [];
  const requests = perRequest(usage);
  const count = num(usage?.requests) ?? (requests.length > 0 ? requests.length : null);
  if (count !== null) parts.push(`${count} request${count === 1 ? "" : "s"}`);
  const first = num(requests[0]?.context);
  const last = contextOf(usage);
  if (last !== null) {
    parts.push(
      first !== null && first !== last
        ? `context ${formatTokens(first)} → ${formatTokens(last)}`
        : `context ${formatTokens(last)}`,
    );
  }
  const out = num(usage?.completion_tokens);
  if (out !== null) parts.push(`${formatTokens(out)} out`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
