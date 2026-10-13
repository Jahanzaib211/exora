import { useQuery } from "@tanstack/react-query";
import { Alert, Badge, Button, Card, Col, Progress, Row, Skeleton, Space, Statistic, Table, Typography } from "antd";
import { useNavigate } from "react-router";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { clampPct, fmtDate, fmtDateTime, fmtMoney, fmtNum, relTime } from "../lib/format";
import { qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { KpiTag, fmt, rule } from "../shared";
import type { Dashboard, Facets, Quality, Run, RunStatus } from "../types/api";

/** Badge status for a run. One definition, previously duplicated on four tabs. */
export const RUN_STATUS: Record<RunStatus, "default" | "processing" | "success" | "error" | "warning"> = {
  queued: "default",
  running: "processing",
  done: "success",
  failed: "error",
  cancelled: "warning",
};

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** A KPI tile that shows a skeleton rather than a fabricated `0` while loading. */
function Tile({ title, value, suffix, hint }: { title: string; value: number | undefined; suffix?: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <Card size="small">
      <Statistic
        title={title}
        value={value ?? 0}
        suffix={suffix}
        loading={value === undefined}
        valueStyle={{ fontVariantNumeric: "tabular-nums" }}
      />
      {hint && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {hint}
        </Typography.Text>
      )}
    </Card>
  );
}

export function Dashboard() {
  const navigate = useNavigate();

  const dash = useQuery({ queryKey: qk.dashboard(), queryFn: () => api<Dashboard>("/api/dashboard"), refetchInterval: REFRESH.dashboard });
  const facets = useQuery({ queryKey: qk.companyFacets(), queryFn: () => api<Facets>("/api/companies/facets"), refetchInterval: REFRESH.dashboard });
  const quality = useQuery({ queryKey: qk.quality(), queryFn: () => api<Quality>("/api/quality"), refetchInterval: REFRESH.dashboard });
  // Deliberately a *different* key from the Discovery tab's full run history:
  // both used `["runs"]` while requesting different endpoints.
  const runs = useQuery({ queryKey: qk.recentRuns(6), queryFn: () => api<Run[]>("/api/runs?limit=6"), refetchInterval: REFRESH.dashboard });

  const d = dash.data;
  /*
  `ready` defaults to 0 rather than undefined once the facets have loaded. Leaving it
  undefined made `Tile` render its skeleton forever, because antd's `Statistic
  loading` never resolves — the value simply never arrived.
*/
const ready = facets.data ? (facets.data.status.find((s) => s.value === "ready")?.count ?? 0) : undefined;
  // Band labels carry an en-dash (U+2013) — written as an escape so it survives copy/paste.
  const highQuality = quality.data?.bands
    .filter((b) => b.band === "90\u2013100" || b.band === "70\u201389")
    .reduce((a, b) => a + b.count, 0);

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={
          d
            ? d.campaign.starts_in_days > 0
              ? `Campaign starts ${fmtDate(d.campaign.start)} (in ${d.campaign.starts_in_days} days)`
              : `Campaign day ${d.campaign.day}`
            : undefined
        }
        extra={
          <Button type="primary" onClick={() => navigate("/discovery")}>
            Start a discovery run
          </Button>
        }
      />
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {d?.warnings.map((w) => (
          <Alert key={w} type="warning" showIcon message={w} />
        ))}
        {d?.kpis
          .filter((k) => k.status === "red")
          .map((k) => (
            <Alert
              key={k.key}
              type="error"
              showIcon
              message={`${k.name} is ${fmt(k.value, k.unit)}, past the ${fmt(k.threshold, k.unit)} threshold`}
              description={k.action}
            />
          ))}

        <Row gutter={[16, 16]}>
          <Col xs={12} lg={6}>
            <Tile title="Companies" value={facets.data?.total} />
          </Col>
          <Col xs={12} lg={6}>
            <Tile title="Named people" value={facets.data?.people} />
          </Col>
          <Col xs={12} lg={6}>
            <Tile
              title="Quality 70+"
              value={highQuality}
              suffix={quality.data ? <Typography.Text type="secondary" style={{ fontSize: 13 }}>/ {fmtNum(quality.data.scored)}</Typography.Text> : undefined}
            />
          </Col>
          <Col xs={12} lg={6}>
            <Tile title="Ready for outreach" value={ready} />
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <Card
              size="small"
              title="Today"
              extra={
                <Button type="link" size="small" onClick={() => navigate("/playbook?tab=routine")}>
                  Daily routine
                </Button>
              }
            >
              <Row gutter={16}>
                {(
                  [
                    ["Drafts to approve", d?.engine.pending_approvals],
                    ["Replies to answer", d?.engine.open_replies],
                    ["LinkedIn tasks", d?.engine.linkedin_due],
                  ] as const
                ).map(([label, value]) => (
                  <Col span={8} key={label}>
                    {/*
                      When the engine is offline the figure is unknown, not zero. Showing
                      `0` would claim "no replies to answer" precisely when replies may be
                      piling up unseen.
                    */}
                    <Statistic
                      title={label}
                      value={d?.engine.available ? (value ?? 0) : "—"}
                      valueStyle={
                        d?.engine.available ? undefined : { color: "var(--exora-muted, #8c8c8c)" }
                      }
                    />
                  </Col>
                ))}
              </Row>
              {d && !d.engine.available && (
                <Typography.Text type="secondary">
                  Outreach engine offline: outreach figures unavailable.
                </Typography.Text>
              )}
              {d?.engine.dry_run && (
                <Typography.Text type="secondary"> Outreach is in test mode: emails are not sent.</Typography.Text>
              )}
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card
              size="small"
              title={d?.week ? `${d.week.label} · ${fmtDate(d.week.start)} – ${fmtDate(d.week.end)}` : "This week"}
              extra={
                <Button type="link" size="small" onClick={() => navigate("/playbook")}>
                  Plan
                </Button>
              }
            >
              <Progress
                percent={clampPct(d?.week?.total ? (d.week.done / d.week.total) * 100 : 0)}
                format={() => (d?.week ? `${d.week.done}/${d.week.total} tasks` : "")}
              />
              <Space size="large" style={{ marginTop: 8 }} wrap>
                <span>
                  Pilots won: <b>{d ? fmtNum(d.calls.pilots) : "—"}</b> of {d ? fmtNum(d.calls.booked) : "—"} calls
                </span>
                {d?.calls.pilot_value ? <span>Pilot value: <b>{fmtMoney(d.calls.pilot_value)}</b></span> : null}
                {d?.next_event && (
                  <span>
                    Next event: <b>{d.next_event.name}</b>
                    {d.next_event.city ? `, ${d.next_event.city}` : ""} — {d.next_event.days_until === 0 ? "today" : d.next_event.days_until === 1 ? "tomorrow" : `in ${d.next_event.days_until} days`}
                  </span>
                )}
              </Space>
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <Card
              size="small"
              title="Recent discovery runs"
              extra={
                <Button type="link" size="small" onClick={() => navigate("/discovery")}>
                  Discovery
                </Button>
              }
              styles={{ body: { padding: 0 } }}
            >
              {!runs.data ? (
                <div style={{ padding: 16 }}>
                  <Skeleton active paragraph={{ rows: 5 }} title={false} />
                </div>
              ) : runs.data.length === 0 ? (
                <div style={{ padding: 16 }}>
                  <EmptyState
                    compact
                    title="No discovery runs yet"
                    action={{ label: "Start one", onClick: () => navigate("/discovery") }}
                  />
                </div>
              ) : (
                <Table<Run>
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={runs.data}
                  columns={[
                    { title: "Source", dataIndex: "source_name", ellipsis: true },
                    {
                      title: "Status",
                      width: 120,
                      render: (_, r) => <Badge status={RUN_STATUS[r.status]} text={RUN_STATUS_LABEL[r.status]} />,
                    },
                    { title: "Found", dataIndex: "found", width: 80, align: "right" },
                    { title: "New", dataIndex: "created", width: 72, align: "right" },
                    {
                      title: "Started",
                      width: 150,
                      // `r.created_at.slice(...)` threw on a null timestamp.
                      render: (_, r) => (
                        <Typography.Text type="secondary" title={fmtDateTime(r.started_at ?? r.created_at)}>
                          {relTime(r.started_at ?? r.created_at)}
                        </Typography.Text>
                      ),
                    },
                  ]}
                />
              )}
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card
              size="small"
              title="KPIs"
              extra={
                <Button type="link" size="small" onClick={() => navigate("/analytics")}>
                  Analytics
                </Button>
              }
              styles={{ body: { padding: 0 } }}
            >
              {!d ? (
                <div style={{ padding: 16 }}>
                  <Skeleton active paragraph={{ rows: 6 }} title={false} />
                </div>
              ) : d.kpis.length === 0 ? (
                <div style={{ padding: 16 }}>
                  <EmptyState compact title="No KPIs defined yet" hint="They sync from the plan document." />
                </div>
              ) : (
                <Table
                  rowKey="key"
                  size="small"
                  pagination={false}
                  dataSource={d.kpis}
                  columns={[
                    { title: "KPI", dataIndex: "name", ellipsis: true },
                    { title: "Actual", render: (_, k) => fmt(k.value, k.unit), align: "right", width: 90 },
                    { title: "Target", render: (_, k) => rule(k), align: "right", width: 90 },
                    { title: "Status", width: 130, render: (_, k) => <KpiTag status={k.status} /> },
                  ]}
                />
              )}
            </Card>
          </Col>
        </Row>
      </Space>
    </>
  );
}