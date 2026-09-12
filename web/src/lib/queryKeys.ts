/**
 * The one place query keys are spelled.
 *
 * A key typed inline in a component is a cache entry nothing else can
 * invalidate, and the bug shows up as "the UI does not refresh" long after the
 * commit that caused it. Every `useQuery` takes its key from here.
 *
 * Keys are hierarchical: `queryKeys.shots.all` is a prefix of
 * `queryKeys.shots.detail(id)`, so invalidating the former also invalidates
 * every detail. Later chunks append their own namespaces.
 */
export const queryKeys = {
  health: () => ["health"] as const,
  settings: {
    all: ["settings"] as const,
    current: () => ["settings", "current"] as const,
  },
  shots: {
    all: ["shots"] as const,
    list: (filters?: Record<string, unknown>) => ["shots", "list", filters ?? {}] as const,
    detail: (id: string) => ["shots", "detail", id] as const,
  },
  /**
   * Curves live *outside* the `shots` prefix, and that placement is the point.
   *
   * Samples are immutable once a shot is ingested, but TanStack refetches
   * every **active** query a prefix match touches regardless of staleTime — so
   * while `shots.samples(...)` sat under `["shots"]`, one `shot.ingested`
   * during a backfill re-fetched every sparkline currently on screen and any
   * open detail curve. Fifty shots landing meant thousands of pointless
   * requests against an appliance.
   *
   * `downsample` is part of the key too: a 40-point sparkline and the full
   * curve are two different answers to the same question, and a cache that
   * mixed them would draw a sparkline on the detail page.
   */
  samples: {
    all: ["samples"] as const,
    shot: (id: string) => ["samples", id] as const,
    curve: (id: string, downsample?: number) => ["samples", id, downsample ?? "all"] as const,
  },
  sync: {
    all: ["sync"] as const,
    status: () => ["sync", "status"] as const,
  },
  sets: {
    all: ["sets"] as const,
    list: () => ["sets", "list"] as const,
    detail: (id: string) => ["sets", "detail", id] as const,
  },
  beans: { all: ["beans"] as const, list: () => ["beans", "list"] as const },
  hardware: { all: ["hardware"] as const, list: () => ["hardware", "list"] as const },
  profiles: {
    all: ["profiles"] as const,
    list: () => ["profiles", "list"] as const,
    versions: (filters?: Record<string, unknown>) =>
      ["profiles", "versions", filters ?? {}] as const,
  },
  knowledge: { all: ["knowledge"] as const, list: () => ["knowledge", "list"] as const },
  imports: { all: ["imports"] as const, list: () => ["imports", "list"] as const },
  device: { all: ["device"] as const, status: () => ["device", "status"] as const },
} as const;
