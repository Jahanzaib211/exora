import { useQuery } from "@tanstack/react-query";
import { Card, Input, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { useToastMutation } from "../hooks/useToastMutation";
import { DASH, fmtDate, pluralise } from "../lib/format";
import { INVALIDATE, qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import type { CampaignEvent } from "../types/api";

/** Status vocabulary from the plan document, shown as sentence case. */
const STATUSES = [
  { value: "watch", label: "Watch" },
  { value: "prepare", label: "Prepare" },
  { value: "outreach", label: "Outreach" },
  { value: "attending", label: "Attending" },
  { value: "done", label: "Done" },
];
const statusLabel = (v: string) => STATUSES.find((s) => s.value === v)?.label ?? v;

export function Events() {
  const { canEdit } = useMe();

  const q = useQuery({
    queryKey: qk.events(),
    queryFn: () => api<CampaignEvent[]>("/api/events"),
    refetchInterval: REFRESH.background,
  });

  const update = useToastMutation(
    ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    { invalidates: [...INVALIDATE.planWrite] },
  );

  return (
    <>
      <PageHeader
        title="Events to target"
        subtitle="Synced from the events table in 09_30_Day_Execution_Plan_and_KPIs.md."
      />
      <QueryBoundary q={q} label="events" skeletonRows={6}>
        {(data) => {
          const upcoming = data.filter((e) => !e.is_past).length;
          return (
            <Card
              size="small"
              title={`${pluralise(upcoming, "upcoming event")}`}
              styles={{ body: { padding: 0 } }}
            >
              {data.length === 0 ? (
                <div style={{ padding: 16 }}>
                  <EmptyState
                    title="No events found"
                    hint="Events are parsed from the plan document. If they should be here, check that the “Events to target” section still uses `- ` bullets and D–D Mon YYYY ranges."
                  />
                </div>
              ) : (
                <Table<CampaignEvent>
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={data}
                  scroll={{ x: 1000 }}
                  rowClassName={(e) => (e.is_past ? "ant-table-row-disabled" : "")}
                  columns={[
                    { title: "Event", dataIndex: "name", render: (n: string) => <b>{n}</b>, ellipsis: true },
                    {
                      title: "Where",
                      width: 160,
                      render: (_, e) =>
                        e.city || e.country ? (
                          [e.city, e.country].filter(Boolean).join(", ")
                        ) : (
                          <Typography.Text type="secondary">{DASH}</Typography.Text>
                        ),
                    },
                    {
                      title: "Dates",
                      width: 190,
                      render: (_, e) => (
                        <Tooltip title={e.end && e.end !== e.start ? `${fmtDate(e.start)} to ${fmtDate(e.end)}` : undefined}>
                          <span>
                            {fmtDate(e.start)}
                            {e.end && e.end !== e.start ? ` – ${fmtDate(e.end)}` : ""}
                          </span>
                        </Tooltip>
                      ),
                      sorter: (a, b) => a.start.localeCompare(b.start),
                    },
                    {
                      title: "When",
                      width: 120,
                      render: (_, e) =>
                        e.is_past ? (
                          <Tag>Past</Tag>
                        ) : e.days_until === 0 ? (
                          <Tag color="red">Today</Tag>
                        ) : e.days_until === 1 ? (
                          <Tag color="orange">Tomorrow</Tag>
                        ) : (
                          <Tag color={e.days_until <= 30 ? "orange" : "blue"}>in {e.days_until} d</Tag>
                        ),
                      sorter: (a, b) => a.days_until - b.days_until,
                    },
                    {
                      title: "Sequences",
                      render: (_, e) =>
                        e.segments.length ? (
                          <Space size={4} wrap>
                            {e.segments.map((s) => (
                              <Tag key={s}>{s}</Tag>
                            ))}
                          </Space>
                        ) : (
                          <Typography.Text type="secondary">{DASH}</Typography.Text>
                        ),
                    },
                    {
                      title: "Status",
                      width: 140,
                      render: (_, e) =>
                        canEdit ? (
                          <Select
                            size="small"
                            style={{ width: 125 }}
                            value={e.status}
                            aria-label={`Status for ${e.name}`}
                            options={STATUSES}
                            loading={update.isPending && update.variables?.id === e.id}
                            onChange={(status) => update.mutate({ id: e.id, body: { status } })}
                          />
                        ) : (
                          <Tag>{statusLabel(e.status)}</Tag>
                        ),
                    },
                    {
                      title: "Notes",
                      width: 220,
                      render: (_, e) =>
                        canEdit ? (
                          <Input
                            size="small"
                            // Controlled with a local draft so a server-side trim or
                            // rejection is visible, and keyed on the saved value so the
                            // field resets when another user edits the same row.
                            key={`${e.id}:${e.notes ?? ""}`}
                            defaultValue={e.notes ?? ""}
                            aria-label={`Notes for ${e.name}`}
                            placeholder="Add a note"
                            onBlur={(ev) => {
                              const next = ev.target.value.trim();
                              if (next !== (e.notes ?? "")) update.mutate({ id: e.id, body: { notes: next } });
                            }}
                          />
                        ) : e.notes ? (
                          e.notes
                        ) : (
                          <Typography.Text type="secondary">{DASH}</Typography.Text>
                        ),
                    },
                  ]}
                />
              )}
            </Card>
          );
        }}
      </QueryBoundary>
    </>
  );
}