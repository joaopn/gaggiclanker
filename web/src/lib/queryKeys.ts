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
    samples: (id: string) => ["shots", "samples", id] as const,
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
  profiles: { all: ["profiles"] as const, list: () => ["profiles", "list"] as const },
  knowledge: { all: ["knowledge"] as const, list: () => ["knowledge", "list"] as const },
  imports: { all: ["imports"] as const, list: () => ["imports", "list"] as const },
  device: { all: ["device"] as const, status: () => ["device", "status"] as const },
} as const;
