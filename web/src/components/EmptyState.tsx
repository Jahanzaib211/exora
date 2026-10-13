import { Button, Empty, Space, Typography } from "antd";
import type { ReactNode } from "react";

/**
 * Explicit empty state for a data view.
 *
 * "No data" from antd's default `Empty` gives the user nothing to act on. An
 * enterprise console should say *why* the view is empty and what to do next —
 * "no results for this filter" is a different situation from "nothing here yet".
 */

export function EmptyState({
  title,
  hint,
  action,
  icon,
  compact,
}: {
  title: string;
  hint?: ReactNode;
  action?: { label: string; onClick: () => void; icon?: ReactNode };
  icon?: ReactNode;
  /** Render inline (inside a table cell) rather than as a full panel. */
  compact?: boolean;
}) {
  if (compact) {
    return (
      <div style={{ padding: "12px 0" }}>
        <Empty image={icon ?? Empty.PRESENTED_IMAGE_SIMPLE} description={null}>
          <Space direction="vertical" size={2} align="center">
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {title}
            </Typography.Text>
            {hint && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {hint}
              </Typography.Text>
            )}
            {action && (
              <Button size="small" onClick={action.onClick} icon={action.icon} style={{ marginTop: 6 }}>
                {action.label}
              </Button>
            )}
          </Space>
        </Empty>
      </div>
    );
  }

  return (
    <div
      style={{
        padding: "32px 16px",
        textAlign: "center",
        border: "1px dashed var(--exora-border, #e6e9ef)",
        borderRadius: 8,
        background: "var(--exora-surface-sunken, transparent)",
      }}
    >
      <Empty image={icon ?? Empty.PRESENTED_IMAGE_SIMPLE} description={null}>
        <Space direction="vertical" size={4} align="center">
          <Typography.Text strong>{title}</Typography.Text>
          {hint && (
            <Typography.Text type="secondary" style={{ fontSize: 13, maxWidth: 460, display: "block" }}>
              {hint}
            </Typography.Text>
          )}
          {action && (
            <Button type="primary" onClick={action.onClick} icon={action.icon} style={{ marginTop: 10 }}>
              {action.label}
            </Button>
          )}
        </Space>
      </Empty>
    </div>
  );
}

/**
 * Distinguish "nothing matches your filter" from "there is nothing here yet".
 * Getting this wrong is what makes an empty screen feel broken.
 */
export function emptyForFilteredList({
  isFiltered,
  filterLabel,
  onClear,
  emptyTitle,
  emptyHint,
}: {
  isFiltered: boolean;
  filterLabel: string;
  onClear: () => void;
  emptyTitle: string;
  emptyHint?: ReactNode;
}) {
  return isFiltered ? (
    <EmptyState
      title={`No results for ${filterLabel}`}
      hint="Try widening or clearing the filters."
      action={{ label: "Clear filters", onClick: onClear }}
    />
  ) : (
    <EmptyState title={emptyTitle} hint={emptyHint} />
  );
}