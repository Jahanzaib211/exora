import { useQuery } from "@tanstack/react-query";
import { App as AntdApp, Badge, Button, Card, Col, Input, Popconfirm, Progress, Row, Select, Space, Tag, Tooltip, Typography } from "antd";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { useToastMutation } from "../hooks/useToastMutation";
import { DASH, clampPct, fmtDate, fmtDateTime } from "../lib/format";
import { INVALIDATE, qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { EngineNote, SourceNote, useMe } from "../shared";
import type { PlanResponse, PlanTask, PlanWeek } from "../types/api";

const TRACKS: { key: PlanTask["track"]; label: string }[] = [
  { key: "build", label: "Build" },
  { key: "outreach", label: "Outreach" },
  { key: "content", label: "Content" },
];

const STATUS_OPTS = [
  { value: "todo", label: "To do" },
  { value: "doing", label: "Doing" },
  { value: "done", label: "Done" },
  { value: "blocked", label: "Blocked" },
];

const STATUS_BADGE: Record<string, "default" | "processing" | "success" | "error"> = {
  todo: "default",
  doing: "processing",
  done: "success",
  blocked: "error",
};



/** Done/total for a week, computed from its tasks so it cannot drift from the rows shown. */
function weekProgress(w: PlanWeek) {
  const total = w.tasks.length;
  const done = w.tasks.filter((t) => t.status === "done").length;
  return { total, done };
}

export function Plan() {
  const { canEdit, isAdmin } = useMe();
  const { message } = AntdApp.useApp();

  const q = useQuery({
    queryKey: qk.plan(),
    queryFn: () => api<PlanResponse>("/api/plan"),
    refetchInterval: REFRESH.background,
  });

  const users = useQuery({
    queryKey: qk.users(),
    queryFn: () => api<{ id: number; name: string; active: boolean }[]>("/api/users"),
    enabled: canEdit,
    refetchInterval: REFRESH.background,
  });

  const update = useToastMutation(
    ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api(`/api/plan/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    { invalidates: [...INVALIDATE.planWrite] },
  );

  const summarise = (r?: { counts: Record<string, number> | null }) => {
    const n = r?.counts;
    return n && Object.keys(n).length ? Object.entries(n).map(([k, v]) => `${v} ${k}`).join(", ") : "no changes";
  };

  /**
   * Re-sync re-reads the markdown and rewrites the plan tables. Content is not lost,
   * but status/owner/notes are reset, so the Popconfirm explains that.
   *
   * Invalidation is scoped to the plan keys: a bare `invalidateQueries()` also
   * refetched the Docs markdown, which never changes on its own.
   */
  const resync = useToastMutation(
    () => api<{ counts: Record<string, number> | null }>("/api/plan/resync", { method: "POST" }),
    {
      invalidates: [...INVALIDATE.planWrite],
      successMessage: false,
      onSuccess: (r) => void message.success(`Plan synced: ${summarise(r)}`),
    },
  );

  const userOpts = (users.data ?? []).filter((u) => u.active).map((u) => ({ value: u.id, label: u.name }));

  return (
    <QueryBoundary q={q} label="the 30-day plan" skeletonRows={12}>
      {(data) => (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <SourceNote source={data.plan_source} />
          <EngineNote available={data.engine_available} />

          <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
            Source of truth: <code>{data.plan_source.path}</code>
            {data.plan_source.synced_at ? `, synced ${fmtDateTime(data.plan_source.synced_at)}` : ""}. Edit the
            markdown to change the plan; status, owners and notes are kept here.{" "}
            {isAdmin && (
              <Popconfirm
                title="Re-sync from markdown?"
                description="Week, KPI and routine rows are rewritten from the document. Status, owners and notes set here are reset."
                okText="Re-sync"
                onConfirm={() => void resync.mutateAsync()}
              >
                <Button size="small" loading={resync.isPending} data-testid="plan-resync">
                  Re-sync now
                </Button>
              </Popconfirm>
            )}
          </Typography.Paragraph>

          {data.weeks.length === 0 ? (
            <Card>
              <EmptyState
                title="No plan weeks parsed"
                hint="The plan is read from the week-by-week table in the source document. Check that the “Week-by-week” section still has its four-column table."
              />
            </Card>
          ) : (
            data.weeks.map((w) => {
              const { done, total } = weekProgress(w);
              const liTarget = typeof w.targets.linkedin_requests === "number" ? w.targets.linkedin_requests : null;
              const liActual = w.actual?.li_requests ?? null;
              const emailRange = Array.isArray(w.targets.emails_per_inbox_day)
                ? (w.targets.emails_per_inbox_day as [number, number])
                : null;

              return (
                <Card
                  key={w.id}
                  title={
                    <Space wrap>
                      <span>
                        {w.label}: {fmtDate(w.start)} – {fmtDate(w.end)}
                      </span>
                      {w.is_current && <Tag color="blue">This week</Tag>}
                    </Space>
                  }
                  extra={`${done}/${total} done`}
                >
                  <Space wrap size="large" style={{ marginBottom: 12 }}>
                    {liTarget !== null && (
                      <span style={{ minWidth: 220, display: "inline-block" }}>
                        LinkedIn requests {liActual === null ? DASH : liActual} / {liTarget}
                        <Progress
                          size="small"
                          // `x / 0` produced Infinity/NaN and a broken bar. The guard
                          // only checked `!== undefined`, which permits 0.
                          percent={liTarget > 0 && liActual !== null ? clampPct((liActual / liTarget) * 100) : 0}
                        />
                      </span>
                    )}
                    {emailRange && (
                      <span>
                        Email: {emailRange[0]}–{emailRange[1]} per inbox per day · sent{" "}
                        {w.actual?.emails_sent ?? DASH}
                      </span>
                    )}
                  </Space>

                  <Row gutter={[16, 16]}>
                    {TRACKS.map((track) => {
                      const tasks = w.tasks.filter((t) => t.track === track.key);
                      return (
                        <Col xs={24} md={8} key={track.key}>
                          <Typography.Title level={5} style={{ marginTop: 0 }}>
                            {track.label}
                          </Typography.Title>
                          {tasks.length === 0 ? (
                            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                              Nothing planned
                            </Typography.Text>
                          ) : (
                            <Space direction="vertical" size={8} style={{ width: "100%" }}>
                              {tasks.map((t) => (
                                <Card size="small" key={t.id}>
                                  <Space direction="vertical" size={6} style={{ width: "100%" }}>
                                    <Space align="start" wrap>
                                      <Badge status={STATUS_BADGE[t.status] ?? "default"} />
                                      <span style={{ flex: 1, minWidth: 0 }}>
                                        {t.detail ? (
                                          <Tooltip title={t.detail}>
                                            <span>{t.title}</span>
                                          </Tooltip>
                                        ) : (
                                          t.title
                                        )}
                                      </span>
                                    </Space>
                                    {canEdit && (
                                      <Space wrap size={6}>
                                        <Select
                                          size="small"
                                          style={{ width: 108 }}
                                          value={t.status}
                                          aria-label={`Status for ${t.title}`}
                                          options={STATUS_OPTS}
                                          loading={update.isPending && update.variables?.id === t.id}
                                          onChange={(status) => update.mutate({ id: t.id, body: { status } })}
                                        />
                                        <Select
                                          size="small"
                                          style={{ width: 140 }}
                                          allowClear
                                          placeholder="Owner"
                                          aria-label={`Owner for ${t.title}`}
                                          value={t.assignee_id ?? undefined}
                                          options={userOpts}
                                          loading={update.isPending && update.variables?.id === t.id}
                                          onChange={(assignee_id) =>
                                            update.mutate({ id: t.id, body: { assignee_id: assignee_id ?? null } })
                                          }
                                        />
                                      </Space>
                                    )}
                                    {canEdit ? (
                                      <Input.TextArea
                                        size="small"
                                        autoSize={{ minRows: 1, maxRows: 6 }}
                                        placeholder="Notes"
                                        aria-label={`Notes for ${t.title}`}
                                        // Keyed on the saved value so the field resets when
                                        // the server normalises or another user edits it.
                                        key={`${t.id}:${t.notes ?? ""}`}
                                        defaultValue={t.notes ?? ""}
                                        onBlur={(e) => {
                                          const next = e.target.value.trim();
                                          if (next !== (t.notes ?? "")) {
                                            update.mutate({ id: t.id, body: { notes: next } });
                                          }
                                        }}
                                      />
                                    ) : (
                                      t.notes && (
                                        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                                          {t.notes}
                                        </Typography.Text>
                                      )
                                    )}
                                  </Space>
                                </Card>
                              ))}
                            </Space>
                          )}
                        </Col>
                      );
                    })}
                  </Row>
                </Card>
              );
            })
          )}
        </Space>
      )}
    </QueryBoundary>
  );
}

