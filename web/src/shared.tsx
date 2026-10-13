import { useGetIdentity } from "@refinedev/core";
import { Alert, Skeleton, Tag } from "antd";
import type { ReactNode } from "react";
import type { Me } from "./api";
import type { Kpi, PlanSource } from "./types/api";

/* Re-exported so pages have one import site for shared UI + shared types. */
export type {
  AuditRow,
  Automation,
  Call,
  CampaignEvent,
  CompanyLite,
  CompanyRow,
  Dashboard,
  Doc,
  DocGroup,
  Draft,
  EngineSnapshot,
  Exclusion,
  Facets,
  FunnelRow,
  Kpi,
  KpisResponse,
  Lead,
  LinkedinTask,
  OutreachSummary,
  ParamKind,
  PersonRow,
  PlanResponse,
  PlanSource,
  PlanTask,
  PlanWeek,
  Quality,
  RoutineItem,
  RoutineResponse,
  Run,
  RunStatus,
  Signal,
  Source,
  SourceParam,
  Task,
  TaskStatus,
  TaskSummary,
} from "./types/api";

/** Tag colours for the KPI health states. */
export const STATUS_COLOR: Record<Kpi["status"], string> = {
  green: "success",
  amber: "warning",
  red: "error",
  gray: "default",
};

export const STATUS_TEXT: Record<Kpi["status"], string> = {
  green: "On target",
  amber: "Below target",
  red: "Past threshold",
  gray: "Not enough data",
};

/** Format a KPI value for display. Null-safe; never emits `NaN`. */
export function fmt(v: number | null | undefined, unit: Kpi["unit"]): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (unit === "pct") return `${v}%`;
  if (unit === "hours") return `${v} h`;
  return String(v);
}

/** The target rule as a human reads it: `≥25%`, `<2 h`. */
export function rule(k: Kpi): string {
  const sym = k.direction === "higher" ? "≥" : "<";
  return `${sym}${fmt(k.target, k.unit)}`;
}

export function KpiTag({ status }: { status: Kpi["status"] }) {
  return <Tag color={STATUS_COLOR[status]}>{STATUS_TEXT[status]}</Tag>;
}

/**
 * Current identity plus the permission flags every write is gated on.
 *
 * `isPending` matters: previously the flags were derived straight from `data`, so
 * while `/api/auth/me` was still in flight every page rendered as though the user
 * were a viewer. Admins saw "The audit log is available to admins only" and SDRs
 * saw "Viewers cannot see the team list" flash on every single page load, and every
 * edit button popped in a moment later. Callers should render a skeleton while
 * `isPending` rather than guessing a role.
 */
/**
 * Current identity plus the permission flags every write is gated on.
 *
 * `isPending` matters: previously the flags were derived straight from `data`, so
 * while `/api/auth/me` was still in flight every page rendered as though the user
 * were a viewer. Admins saw "The audit log is available to admins only" and SDRs
 * saw "Viewers cannot see the team list" flash on every page load. Callers should
 * render a skeleton while `isPending` rather than guessing a role.
 *
 * The identity query must also not poll: it was firing every 15s via the global
 * `refetchInterval`, which produced a 401 every 15 seconds for anyone sitting on
 * the login screen and put a wall of red in the console. Sessions are re-checked on
 * every navigation instead, and any real expiry is caught by the central 401
 * handler in api.ts.
 */
export function useMe() {
  const { data, isPending, isError, refetch } = useGetIdentity<Me>();
  const role = data?.role;
  return {
    me: data,
    isPending,
    isError,
    refetchIdentity: refetch,
    role,
    canEdit: role === "admin" || role === "sdr",
    isAdmin: role === "admin",
    isViewer: role === "viewer",
  };
}

/** Permission gate that waits for identity instead of assuming the worst case. */
export function CanEdit({ children, fallback = null }: { children: ReactNode; fallback?: ReactNode }) {
  const { canEdit, isPending } = useMe();
  if (isPending) return <Skeleton active paragraph={{ rows: 1 }} title={false} />;
  return canEdit ? <>{children}</> : <>{fallback}</>;
}

/** Admin-only gate, same contract as `CanEdit`. */
export function IsAdmin({ children, fallback = null }: { children: ReactNode; fallback?: ReactNode }) {
  const { isAdmin, isPending } = useMe();
  if (isPending) return <Skeleton active paragraph={{ rows: 1 }} title={false} />;
  return isAdmin ? <>{children}</> : <>{fallback}</>;
}

/**
 * Banner shown when the campaign markdown could not be re-read.
 * The app keeps serving the last good parse, so this explains a stale screen.
 */
export function SourceNote({ source }: { source?: PlanSource | null }) {
  if (!source?.error) return null;
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 16 }}
      message={`${source.path} could not be read. Showing the last saved version.`}
      description={source.error}
    />
  );
}

/**
 * Banner shown when the outreach engine is unreachable.
 *
 * The `error` prop used to be destructured and then never rendered, so the engine's
 * actual failure string was discarded and the operator saw only a generic notice.
 * The reason is the single most useful thing on this screen, so it is now shown.
 */
export function EngineNote({ available, error }: { available?: boolean; error?: string | null }) {
  if (available !== false) return null;
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 16 }}
      message="Outreach engine offline — outreach figures are unavailable until it reconnects."
      description={
        error ? (
          <>
            <div style={{ marginBottom: 6 }}>Reported reason:</div>
            <code style={{ wordBreak: "break-word", whiteSpace: "pre-wrap" }}>{error}</code>
          </>
        ) : (
          "The app could not reach the engine database. Check that the engine is running and that PROSPECTOR_ENGINE_CONFIG points at its config."
        )
      }
    />
  );
}