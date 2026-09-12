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
