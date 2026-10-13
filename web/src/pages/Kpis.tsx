import { useQuery } from "@tanstack/react-query";
import { Button, Card, DatePicker, Form, InputNumber, Progress, Space, Table, Tag, Tooltip, Typography } from "antd";
import dayjs from "dayjs";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { useToastMutation } from "../hooks/useToastMutation";
import { DASH, clampPct, fmtDate, fmtNum } from "../lib/format";
import { INVALIDATE, qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { EngineNote, KpiTag, SourceNote, fmt, rule, useMe } from "../shared";
import type { FunnelRow, Kpi, KpisResponse } from "../types/api";

/** Where each KPI's number comes from. Exhaustive over `Kpi["source"]`. */
const SOURCE_LABEL: Record<Kpi["source"], string> = {
  engine: "Measured by the engine",
  manual: "Entered by the team",
  calls: "From the calls log",
};

const UNIT_SUFFIX: Record<Kpi["unit"], string | undefined> = {
  pct: "%",
  hours: "h",
  count: undefined,
};

export function Kpis() {
  const { canEdit } = useMe();

  const q = useQuery({
    queryKey: qk.kpis(),
    queryFn: () => api<KpisResponse>("/api/kpis"),
    refetchInterval: REFRESH.dashboard,
  });

  const add = useToastMutation(
    ({ key, value, on }: { key: string; value: number; on: string }) =>
      api(`/api/kpis/${key}/entries`, { method: "POST", body: JSON.stringify({ value, on }) }),
    { invalidates: [...INVALIDATE.kpiWrite], successMessage: "Reading saved" },
  );

  return (
    <>
      <PageHeader title="Analytics" subtitle="KPIs, their thresholds, and the funnel maths behind the plan." />
      <QueryBoundary q={q} label="analytics" skeletonRows={10}>
        {(data) => {
          const manual = data.kpis.filter((k) => k.source === "manual");
          return (
            <Space direction="vertical" size="large" style={{ width: "100%" }}>
              <SourceNote source={data.plan_source} />
              <EngineNote available={data.engine_available} error={data.engine_error} />

              <Card size="small" title="KPIs to track weekly" styles={{ body: { padding: 0 } }}>
                {data.kpis.length === 0 ? (
                  <div style={{ padding: 16 }}>
                    <EmptyState title="No KPIs defined" hint="They sync from the KPI table in the plan document." />
                  </div>
                ) : (
                  <Table<Kpi>
                    rowKey="key"
                    size="small"
                    pagination={false}
                    dataSource={data.kpis}
                    scroll={{ x: 1000 }}
                    columns={[
                      { title: "KPI", dataIndex: "name", ellipsis: true },
                      {
                        title: "Actual",
                        width: 110,
                        align: "right",
                        render: (_, k) => <b>{fmt(k.value, k.unit)}</b>,
                      },
                      { title: "Target", width: 100, align: "right", render: (_, k) => rule(k) },
                      {
                        title: "Kill / fix threshold",
                        width: 200,
                        render: (_, k) =>
                          k.threshold === null ? (
                            <Typography.Text type="secondary">{DASH}</Typography.Text>
                          ) : (
                            <span>
                              {k.direction === "higher" ? "<" : "≥"}
                              {fmt(k.threshold, k.unit)}
                              {k.min_sample > 1 && (
                                <Typography.Text type="secondary"> (after {k.min_sample})</Typography.Text>
                              )}
                            </span>
                          ),
                      },
                      { title: "If crossed", dataIndex: "action", ellipsis: true },
                      { title: "Status", width: 140, render: (_, k) => <KpiTag status={k.status} /> },
                      {
                        title: "Data",
                        render: (_, k) => (
                          <Typography.Text type="secondary">
                            {SOURCE_LABEL[k.source]}
                            {k.sample ? ` · n=${fmtNum(k.sample)}` : ""}
                            {k.note ? ` · ${k.note}` : ""}
                          </Typography.Text>
                        ),
                      },
                    ]}
                  />
                )}
              </Card>

              {canEdit && manual.length > 0 && (
                <Card size="small" title="Enter a reading">
                  <Space direction="vertical" size="small" style={{ width: "100%" }}>
                    {manual.map((k) => (
                      <Form
                        key={k.key}
                        layout="inline"
                        initialValues={{ on: dayjs() }}
                        onFinish={(v) =>
                          add.mutate({ key: k.key, value: v.value, on: v.on.format("YYYY-MM-DD") })
                        }
                      >
                        <Form.Item label={k.name} name="value" rules={[{ required: true, message: "Required" }]}>
                          <InputNumber
                            min={0}
                            step={0.01}
                            addonAfter={UNIT_SUFFIX[k.unit]}
                            aria-label={`${k.name} value`}
                          />
                        </Form.Item>
                        <Form.Item name="on" rules={[{ required: true, message: "Required" }]}>
                          <DatePicker aria-label={`${k.name} date`} />
                        </Form.Item>
                        <Button
                          htmlType="submit"
                          type="primary"
                          loading={add.isPending && add.variables?.key === k.key}
                        >
                          Save
                        </Button>
                      </Form>
                    ))}
                  </Space>
                  {data.entries.length > 0 && (
                    <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                      Recent:{" "}
                      {data.entries
                        .slice(0, 5)
                        .map((e) => `${fmtDate(e.on)} ${e.value}`)
                        .join(" · ")}
                    </Typography.Paragraph>
                  )}
                </Card>
              )}

              <Card size="small" title="Funnel maths: planning estimates vs actual" styles={{ body: { padding: 0 } }}>
                {data.funnel.length === 0 ? (
                  <div style={{ padding: 16 }}>
                    <EmptyState title="No funnel stages" hint="They sync from the funnel table in the plan document." />
                  </div>
                ) : (
                  <Table<FunnelRow>
                    rowKey="key"
                    size="small"
                    pagination={false}
                    dataSource={data.funnel}
                    scroll={{ x: 1000 }}
                    columns={[
                      { title: "Stage", dataIndex: "name", width: 200 },
                      {
                        title: "Assumption",
                        render: (_, f) => (
                          <Space wrap size={4}>
                            {f.assumption}
                            {f.is_assumption && <Tag color="orange">assumption, not a benchmark</Tag>}
                          </Space>
                        ),
                      },
                      { title: "Source", dataIndex: "source", width: 180, ellipsis: true },
                      {
                        title: "30-day plan",
                        width: 130,
                        align: "right",
                        render: (_, f) =>
                          f.plan_low === f.plan_high ? `~${fmtNum(f.plan_low)}` : `~${fmtNum(f.plan_low)}–${fmtNum(f.plan_high)}`,
                      },
                      {
                        title: "Actual",
                        width: 100,
                        align: "right",
                        render: (_, f) => (f.actual === null ? <Typography.Text type="secondary">{DASH}</Typography.Text> : <b>{fmtNum(f.actual)}</b>),
                      },
                      {
                        title: "vs plan (low end)",
                        width: 190,
                        render: (_, f) =>
                          f.progress_pct === null ? (
                            <Typography.Text type="secondary">{DASH}</Typography.Text>
                          ) : (
                            <Tooltip title={`${f.progress_pct}% of the low-end plan`}>
                              {/* `Math.min(100, …)` left negative values and NaN to antd. */}
                              <Progress size="small" percent={clampPct(f.progress_pct)} />
                            </Tooltip>
                          ),
                      },
                    ]}
                  />
                )}
                {data.plan_source.outcome && (
                  <div style={{ padding: 16, paddingTop: 0 }}>
                    <Typography.Paragraph style={{ marginBottom: 0 }}>
                      <b>Realistic month-1 outcome:</b> {data.plan_source.outcome}
                    </Typography.Paragraph>
                  </div>
                )}
              </Card>
            </Space>
          );
        }}
      </QueryBoundary>
    </>
  );
}