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
  /**
   * Outside every other namespace, and it has to be: signing out clears the
   * whole cache, and the answer to "does this server want a token" is the one
   * thing that must survive being cleared long enough to render the form.
   */
  auth: {
    status: () => ["auth", "status"] as const,
  },
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
    list: (includeArchived?: boolean) => ["sets", "list", includeArchived ?? false] as const,
    detail: (id: string) => ["sets", "detail", id] as const,
    /**
     * Under the `sets` prefix on purpose, unlike `samples` under `shots`.
     *
     * A Set's trends change whenever one of its shots does — a judgement
     * saved, a shot reassigned — and both of those already invalidate
     * `sets.all`. There is exactly one chart on screen at a time, so the
     * over-fetch the shots list could not afford costs one request here.
     */
    trends: (id: string) => ["sets", "trends", id] as const,
  },
  beans: {
    all: ["beans"] as const,
    list: (includeArchived?: boolean) => ["beans", "list", includeArchived ?? false] as const,
    /**
     * The similar-Set cards for a bag, on one grinder.
     *
     * Under the `beans` prefix rather than under `startingPoints`, because it
     * is a fact about the bean and it is read whether or not anybody ever asks
     * the model anything. The grinder is in the key because changing it changes
     * the answer completely — the query filters on it.
     */
    similarSets: (id: string, grinderId?: number | null) =>
      ["beans", "similar-sets", id, grinderId ?? null] as const,
  },
  /** The grinders and the machine: one page, one prefix, so one invalidation. */
  hardware: {
    all: ["hardware"] as const,
    grinders: () => ["hardware", "grinders"] as const,
    machine: () => ["hardware", "machine"] as const,
  },
  /**
   * The closed vocabularies. Fetched once and never invalidated: they change
   * with a redeploy, and a refetch on every page would be a request that can
   * only ever return the same bytes.
   */
  vocab: { all: ["vocab"] as const, current: () => ["vocab", "current"] as const },
  /**
   * Which wheel notes the shot panel offers. Not under `vocab`: the wheel is
   * fixed, this is a person's choice from it and changes on the Taste wheel
   * page.
   */
  flavorPicks: { all: ["flavor-picks"] as const },
  profiles: {
    all: ["profiles"] as const,
    list: () => ["profiles", "list"] as const,
    versions: (filters?: Record<string, unknown>) =>
      ["profiles", "versions", filters ?? {}] as const,
    // Its own prefix rather than a member of `versions`: a version document is
    // immutable, and keying it under the list would refetch it every time the
    // list is invalidated by a push.
    version: (id: string) => ["profiles", "version", id] as const,
  },
  /** The wizard's runs. One entry per run id; there is no list the UI renders. */
  startingPoints: {
    all: ["starting-points"] as const,
    detail: (id: string) => ["starting-points", "detail", id] as const,
  },
  drafts: {
    all: ["drafts"] as const,
    list: (filters?: Record<string, unknown>) => ["drafts", "list", filters ?? {}] as const,
    detail: (id: string) => ["drafts", "detail", id] as const,
  },
  knowledge: {
    all: ["knowledge"] as const,
    list: (filters?: Record<string, unknown>) => ["knowledge", "list", filters ?? {}] as const,
    docs: () => ["knowledge", "docs"] as const,
    doc: (slug: string) => ["knowledge", "doc", slug] as const,
    // The query text is part of the key, so typing narrows through the cache
    // rather than re-fetching what has already been asked.
    search: (q: string, k: number) => ["knowledge", "search", q, k] as const,
    insights: (filters?: Record<string, unknown>) =>
      ["knowledge", "insights", filters ?? {}] as const,
  },
  /**
   * Analyses live outside the `shots` prefix, like samples and for the same
   * reason: a shot ingested during a backfill invalidates `shots`, and an
   * analysis list under that prefix would be re-fetched for every row on screen
   * for a list that changes only when somebody presses a button.
   */
  /**
   * The chat. `tools` is outside `threads` because the tool list
   * changes with a redeploy and nothing a conversation does invalidates it,
   * while a thread moves on every turn.
   */
  chat: {
    all: ["chat"] as const,
    threads: () => ["chat", "threads"] as const,
    thread: (id: string) => ["chat", "thread", id] as const,
    run: (id: string) => ["chat", "run", id] as const,
    tools: () => ["chat", "tools"] as const,
  },
  analyses: {
    all: ["analyses"] as const,
    forShot: (shotId: string) => ["analyses", "shot", shotId] as const,
    forSet: (setId: string) => ["analyses", "set", setId] as const,
  },
  imports: { all: ["imports"] as const, list: () => ["imports", "list"] as const },
  device: {
    all: ["device"] as const,
    status: () => ["device", "status"] as const,
    writes: () => ["device", "writes"] as const,
    /**
     * Storage cleanup. Under the `device` prefix so one `sync.progress` or
     * `cleanup.progress` refreshes the whole section: the plan, the run history
     * and the free-space figures on it are three reads of the same subject.
     */
    cleanupPlan: () => ["device", "cleanup", "plan"] as const,
    cleanupRuns: () => ["device", "cleanup", "runs"] as const,
    /** Notes write-back: the judgements the machine's own notes cards do not have. */
    pendingNotes: () => ["device", "notes", "pending"] as const,
  },
  llm: {
    all: ["llm"] as const,
    status: () => ["llm", "status"] as const,
    models: (provider?: string) => ["llm", "models", provider ?? "configured"] as const,
    calls: () => ["llm", "calls"] as const,
    usage: (since?: string) => ["llm", "usage", since ?? "all"] as const,
  },
  prompts: {
    all: ["prompts"] as const,
    list: () => ["prompts", "list"] as const,
    detail: (name: string) => ["prompts", "detail", name] as const,
  },
} as const;
