import { Button, Result, Skeleton, Spin } from "antd";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

/**
 * One loading / error / empty contract for every data-backed view.
 *
 * The codebase previously had ~15 hand-written copies of
 * `if (isLoading || !data) return <Skeleton active />`, which meant an API failure
 * rendered an eternal shimmer with no message and no retry — indistinguishable
 * from "still loading". Seven pages had exactly that bug. This component makes the
 * three states explicit and impossible to get wrong.
 */

export function QueryBoundary<T>({
  q,
  children,
  loading,
  skeletonRows,
  empty,
  label,
}: {
  q: Pick<UseQueryResult<T, Error>, "data" | "isPending" | "isError" | "error" | "refetch" | "isFetching">;
  children: (data: T) => ReactNode;
  /** Custom loading UI. Defaults to a table-shaped skeleton. */
  loading?: ReactNode;
  /** Rows in the default skeleton. */
  skeletonRows?: number;
  /** When provided and the payload is empty, this renders instead of `children`. */
  empty?: ReactNode;
  /** Used in the error message. */
  label?: string;
}) {
  if (q.isPending) return loading ?? <TableSkeleton rows={skeletonRows ?? 6} />;

  if (q.isError) {
    return (
      <Result
        status="error"
        title={label ? `Could not load ${label}` : "Could not load this data"}
        subTitle={q.error?.message || "The server did not respond as expected."}
        extra={
          <Button type="primary" onClick={() => void q.refetch()} loading={q.isFetching}>
            Retry
          </Button>
        }
      />
    );
  }

  if (q.data === undefined || q.data === null) return <>{empty ?? null}</>;

  return <>{children(q.data)}</>;
}

/** A skeleton shaped like the app's tables so layout does not jump on load. */
export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-live="polite">
      <Skeleton active paragraph={{ rows }} title={false} />
    </div>
  );
}

/** Full-height centred spinner for route transitions. */
export function PageSpinner({ label = "Loading" }: { label?: string }) {
  return (
    <div
      aria-busy="true"
      aria-live="polite"
      role="status"
      style={{ display: "grid", placeItems: "center", minHeight: 240 }}
    >
      <Spin size="large" />
      <span style={visuallyHidden}>{label}</span>
    </div>
  );
}

/** Screen-reader-only text. */
export const visuallyHidden: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};