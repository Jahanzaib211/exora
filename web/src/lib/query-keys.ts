/**
 * The one place query keys are defined.
 *
 * Why: the codebase previously inlined `queryKey: ["runs"]` in four places while
 * two of them requested *different endpoints* (`/api/runs?limit=6` on the dashboard,
 * `/api/runs` on the discovery tab). With `staleTime: 0`, navigating between the two
 * rendered the dashboard's 6-row payload inside the discovery table — where
 * `Object.entries(r.params)` then threw and, with no error boundary, blanked the app.
 *
 * Rule going forward: a query key must uniquely identify (endpoint + parameters).
 * If two call sites differ in shape, they differ in key. Use the helpers below and
 * never inline an array literal.
 */

export const qk = {
  /* -------------------------------------------------------------- identity */
  users: () => ["users"] as const,

  /* ------------------------------------------------------------ dashboards */
  dashboard: () => ["dashboard"] as const,
  kpis: () => ["kpis"] as const,
  routine: () => ["routine"] as const,
  calls: () => ["calls"] as const,
  audit: () => ["audit"] as const,
  plan: () => ["plan"] as const,
  events: () => ["events"] as const,

  /** `/api/quality` — one shape, used by Overview, Data quality and Data engine. */
  quality: () => ["quality"] as const,

  /** `/api/engine` */
  engine: () => ["engine"] as const,

  /* -------------------------------------------------------------- companies */
  companies: (query: string) => ["companies", query] as const,
  companiesAll: () => ["companies"] as const,
  companyFacets: () => ["companies", "facets"] as const,
  company: (id: number | string) => ["company", String(id)] as const,
  companyNotes: (id: number | string) => ["company-notes", String(id)] as const,
  companyTasks: (id: number | string) => ["company-tasks", String(id)] as const,
  /** Lightweight name lookup for a single row tag — not the full detail payload. */
  companyName: (id: number | string) => ["company-name", String(id)] as const,
  taskCompanies: (q: string) => ["task-companies", q] as const,
  promoteCompanies: (query: string) => ["promote-companies", query] as const,

  /* ------------------------------------------------------------------ people */
  people: (query: string) => ["people", query] as const,
  person: (id: number | string) => ["person", String(id)] as const,

  /* --------------------------------------------------------------- discovery */
  sources: () => ["sources"] as const,
  /** Full run history for the Discovery tab. */
  runs: () => ["runs", "history"] as const,
  /** Recent-activity strip on the Overview tab — deliberately a *different* key. */
  recentRuns: (limit: number) => ["runs", "recent", limit] as const,
  automations: () => ["automations"] as const,
  exclusions: () => ["exclusions"] as const,
  signals: (query: string) => ["signals", query] as const,

  /* ---------------------------------------------------------------- outreach */
  outreachSummary: () => ["outreach-summary"] as const,
  outreachDrafts: () => ["outreach-drafts"] as const,
  outreachLinkedin: () => ["outreach-linkedin"] as const,
  outreachLeads: (query: string) => ["outreach-leads", query] as const,

  /* ------------------------------------------------------------------- tasks */
  tasks: (query: string) => ["tasks", query] as const,
  tasksSummary: () => ["tasks-summary"] as const,

  /* -------------------------------------------------------------------- docs */
  docs: () => ["docs"] as const,
  doc: (path: string | null) => ["doc", path ?? ""] as const,

  /* ------------------------------------------------------------------ search */
  searchCompanies: (q: string) => ["search-c", q] as const,
  searchPeople: (q: string) => ["search-p", q] as const,
} as const;

/**
 * Every key whose data a given mutation invalidates.
 *
 * Invalidation used to be hand-written per mutation and was frequently incomplete
 * (a task write left the dashboard stale, a company write left the quality panel
 * stale). Naming the fan-out once keeps the cache coherent.
 *
 * Note: TanStack Query invalidation matches on key *prefix*, so `["companies"]`
 * already covers both `qk.companies(q)` and `qk.companyFacets()`.
 */
export const INVALIDATE = {
  companyWrite: [["companies"], ["dashboard"], ["quality"], ["company"]],
  companyDelete: [["companies"], ["dashboard"], ["quality"], ["company"], ["outreach-leads"]],
  taskWrite: [["tasks"], ["tasks-summary"], ["dashboard"], ["routine"], ["company-tasks"]],
  callWrite: [["calls"], ["kpis"], ["dashboard"], ["routine"]],
  kpiWrite: [["kpis"], ["dashboard"], ["routine"]],
  runWrite: [["runs"], ["engine"], ["dashboard"], ["sources"]],
  automationWrite: [["automations"], ["runs"], ["engine"], ["sources"]],
  outreachWrite: [["outreach-summary"], ["outreach-drafts"], ["outreach-linkedin"], ["outreach-leads"], ["dashboard"], ["companies"]],
  planWrite: [["plan"], ["dashboard"], ["kpis"], ["routine"]],
  userWrite: [["users"]],
  qualityRecompute: [["quality"], ["dashboard"], ["engine"]],
} as const;

/** Apply an `INVALIDATE` bundle to a QueryClient. */
export function invalidate(client: { invalidateQueries: (f: { queryKey: readonly unknown[] }) => void }, bundle: readonly (readonly unknown[])[]) {
  for (const queryKey of bundle) client.invalidateQueries({ queryKey });
}