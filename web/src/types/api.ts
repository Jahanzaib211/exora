/**
 * Shared API response types, verified against the live endpoints.
 *
 * The codebase previously declared `Run` four times with different field subsets
 * (Dashboard, Discovery, Engine, Automations) and `Quality` twice with genuinely
 * different shapes. Because they all shared one query key, TypeScript's view of the
 * cache entry depended on which page happened to mount first. One canonical
 * definition per resource removes the ambiguity.
 *
 * Rule: optional-in-the-DB and nullable fields are `| null`. Do not declare a
 * timestamp as plain `string` — several were, and the UI then called `.slice()` on
 * a value that can be null, which blanked the page.
 */

/* ------------------------------------------------------------------- runs */

export const RUN_STATUSES = ["queued", "running", "done", "failed", "cancelled"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/** `/api/runs` — the union of every field any run row can carry. */
export type Run = {
  id: number;
  source: string;
  source_name: string;
  params: Record<string, unknown>;
  status: RunStatus;
  found: number;
  created: number;
  updated: number;
  errors: number;
  message: string | null;
  automation_id: number | null;
  automation: string | null;
  /** Null until a queued run is claimed by a worker. */
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

/* --------------------------------------------------------------- quality */

export type QualityCheck = { check: string; label: string; weight: number; passed: number; failed: number };
export type QualityBand = { band: string; count: number };
export type QualityTier = { tier: string; rule: string; count: number };

/** `/api/quality` — one shape, consumed by Overview, Data quality and Data engine. */
export type Quality = {
  checks: QualityCheck[];
  bands: QualityBand[];
  fit_bands: QualityBand[];
  tiers: QualityTier[];
  scored: number;
  unscored: number;
  stale: number;
};

/* ---------------------------------------------------------------- sources */

export type ParamKind = "text" | "select" | "int";

export type SourceParam = {
  name: string;
  label: string;
  kind: ParamKind;
  required: boolean;
  options: string[] | null;
  default: unknown;
  help: string;
  hidden: boolean;
};

/** `/api/sources` — the in-code connector registry rendered at request time. */
export type Source = {
  key: string;
  name: string;
  description: string;
  licence: string;
  /** False when a credential is required but not configured. */
  available: boolean;
  needs_key: boolean;
  params: SourceParam[];
  last_run: Run | null;
};

/* ------------------------------------------------------------ automations */

export type Automation = {
  id: number;
  name: string;
  source: string;
  source_name: string;
  params: Record<string, unknown>;
  rotate_field: string | null;
  rotate_values: string[];
  rotate_index: number;
  next_value: string | null;
  cadence_minutes: number;
  enabled: boolean;
  builtin: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run: Run | null;
};

/* ------------------------------------------------------------- companies */

export type CompanyRow = {
  id: number;
  name: string;
  domain: string | null;
  website: string | null;
  country: string | null;
  city: string | null;
  segment: string | null;
  tier: string | null;
  status: string | null;
  size_band: string | null;
  fit_score: number | null;
  quality_score: number | null;
  general_email: string | null;
  email_provider: string | null;
  phone: string | null;
  linkedin_url: string | null;
  owner_id: number | null;
  people_count: number;
  signal_count: number;
  last_crawled_at: string | null;
  last_verified_at: string | null;
  created_at: string;
};

/** `/api/companies/facets` */
export type FacetValue = { value: string; count: number };
export type Facets = {
  total: number;
  people: number;
  segment: FacetValue[];
  country: FacetValue[];
  status: FacetValue[];
  tier: FacetValue[];
};

export type PersonRow = {
  id: number;
  company_id: number | null;
  company: string | null;
  company_domain: string | null;
  country: string | null;
  segment: string | null;
  name: string;
  title: string | null;
  seniority: string | null;
  email: string | null;
  email_status: string | null;
  phone: string | null;
  linkedin_url: string | null;
  updated_at: string | null;
};

/** Standard list envelope used by every paginated endpoint. */
export type Paged<T> = { items: T[]; total: number };

/* ----------------------------------------------------------------- tasks */

export const TASK_STATUSES = ["todo", "doing", "blocked", "done"] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

/** `/api/tasks` */
export type Task = {
  id: number;
  title: string;
  kind: string;
  status: TaskStatus;
  priority: number | null;
  due_on: string | null;
  assignee_id: number | null;
  company_id: number | null;
  person_id: number | null;
  created_by: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string | null;
};

/** `/api/tasks/summary` */
export type TaskSummary = {
  mine: number;
  mine_overdue: number;
  mine_today: number;
  unassigned: number;
  team: { id: number; name: string; open: number; overdue: number }[];
};

export type CompanyLite = { id: number; name: string };

/* ----------------------------------------------------------------- calls */

/** `/api/calls` — discovery calls and their outcome. */
export type CallOutcome = "pending" | "pilot" | "no" | "later";

export type Call = {
  id: number;
  /** Engine lead id (a string), not the prospector person id. */
  lead_id: string | null;
  company: string;
  contact: string | null;
  source: string;
  scheduled_for: string | null;
  held: boolean;
  outcome: CallOutcome;
  /** Price-sheet package code, P01..P12. */
  package: string | null;
  value_usd: number | null;
  notes: string | null;
  owner_id: number | null;
  created_at: string;
};

/* -------------------------------------------------------------- outreach */

/** `/api/outreach/summary` */
export type OutreachSummary = {
  dry_run: boolean;
  paused: boolean;
  leads_by_status: Record<string, number>;
  pending_drafts: number;
  linkedin_due: number;
  emails_sent: number;
  postal_address_ok: boolean;
  mailboxes: number;
};

/** `/api/outreach/drafts` */
export type DraftStatus = "pending" | "approved" | "rejected";

export type Draft = {
  id: number;
  lead_id: number | null;
  company: string | null;
  to: string | null;
  subject: string | null;
  body: string | null;
  opener: string | null;
  opener_source: string | null;
  channel: string | null;
  kind: string | null;
  sequence: string | null;
  step: number;
  first_touch: boolean;
  region: string | null;
  message_id: string | null;
  status: DraftStatus;
  /** Compliance warnings raised by the engine — must be shown, not swallowed. */
  warnings: string[];
  created_at: string;
};

/** `/api/outreach/linkedin` */
export type LinkedinTask = {
  id: number;
  lead_id: number | null;
  company: string | null;
  contact: string | null;
  status: string;
  created_at: string;
};

/** `/api/outreach/leads` */
export type Lead = {
  lead_id: string;
  company: string | null;
  contact: string | null;
  email: string | null;
  status: string | null;
  sequence: string | null;
  step_index: number;
  region: string | null;
  next_due_at: string | null;
  last_touch_at: string | null;
  stop_reason: string | null;
};

/* ---------------------------------------------------------------- signals */

/** `/api/signals` */
export type Signal = {
  id: number;
  company_id: number | null;
  company: string | null;
  kind: string;
  title: string;
  url: string | null;
  source: string | null;
  segment: string | null;
  tier: string | null;
  country: string | null;
  fit_score: number | null;
  created_at: string;
};

/* ------------------------------------------------------------ exclusions */

/** `/api/exclusions` */
export type Exclusion = { id: number; kind: string; value: string; reason: string | null; created_at: string };

/* ------------------------------------------------------------------ docs */

/** `/api/docs` */
export type DocSummary = { path: string; title: string; modified: string | null; bytes: number };
export type DocGroup = { group: string; docs: DocSummary[] };

/** `/api/docs/content` */
export type DocHeading = { level: number; text: string };

/** `/api/docs/content` — note the key is `markdown`, not `content`. */
export type Doc = {
  path: string;
  title: string;
  modified: string | null;
  headings: DocHeading[];
  markdown: string;
};

/* ----------------------------------------------------------------- audit */

/**
 * `/api/audit` — note there is no `id` in the response, so table rows must use a
 * composite key. (The column exists; the serialiser does not expose it.)
 */
export type AuditRow = {
  at: string;
  user_id: number | null;
  action: string;
  target: string | null;
  detail: Record<string, unknown> | null;
};

/* --------------------------------------------------------------- engine */

/** `/api/engine` */
export type EngineSnapshot = {
  status: string;
  heartbeat_age_s: number | null;
  worker_threads: number;
  automations_enabled: number;
  started_at: string | null;
  cache: Record<string, unknown> | null;
  queue: { queued: number; running: Run[] };
  last_24h: {
    runs: number;
    records_found: number;
    new_companies: number;
    new_people: number;
    failed_runs: number;
  };
  series: SeriesPoint[];
};

export type SeriesPoint = {
  day: string;
  companies: number;
  people: number;
  with_website: number;
  crawled: number;
  with_email: number;
};

/* ------------------------------------------------------ plan, kpis, routine */

export type PlanSource = { path: string; synced_at: string | null; error: string | null; outcome: string | null };

export type Kpi = {
  key: string;
  name: string;
  unit: "pct" | "hours" | "count";
  direction: "higher" | "lower";
  target: number;
  threshold: number | null;
  min_sample: number;
  action: string;
  /** Where the number is measured. A union so label maps stay exhaustive at compile time. */
  source: "engine" | "manual" | "calls";
  value: number | null;
  sample: number;
  status: "green" | "amber" | "red" | "gray";
  note: string;
};

/** `/api/kpis` */
export type KpisResponse = {
  engine_available: boolean;
  engine_error: string | null;
  kpis: Kpi[];
  funnel: FunnelRow[];
  entries: { id: number; key: string; value: number; note: string | null; on: string }[];
  plan_source: PlanSource;
};

export type FunnelRow = {
  key: string;
  name: string;
  assumption: string;
  source: string;
  is_assumption: boolean;
  plan_low: number;
  plan_high: number;
  actual: number | null;
  progress_pct: number | null;
};

/** `/api/plan` */
export type PlanTask = {
  id: number;
  week_id: number;
  track: string | null;
  title: string;
  detail: string | null;
  status: string;
  assignee_id: number | null;
  notes: string | null;
};

export type PlanWeek = {
  id: number;
  label: string;
  start: string;
  end: string;
  focus: string;
  targets: { linkedin_requests?: number; emails_per_inbox_day?: [number, number] } & Record<string, unknown>;
  actual: { li_requests?: number; emails_sent?: number } | null;
  /** The week containing today. */
  is_current: boolean;
  tasks: PlanTask[];
};

export type PlanResponse = {
  today: string;
  engine_available: boolean;
  plan_source: PlanSource;
  weeks: PlanWeek[];
};

/** `/api/events` */
export type CampaignEvent = {
  id: number;
  name: string;
  city: string | null;
  country: string | null;
  start: string;
  end: string | null;
  segments: string[];
  status: string;
  notes: string | null;
  days_until: number;
  is_past: boolean;
};

/** `/api/routine` */
export type RoutineItem = {
  id: number;
  minutes: number;
  title: string;
  detail: string;
  done: boolean;
  team_done: number[];
  live_count: number;
  live_counter: string | null;
};

export type RoutineResponse = { day: string; items: RoutineItem[] };

/* ------------------------------------------------------------- dashboard */

/** `/api/dashboard` */
export type Dashboard = {
  today: string;
  campaign: { start: string; day: number; starts_in_days: number };
  plan_source: PlanSource;
  engine: {
    available: boolean;
    dry_run: boolean | null;
    pending_approvals: number;
    open_replies: number;
    linkedin_due: number;
  };
  week: { label: string; start: string; end: string; done: number; total: number } | null;
  kpis: Kpi[];
  calls: { booked: number; pilots: number; pilot_value: number };
  next_event: { name: string; city: string; days_until: number } | null;
  warnings: string[];
};