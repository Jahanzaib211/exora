import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Col, Empty, Progress, Row, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from "antd";
import { api } from "../api";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";

type Run = {
  id: number;
  source: string;
  source_name: string;
  params: Record<string, unknown>;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  found: number;
  created: number;
  updated: number;
  errors: number;
  message: string | null;
  automation: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type SeriesPoint = {
  day: string;
  companies: number;
  people: number;
  with_website: number;
  crawled: number;
  with_email: number;
  with_phone: number;
  with_decision_maker: number;
  high_quality: number;
  avg_quality: number | null;
  runs: number;
  run_errors: number;
};

type Engine = {
  status: "running" | "stopped" | "starting";
  heartbeat_age_s: number | null;
  worker_threads: number;
  started_at: string | null;
  automations_enabled: number;
  queue: { queued: number; running: Run[] };
  last_24h: {
    runs: number;
    records_found: number;
    new_companies: number;
    new_people: number;
    failed_runs: number;
  };
  series: SeriesPoint[];
  cache: { version: number; entries: number };
};

type Quality = {
  tiers: { tier: string; rule: string; count: number }[];
  scored: number;
};

// validated ordinal blue ramp (single hue, monotone lightness): golden > verified > partial > raw
const RAMP = ["#184f95", "#2a78d6", "#5598e7", "#86b6ef"];
export const TIER_COLOR: Record<string, string> = { golden: RAMP[0], verified: RAMP[1], partial: RAMP[2], raw: RAMP[3] };
const STATUS_COLOR: Record<Engine["status"], string> = { running: "success", starting: "processing", stopped: "error" };
const RUN_STATUS = { queued: "default", running: "processing", done: "success", failed: "error", cancelled: "default" } as const;

function heartbeatAge(s: number | null): string {
  if (s === null) return "—";
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ${s % 60}s ago`;
}

function when(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

function GrowthChart({ series }: { series: SeriesPoint[] }) {
  const w = 720;
  const h = 220;
  const pad = 14;
  const max = Math.max(1, ...series.map((d) => Math.max(d.companies, d.people)));
  const x = (i: number) => pad + (i / Math.max(1, series.length - 1)) * (w - pad * 2);
  const y = (v: number) => h - pad - (v / max) * (h - pad * 2);
  const line = (get: (d: SeriesPoint) => number) =>
    series.map((d, i) => `${x(i).toFixed(1)},${y(get(d)).toFixed(1)}`).join(" ");
  const first = series[0];
  const last = series[series.length - 1];
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto" }} role="img" aria-label="Companies and people growth">
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke="#e6e9ef" strokeWidth={1} />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} stroke="#e6e9ef" strokeWidth={1} />
        <line x1={pad} y1={y(max)} x2={w - pad} y2={y(max)} stroke="#eef1f6" strokeWidth={1} />
        <polyline points={line((d) => d.companies)} fill="none" stroke={RAMP[0]} strokeWidth={2} />
        <polyline points={line((d) => d.people)} fill="none" stroke={RAMP[1]} strokeWidth={2} />
        <circle cx={x(series.length - 1)} cy={y(last.companies)} r={3} fill={RAMP[0]} />
        <circle cx={x(series.length - 1)} cy={y(last.people)} r={3} fill={RAMP[1]} />
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12, color: "#5b6577" }}>
        <span>{first.day}</span>
        <Space size={14} wrap>
          <span style={{ color: RAMP[0], fontWeight: 600 }}>Companies {last.companies.toLocaleString()}</span>
          <span style={{ color: RAMP[1], fontWeight: 600 }}>People {last.people.toLocaleString()}</span>
          <span>peak {max.toLocaleString()}</span>
        </Space>
        <span>{last.day}</span>
      </div>
    </Space>
  );
}

export function Engine() {
  const engine = useQuery({
    queryKey: ["engine"],
    queryFn: () => api<Engine>("/api/engine"),
    refetchInterval: REFRESH.dashboard,
  });
  const quality = useQuery({ queryKey: ["quality"], queryFn: () => api<Quality>("/api/quality") });

  if (engine.isLoading) return <Skeleton active />;
  if (engine.isError) return <Alert type="error" showIcon message={engine.error.message} />;
  const e = engine.data;
  if (!e) return <Skeleton active />;

  const tiers = quality.data?.tiers ?? [];
  const maxTier = Math.max(1, ...tiers.map((t) => t.count));

  return (
    <>
      <PageHeader
        title="Data engine"
        subtitle="Discovery workers, queue and the daily snapshot of the database"
        extra={
          <Space>
            <Tag color={STATUS_COLOR[e.status]} bordered={false}>
              {e.status}
            </Tag>
            <Typography.Text type="secondary">heartbeat {heartbeatAge(e.heartbeat_age_s)}</Typography.Text>
          </Space>
        }
      />
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {e.status !== "running" && (
          <Alert
            type="warning"
            showIcon
            message={`Scheduler ${e.status}`}
            description={
              e.heartbeat_age_s === null
                ? "No heartbeat has been recorded yet, so no runs are being picked up."
                : `Last heartbeat ${heartbeatAge(e.heartbeat_age_s)}.`
            }
          />
        )}
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Worker threads" value={e.worker_threads} />
              <Typography.Text type="secondary">started {when(e.started_at)}</Typography.Text>
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Queued runs" value={e.queue.queued} />
              <Typography.Text type="secondary">{e.queue.running.length} running now</Typography.Text>
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Automations enabled" value={e.automations_enabled} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Cache entries" value={e.cache.entries} />
              <Typography.Text type="secondary">version {e.cache.version}</Typography.Text>
            </Card>
          </Col>
        </Row>

        <Card size="small" title="Last 24 hours">
          <Row gutter={[16, 16]}>
            <Col xs={12} md={5}>
              <Statistic title="Runs" value={e.last_24h.runs} />
            </Col>
            <Col xs={12} md={5}>
              <Statistic title="Records found" value={e.last_24h.records_found} />
            </Col>
            <Col xs={12} md={5}>
              <Statistic title="New companies" value={e.last_24h.new_companies} />
            </Col>
            <Col xs={12} md={5}>
              <Statistic title="New people" value={e.last_24h.new_people} />
            </Col>
            <Col xs={12} md={4}>
              <Statistic
                title="Failed runs"
                value={e.last_24h.failed_runs}
                valueStyle={e.last_24h.failed_runs ? { color: "#d4380d" } : undefined}
              />
            </Col>
          </Row>
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={16}>
            <Card size="small" title={`Growth · last ${e.series.length} days`}>
              {e.series.length ? (
                <GrowthChart series={e.series} />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No daily snapshots recorded yet" />
              )}
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card size="small" title="Company tiers" extra={quality.data ? <Typography.Text type="secondary">{quality.data.scored} scored</Typography.Text> : null}>
              {quality.isLoading ? (
                <Skeleton active paragraph={{ rows: 4 }} />
              ) : quality.isError ? (
                <Alert type="error" showIcon message={quality.error.message} />
              ) : tiers.length ? (
                tiers.map((t) => (
                  <div key={t.tier} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
                    <Tooltip title={t.rule}>
                      <span style={{ width: 80, textTransform: "capitalize" }}>{t.tier}</span>
                    </Tooltip>
                    <div style={{ flex: 1 }}>
                      <Progress percent={(t.count / maxTier) * 100} showInfo={false} strokeColor={TIER_COLOR[t.tier] ?? RAMP[1]} />
                    </div>
                    <span style={{ width: 56, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{t.count}</span>
                  </div>
                ))
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No tiers recorded yet" />
              )}
            </Card>
          </Col>
        </Row>

        <Card size="small" title="Runs in progress" styles={{ body: { padding: 0 } }}>
          <Table<Run>
            rowKey="id"
            size="small"
            dataSource={e.queue.running}
            locale={{ emptyText: "Nothing is running right now" }}
            pagination={false}
            columns={[
              { title: "Run", dataIndex: "id", width: 80 },
              { title: "Source", dataIndex: "source_name", width: 180 },
              { title: "Automation", dataIndex: "automation", render: (v) => v ?? "—" },
              { title: "Status", width: 110, render: (_, r) => <Tag color={RUN_STATUS[r.status]} bordered={false}>{r.status}</Tag> },
              { title: "Started", width: 150, render: (_, r) => when(r.started_at) },
              { title: "Found", dataIndex: "found", width: 80, align: "right" },
              { title: "New", dataIndex: "created", width: 80, align: "right" },
              { title: "Errors", dataIndex: "errors", width: 80, align: "right" },
              { title: "Message", dataIndex: "message", ellipsis: true, render: (v) => v ?? "—" },
            ]}
          />
        </Card>
      </Space>
    </>
  );
}
