import { PlayCircleOutlined, RedoOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Card, Col, Form, Input, InputNumber, Modal, Row, Select, Skeleton, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { useConfirmAction, useToastMutation } from "../hooks/useToastMutation";
import { DASH, fmtDateTime, parseApiDate, relTime } from "../lib/format";
import { INVALIDATE, qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import { RUN_STATUS, RUN_STATUS_LABEL } from "./Dashboard";
import type { Run, Source } from "../types/api";

/** Wall-clock duration of a run, from `parseApiDate` so a naive-UTC stamp is read correctly. */
function duration(r: Run, now: Date = new Date()): string {
  const start = parseApiDate(r.started_at);
  if (!start) return DASH;
  const end = parseApiDate(r.finished_at) ?? now;
  const secs = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  return m < 60 ? `${m}m ${secs % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Render a run's parameters, with long/credential-shaped values truncated. */
const SECRETISH = /key|token|secret|password|apikey/i;
function ParamTags({ params }: { params: Record<string, unknown> }) {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return <Typography.Text type="secondary">{DASH}</Typography.Text>;
  return (
    <Space size={4} wrap>
      {entries.map(([k, v]) => {
        const text = String(v);
        const shown = SECRETISH.test(k) ? "•••" : text.length > 40 ? `${text.slice(0, 39)}…` : text;
        return (
          <Tag key={k} bordered={false}>
            {k}: {shown}
          </Tag>
        );
      })}
    </Space>
  );
}

export function Discovery() {
  const { canEdit } = useMe();
  const [active, setActive] = useState<Source | null>(null);
  const [form] = Form.useForm();
  const confirmAction = useConfirmAction();

  const sources = useQuery({
    queryKey: qk.sources(),
    queryFn: () => api<Source[]>("/api/sources"),
    refetchInterval: REFRESH.background,
  });

  const runs = useQuery({
    queryKey: qk.runs(),
    queryFn: () => api<Run[]>("/api/runs"),
    // Fast while a run is queued/running, slow otherwise — never fully stale.
    refetchInterval: (q) =>
      (q.state.data || []).some((r) => r.status === "queued" || r.status === "running")
        ? REFRESH.operational
        : REFRESH.background,
  });

  const start = useToastMutation(
    (body: { source: string; params: Record<string, unknown> }) =>
      api<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
    {
      invalidates: [...INVALIDATE.runWrite],
      successMessage: "Run queued",
      onSuccess: () => setActive(null),
    },
  );

  // Cancel only works on queued runs (the API answers 409 otherwise).
  const cancelRun = useToastMutation((id: number) => api<Run>(`/api/runs/${id}/cancel`, { method: "POST" }), {
    invalidates: [...INVALIDATE.runWrite],
    successMessage: "Run cancelled",
  });
  // Retry re-queues a finished run with the same parameters.
  const retryRun = useToastMutation((id: number) => api<Run>(`/api/runs/${id}/retry`, { method: "POST" }), {
    invalidates: [...INVALIDATE.runWrite],
    successMessage: "Run queued again",
  });

  /**
   * Reset before seeding defaults. Previously the form was only `setFieldsValue`d, so
   * antd's `preserve` kept the previous source's fields: opening source B submitted
   * source A's params alongside B's, and the connector received foreign input.
   */
  const openForm = (s: Source) => {
    form.resetFields();
    const visible = s.params.filter((p) => !p.hidden);
    form.setFieldsValue(Object.fromEntries(visible.map((p) => [p.name, p.default ?? undefined])));
    setActive(s);
  };

  const submit = (values: Record<string, unknown>) => {
    if (!active) return;
    // Send only the params this source declares, so nothing leaks in from a previous form.
    const params = Object.fromEntries(
      active.params
        .filter((p) => !p.hidden)
        .map((p) => [p.name, values[p.name] ?? p.default ?? null]),
    );
    start.mutate({ source: active.key, params });
  };

  const requestCancel = (r: Run) =>
    confirmAction({
      title: "Cancel this run?",
      content: `${r.source_name} is queued and has not started yet. Cancelling cannot be undone.`,
      okText: "Cancel run",
      onOk: async () => {
        await cancelRun.mutateAsync(r.id);
      },
    });

  return (
    <>
      <PageHeader
        title="Discovery"
        subtitle="Lawful public sources. Every record keeps its source, URL and retrieval time."
      />

      {sources.isPending ? (
        <Row gutter={[16, 16]}>
          {[0, 1, 2, 3].map((i) => (
            <Col xs={24} md={12} xl={6} key={i}>
              <Card size="small">
                <Skeleton active paragraph={{ rows: 3 }} />
              </Card>
            </Col>
          ))}
        </Row>
      ) : sources.isError ? (
        <Card>
          <Typography.Text type="danger">{sources.error.message}</Typography.Text>
          <Button style={{ marginLeft: 12 }} onClick={() => void sources.refetch()}>
            Retry
          </Button>
        </Card>
      ) : (sources.data?.length ?? 0) === 0 ? (
        <Card>
          <EmptyState title="No discovery sources available" />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {sources.data!.map((s) => (
            <Col xs={24} md={12} xl={6} key={s.key}>
              <Card
                size="small"
                title={s.name}
                style={{ height: "100%" }}
                extra={
                  canEdit && (
                    <Button
                      size="small"
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      disabled={!s.available}
                      title={s.available ? undefined : "This source needs an API key that is not configured"}
                      onClick={() => openForm(s)}
                    >
                      New run
                    </Button>
                  )
                }
              >
                <Typography.Paragraph type="secondary" style={{ minHeight: 44, marginBottom: 8 }}>
                  {s.description}
                </Typography.Paragraph>
                <Space direction="vertical" size={2} style={{ fontSize: 12 }}>
                  <span>Licence: {s.licence}</span>
                  {!s.available && (
                    <Typography.Text type="warning" style={{ fontSize: 12 }}>
                      Needs an API key
                    </Typography.Text>
                  )}
                  <span>
                    Last run:{" "}
                    {s.last_run ? (
                      <Tooltip title={fmtDateTime(s.last_run.started_at ?? s.last_run.created_at)}>
                        <span>
                          <Badge
                            status={RUN_STATUS[s.last_run.status]}
                            text={`${relTime(s.last_run.started_at ?? s.last_run.created_at)} · ${s.last_run.found} found`}
                          />
                        </span>
                      </Tooltip>
                    ) : (
                      "never"
                    )}
                  </span>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Card size="small" title="Run history" style={{ marginTop: 16 }} styles={{ body: { padding: 0 } }}>
        <Table<Run>
          rowKey="id"
          size="small"
          loading={runs.isPending}
          dataSource={runs.data}
          locale={{ emptyText: "No runs yet — start one from a source above." }}
          pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
          scroll={{ x: 1200 }}
          columns={[
            {
              title: "Started",
              width: 150,
              // Was `r.created_at.slice(0,16)` — unguarded against a null timestamp.
              render: (_, r) => (
                <Tooltip title={fmtDateTime(r.created_at)}>
                  <span className="num">{fmtDateTime(r.created_at)}</span>
                </Tooltip>
              ),
              sorter: (a, b) => a.created_at.localeCompare(b.created_at),
              defaultSortOrder: "descend",
            },
            { title: "Source", dataIndex: "source_name", width: 180, ellipsis: true },
            { title: "Parameters", render: (_, r) => <ParamTags params={r.params ?? {}} /> },
            {
              title: "Status",
              width: 120,
              render: (_, r) => <Badge status={RUN_STATUS[r.status]} text={RUN_STATUS_LABEL[r.status]} />,
              filters: Object.entries(RUN_STATUS_LABEL).map(([value, text]) => ({ value, text })),
              onFilter: (value, r) => r.status === value,
            },
            {
              // Placed beside Status on purpose: at the end of this table it sits behind the
              // horizontal scroll and nobody would ever find it.
              title: "",
              key: "actions",
              width: 130,
              render: (_, r) =>
                canEdit ? (
                  <Space size={4}>
                    {r.status === "queued" && (
                      <Button
                        size="small"
                        aria-label={`Cancel run ${r.id}`}
                        loading={cancelRun.isPending && cancelRun.variables === r.id}
                        onClick={() => requestCancel(r)}
                      >
                        Cancel
                      </Button>
                    )}
                    {(r.status === "failed" || r.status === "cancelled" || r.status === "done") && (
                      <Button
                        size="small"
                        icon={<RedoOutlined />}
                        aria-label={`Retry run ${r.id}`}
                        loading={retryRun.isPending && retryRun.variables === r.id}
                        onClick={() => retryRun.mutate(r.id)}
                      >
                        Retry
                      </Button>
                    )}
                  </Space>
                ) : null,
            },
            { title: "Found", dataIndex: "found", width: 80, align: "right", sorter: (a, b) => a.found - b.found },
            { title: "New", dataIndex: "created", width: 72, align: "right", sorter: (a, b) => a.created - b.created },
            { title: "Merged", dataIndex: "updated", width: 80, align: "right", sorter: (a, b) => a.updated - b.updated },
            {
              title: "Errors",
              dataIndex: "errors",
              width: 80,
              align: "right",
              sorter: (a, b) => a.errors - b.errors,
              render: (_, r) => (r.errors > 0 ? <Typography.Text type="danger">{r.errors}</Typography.Text> : r.errors),
            },
            { title: "Duration", width: 100, render: (_, r) => duration(r) },
            {
              title: "Message",
              dataIndex: "message",
              ellipsis: true,
              render: (v: string | null) =>
                v ? (
                  <Tooltip title={v}>
                    <Typography.Text
                      type={v.toLowerCase().includes("error") || v.toLowerCase().includes("traceback") ? "danger" : undefined}
                    >
                      {v}
                    </Typography.Text>
                  </Tooltip>
                ) : (
                  <Typography.Text type="secondary">{DASH}</Typography.Text>
                ),
            },
          ]}
        />
      </Card>

      <Modal
        title={active ? `New run: ${active.name}` : ""}
        open={!!active}
        onCancel={() => setActive(null)}
        okText="Start run"
        confirmLoading={start.isPending}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        {active && (
          <Form form={form} layout="vertical" onFinish={submit}>
            {active.params.filter((p) => !p.hidden).length === 0 ? (
              <Typography.Text type="secondary">This source takes no parameters.</Typography.Text>
            ) : (
              active.params
                .filter((p) => !p.hidden)
                .map((p) => (
                  <Form.Item
                    key={p.name}
                    name={p.name}
                    label={p.label}
                    extra={p.help || undefined}
                    rules={[{ required: p.required, message: `${p.label} is required` }]}
                  >
                    {p.kind === "select" ? (
                      <Select
                        allowClear={!p.required}
                        options={(p.options || []).map((o) => ({ value: o, label: o }))}
                      />
                    ) : p.kind === "int" ? (
                      <InputNumber min={1} max={5000} style={{ width: 180 }} />
                    ) : (
                      <Input />
                    )}
                  </Form.Item>
                ))
            )}
          </Form>
        )}
      </Modal>
    </>
  );
}