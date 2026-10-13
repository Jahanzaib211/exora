import { useQuery } from "@tanstack/react-query";
import { Card, Input, Select, Space, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { api, type Me } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { DASH, fmtUtc } from "../lib/format";
import { qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import type { AuditRow } from "../types/api";

function Detail({ detail }: { detail: Record<string, unknown> | null }) {
  if (!detail || !Object.keys(detail).length) return <Typography.Text type="secondary">{DASH}</Typography.Text>;
  return (
    <Space size={4} wrap>
      {Object.entries(detail).map(([k, v]) => (
        <Tag key={k} bordered={false}>
          {k}: {typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}
        </Tag>
      ))}
    </Space>
  );
}

export function Audit() {
  const { isAdmin, isPending: identityPending } = useMe();
  const [action, setAction] = useState<string | undefined>(undefined);
  const [q, setQ] = useState("");

  const rows = useQuery({
    queryKey: qk.audit(),
    queryFn: () => api<AuditRow[]>("/api/audit"),
    enabled: isAdmin,
    refetchInterval: REFRESH.background,
    // An audit trail is immutable: never serve a stale copy.
    staleTime: 0,
  });

  const users = useQuery({
    queryKey: qk.users(),
    queryFn: () => api<Me[]>("/api/users"),
    enabled: isAdmin,
    refetchInterval: REFRESH.background,
  });

  // Build the id→name lookup once. Previously `nameOf` ran a linear scan with an
  // array `.find` for every row on every keystroke, which is O(rows × users).
  const nameById = useMemo(() => {
    const m = new Map<number, string>();
    for (const u of users.data ?? []) m.set(u.id, u.name);
    return m;
  }, [users.data]);
  const nameOf = (id: number | null) => (id === null ? "system" : (nameById.get(id) ?? `#${id}`));

  const actions = useMemo(
    () => Array.from(new Set((rows.data ?? []).map((r) => r.action))).sort(),
    [rows.data],
  );

  const term = q.trim().toLowerCase();
  const filtered = (rows.data ?? []).filter(
    (r) =>
      (!action || r.action === action) &&
      (!term ||
        r.action.toLowerCase().includes(term) ||
        (r.target ?? "").toLowerCase().includes(term) ||
        nameOf(r.user_id).toLowerCase().includes(term)),
  );

  if (identityPending) return <Card loading />;
  if (!isAdmin) {
    return (
      <>
        <PageHeader title="Audit log" />
        <Card>
          <EmptyState
            title="The audit log is available to admins only"
            hint="It records every change to the database, who made it and what changed."
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Every change to the database, who made it and what changed — newest first"
      />
      <QueryBoundary q={rows} label="the audit log" skeletonRows={10}>
        {() => (
          <Card size="small" styles={{ body: { padding: 0 } }}>
            <div className="table-toolbar">
              <label className="filter-label" htmlFor="audit-search">
                Search
              </label>
              <Input.Search
                id="audit-search"
                placeholder="Action, target or person"
                allowClear
                style={{ width: 260 }}
                onSearch={setQ}
              />
              <label className="filter-label" htmlFor="audit-action">
                Action
              </label>
              <Select
                id="audit-action"
                allowClear
                placeholder="Any action"
                style={{ minWidth: 220 }}
                value={action}
                onChange={setAction}
                options={actions.map((a) => ({ value: a, label: a }))}
              />
              <Typography.Text type="secondary">
                {filtered.length} of {rows.data?.length ?? 0} entries
              </Typography.Text>
            </div>
            {filtered.length === 0 ? (
              <div style={{ padding: 16 }}>
                <EmptyState
                  title={
                    (rows.data?.length ?? 0) === 0
                      ? "No audit entries yet"
                      : "No entries match these filters"
                  }
                  hint={
                    (rows.data?.length ?? 0) === 0
                      ? "Entries appear as soon as someone changes data."
                      : "Try a different action or clear the search."
                  }
                  action={
                    (rows.data?.length ?? 0) > 0
                      ? {
                          label: "Clear filters",
                          onClick: () => {
                            setAction(undefined);
                            setQ("");
                          },
                        }
                      : undefined
                  }
                />
              </div>
            ) : (
              <Table<AuditRow & { _key: string }>
                /* The API exposes no `id`, so the key must be derived. The trailing
                   index guarantees uniqueness when two entries share a second, an
                   action and a target — a composite of the data alone collided. */
                rowKey="_key"
                dataSource={filtered.map((r, i) => ({
                  ...r,
                  _key: `${i}:${r.at}:${r.action}:${r.target ?? ""}:${r.user_id ?? "system"}`,
                }))}
                size="small"
                scroll={{ x: 900 }}
                pagination={{ pageSize: 25, showSizeChanger: true, pageSizeOptions: [25, 50, 100] }}
                columns={[
                  {
                    title: "When (UTC)",
                    width: 170,
                    // An audit trail wants an unambiguous instant, so this is UTC, not operator-local.
                    render: (_, r) => <span className="num">{fmtUtc(r.at)}</span>,
                    sorter: (a, b) => a.at.localeCompare(b.at),
                  },
                  {
                    title: "Who",
                    width: 160,
                    render: (_, r) => nameOf(r.user_id),
                    sorter: (a, b) => (a.user_id ?? 0) - (b.user_id ?? 0),
                  },
                  {
                    title: "Action",
                    width: 200,
                    render: (_, r) => <Tag bordered={false}>{r.action}</Tag>,
                    sorter: (a, b) => a.action.localeCompare(b.action),
                  },
                  { title: "Target", dataIndex: "target", ellipsis: true, render: (v) => v ?? DASH },
                  { title: "Detail", render: (_, r) => <Detail detail={r.detail} /> },
                ]}
              />
            )}
          </Card>
        )}
      </QueryBoundary>
    </>
  );
}