import { useQuery } from "@tanstack/react-query";
import { Button, Card, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { useToastMutation } from "../hooks/useToastMutation";
import { DASH, fmtDate, fmtDateTime, fmtMoney } from "../lib/format";
import { INVALIDATE, qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import type { Call, CallOutcome } from "../types/api";

/**
 * Outcome and source values are the API's own vocabulary. They are shown as
 * sentence case rather than raw lowercase (`pending`, `no`, `later` read as noise
 * in a table of real work), but the value sent back is unchanged.
 */
const OUTCOMES: { value: CallOutcome; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "pilot", label: "Pilot" },
  { value: "no", label: "No" },
  { value: "later", label: "Later" },
];
const outcomeLabel = (v: string) => OUTCOMES.find((o) => o.value === v)?.label ?? v;

const SOURCES = ["linkedin", "email", "event", "referral", "other"].map((v) => ({
  value: v,
  label: v.charAt(0).toUpperCase() + v.slice(1),
}));

/** P01..P12, matching the price sheet (10_Price_Sheet.md). */
const PACKAGES = Array.from({ length: 12 }, (_, i) => `P${String(i + 1).padStart(2, "0")}`).map((v) => ({
  value: v,
  label: v,
}));

const OUTCOME_COLOR: Record<string, string> = {
  pending: "default",
  pilot: "green",
  no: "red",
  later: "gold",
};

export function Calls() {
  const { canEdit } = useMe();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const q = useQuery({
    queryKey: qk.calls(),
    queryFn: () => api<Call[]>("/api/calls"),
    // A call log is append-only between edits; 10s polling was needlessly hot.
    refetchInterval: REFRESH.background,
  });

  const create = useToastMutation(
    (body: Record<string, unknown>) =>
      api("/api/calls", { method: "POST", body: JSON.stringify(body) }),
    {
      invalidates: INVALIDATE.callWrite,
      successMessage: "Call logged",
      onSuccess: () => {
        setOpen(false);
        form.resetFields();
      },
    },
  );

  const update = useToastMutation(
    ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api(`/api/calls/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    { invalidates: INVALIDATE.callWrite },
  );

  const openModal = () => {
    form.resetFields();
    form.setFieldsValue({ source: "linkedin", outcome: "pending", held: false });
    setOpen(true);
  };

  return (
    <>
      <PageHeader
        title="Discovery calls and pilots"
        subtitle="Every logged call feeds the “replies → calls” and “calls → pilot” KPIs."
        extra={
          canEdit && (
            <Button type="primary" onClick={openModal}>
              Log a call
            </Button>
          )
        }
      />
      <QueryBoundary q={q} label="calls" skeletonRows={8}>
        {(data) => (
          <Card size="small" styles={{ body: { padding: 0 } }}>
            {data.length === 0 ? (
              <div style={{ padding: 16 }}>
                <EmptyState
                  title="No calls logged yet"
                  hint="Log each discovery call to keep the funnel KPIs accurate."
                  action={canEdit ? { label: "Log a call", onClick: openModal } : undefined}
                />
              </div>
            ) : (
              <Table<Call>
                rowKey="id"
                size="small"
                dataSource={data}
                scroll={{ x: 1100 }}
                pagination={{ pageSize: 25, showSizeChanger: true, pageSizeOptions: [25, 50, 100] }}
                columns={[
                  {
                    title: "Company",
                    dataIndex: "company",
                    sorter: (a, b) => a.company.localeCompare(b.company),
                    render: (name: string) => <b>{name}</b>,
                  },
                  { title: "Contact", dataIndex: "contact", ellipsis: true, render: (v: string | null) => v ?? DASH },
                  { title: "Lead", dataIndex: "lead_id", width: 100, render: (v: string | null) => v ?? DASH },
                  {
                    title: "Source",
                    dataIndex: "source",
                    width: 110,
                    render: (v: string) => SOURCES.find((s) => s.value === v)?.label ?? v,
                  },
                  {
                    title: "Scheduled",
                    dataIndex: "scheduled_for",
                    width: 120,
                    render: (v: string | null) => (v ? fmtDate(v) : DASH),
                  },
                  {
                    title: "Held",
                    width: 80,
                    render: (_, c) => (
                      <Tooltip title={c.held ? "Mark as not held" : "Mark as held"}>
                        <Switch
                          size="small"
                          checked={c.held}
                          disabled={!canEdit}
                          aria-label={`Held for ${c.company}`}
                          loading={update.isPending && update.variables?.id === c.id}
                          onChange={(held) => update.mutate({ id: c.id, body: { held } })}
                        />
                      </Tooltip>
                    ),
                  },
                  {
                    title: "Outcome",
                    width: 130,
                    render: (_, c) =>
                      canEdit ? (
                        <Select
                          size="small"
                          style={{ width: 110 }}
                          value={c.outcome}
                          aria-label={`Outcome for ${c.company}`}
                          options={OUTCOMES}
                          loading={update.isPending && update.variables?.id === c.id}
                          onChange={(outcome) => update.mutate({ id: c.id, body: { outcome } })}
                        />
                      ) : (
                        <Tag color={OUTCOME_COLOR[c.outcome]}>{outcomeLabel(c.outcome)}</Tag>
                      ),
                  },
                  { title: "Package", dataIndex: "package", width: 90, render: (v: string | null) => v ?? DASH },
                  {
                    title: "Value",
                    width: 110,
                    align: "right",
                    // `value_usd ? … : ""` used to render a legitimate 0 as blank.
                    render: (_, c) =>
                      c.value_usd === null ? (
                        <Typography.Text type="secondary">{DASH}</Typography.Text>
                      ) : (
                        <span className="num">{fmtMoney(c.value_usd)}</span>
                      ),
                    sorter: (a, b) => (a.value_usd ?? 0) - (b.value_usd ?? 0),
                  },
                  {
                    title: "Notes",
                    dataIndex: "notes",
                    ellipsis: true,
                    render: (v: string | null) => v ?? DASH,
                  },
                  {
                    title: "Logged",
                    width: 140,
                    render: (_, c) => <Tooltip title={fmtDateTime(c.created_at)}>{fmtDate(c.created_at)}</Tooltip>,
                  },
                ]}
              />
            )}
          </Card>
        )}
      </QueryBoundary>

      <Modal
        title="Log a discovery call"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        okText="Log call"
        confirmLoading={create.isPending}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => create.mutate(v)}>
          <Form.Item name="company" label="Company" rules={[{ required: true, message: "Company is required" }]}>
            <Input autoFocus />
          </Form.Item>
          <Space wrap align="start">
            <Form.Item name="contact" label="Contact">
              <Input style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="lead_id" label="Engine lead id">
              <Input placeholder="L0038" style={{ width: 140 }} />
            </Form.Item>
          </Space>
          <Space wrap align="start">
            <Form.Item name="source" label="Source">
              <Select options={SOURCES} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="outcome" label="Outcome">
              <Select options={OUTCOMES} style={{ width: 130 }} />
            </Form.Item>
            <Form.Item name="held" label="Held" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Space wrap align="start">
            <Form.Item name="package" label="Package" tooltip="From 10_Price_Sheet.md">
              <Select options={PACKAGES} allowClear placeholder="None" style={{ width: 130 }} />
            </Form.Item>
            <Form.Item name="value_usd" label="Value (USD)">
              <InputNumber min={0} step={250} style={{ width: 160 }} />
            </Form.Item>
          </Space>
          <Form.Item name="notes" label="Notes">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}