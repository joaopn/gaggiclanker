import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { invalidateShots } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

describe("a shot's fields", () => {
  it("are read again whenever the shot is, because filing it changes its warnings and shares", async () => {
    // The warnings and the share of the target depend on the version the shot is
    // filed under, so a write that invalidates the shot's detail has to reach them.
    const client = new QueryClient();
    client.setQueryData(queryKeys.shots.fields("7"), { shot_id: 7 });
    client.setQueryData(queryKeys.shots.fields("8"), { shot_id: 8 });

    await invalidateShots(client, "7");

    expect(client.getQueryState(queryKeys.shots.fields("7"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.shots.fields("8"))?.isInvalidated).toBe(false);

    await invalidateShots(client);
    expect(client.getQueryState(queryKeys.shots.fields("8"))?.isInvalidated).toBe(true);
  });
});
