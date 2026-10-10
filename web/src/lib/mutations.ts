/**
 * Run a mutation and swallow its rejection.
 *
 * `mutateAsync` rejects as well as calling the hook's `onError`, so an `await`
 * in a click handler produces an unhandled rejection on top of the toast the
 * user already saw — noise in the console in development, and a reported error
 * with no cause in production. The failure is already handled where it belongs;
 * this is only about not reporting it twice. Returns `undefined` on failure, so
 * a caller that chains a second step stops rather than carrying on with nothing.
 */
export async function attempt<T>(run: () => Promise<T>): Promise<T | undefined> {
  try {
    return await run();
  } catch {
    return undefined;
  }
}

/**
 * What to say when a request got no answer (the browser's fetch failed): the one thing that is
 * not known is whether the write happened, since the app may have done it and the answer been
 * lost. So it names no outcome, only where the truth is. `where` is the place that shows it.
 */
export function noAnswerSentence(where: string): string {
  return `No answer came back from the app. Check ${where} before trying again.`;
}
