import { useQuery } from "@tanstack/react-query";
import { Button, Card, Col, Progress, Row, Statistic, Table, Tooltip, Typography } from "antd";
import { useNavigate } from "react-router";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { DASH, clampPct, fmtNum } from "../lib/format";
import { SERIES } from "../theme";
import { qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import type { Quality } from "../types/api";

/**
 * Band colours. The API emits these labels with an en-dash (U+2013) — "90–100" —
 * which is why they are written as escapes rather than literals: an en-dash is
 * invisible in a diff and trivially lost to an editor or a copy/paste.
 */
const BAND_COLORS: Record<string, string> = {
  "90\u2013100": SERIES[1],
  "70\u201389": "#52a36f",
  "50\u201369": SERIES[2],
  "0\u201349": SERIES[5],
};

const bandColor = (band: string) => BAND_COLORS[band] ?? "#8c8c8c";

export function Quality() {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: qk.quality(),
    queryFn: () => api<Quality>("/api/quality"),
    refetchInterval: REFRESH.dashboard,
  });

  return (
    <>
      <PageHeader
        title="Data quality"
        subtitle="Each company is scored from weighted checks. Scores update after every discovery run."
        extra={
          <Button onClick={() => navigate("/companies")}>Review companies</Button>
        }
      />
      <QueryBoundary q={q} label="data quality" skeletonRows={8}>
        {(data) => {
          const max = Math.max(1, ...data.bands.map((b) => b.count));
          const totalChecked = data.checks.reduce(
            (acc, c) => acc + c.passed + c.failed,
            0,
          );
          const totalPassed = data.checks.reduce((acc, c) => acc + c.passed, 0);
          const overall = totalChecked ? Math.round((totalPassed / totalChecked) * 100) : 0;

          return (
            <>
              <Row gutter={[16, 16]}>
                <Col xs={24} md={8}>
                  <Card size="small">
                    <Statistic title="Scored companies" value={data.scored} />
                    <Typography.Text type="secondary">
                      {fmtNum(data.unscored)} awaiting verification
                    </Typography.Text>
                    {/*
                      `data.stale` is counted server-side against its own freshness
                      window (verify.py). Restating that window here as a literal would
                      duplicate a backend threshold in the UI, so link to it instead.
                    */}
                    {data.stale > 0 && (
                      <Typography.Link
                        style={{ fontSize: 12, display: "block" }}
                        onClick={() => navigate("/engine")}
                      >
                        {fmtNum(data.stale)} need re-verification →
                      </Typography.Link>
                    )}
                  </Card>
                </Col>
                <Col xs={24} md={8}>
                  <Card size="small">
                    <Statistic title="Checks passed" value={overall} suffix="%" />
                    <Typography.Text type="secondary">
                      {fmtNum(totalPassed)} of {fmtNum(totalChecked)} individual checks
                    </Typography.Text>
                  </Card>
                </Col>
                <Col xs={24} md={8}>
                  <Card size="small" title="Fit distribution">
                    {data.fit_bands?.length ? (
                      data.fit_bands.map((b) => (
                        <div
                          key={b.band}
                          style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}
                        >
                          <span style={{ width: 52, fontVariantNumeric: "tabular-nums", fontSize: 12 }}>
                            {b.band}
                          </span>
                          <div style={{ flex: 1 }}>
                            <Progress
                              percent={clampPct((b.count / max) * 100)}
                              showInfo={false}
                              strokeColor={bandColor(b.band)}
                              size="small"
                            />
                          </div>
                          <span
                            className="num"
                            style={{ width: 44, textAlign: "right", fontSize: 12 }}
                          >
                            {fmtNum(b.count)}
                          </span>
                        </div>
                      ))
                    ) : (
                      <Typography.Text type="secondary">Not scored yet</Typography.Text>
                    )}
                  </Card>
                </Col>
                <Col xs={24} md={24}>
                  <Card size="small" title="Quality score distribution">
                    {data.bands.map((b) => (
                      <div
                        key={b.band}
                        style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}
                      >
                        <span style={{ width: 56, fontVariantNumeric: "tabular-nums" }}>{b.band}</span>
                        <div style={{ flex: 1 }}>
                          <Progress
                            percent={clampPct((b.count / max) * 100)}
                            showInfo={false}
                            strokeColor={bandColor(b.band)}
                          />
                        </div>
                        <span
                          className="num"
                          style={{ width: 56, textAlign: "right", fontVariantNumeric: "tabular-nums" }}
                        >
                          {fmtNum(b.count)}
                        </span>
                      </div>
                    ))}
                  </Card>
                </Col>
              </Row>

              <Card size="small" title="Checks" style={{ marginTop: 16 }} styles={{ body: { padding: 0 } }}>
                {data.checks.length === 0 ? (
                  <div style={{ padding: 16 }}>
                    <EmptyState
                      compact
                      title="No quality checks recorded yet"
                      hint="Checks are written the first time a company is verified."
                    />
                  </div>
                ) : (
                  <Table
                    rowKey="check"
                    size="small"
                    pagination={false}
                    dataSource={data.checks}
                    scroll={{ x: true }}
                    columns={[
                      { title: "Check", dataIndex: "label", ellipsis: true },
                      {
                        title: "Weight",
                        dataIndex: "weight",
                        width: 80,
                        align: "right",
                        sorter: (a, b) => a.weight - b.weight,
                      },
                      {
                        title: "Passed",
                        dataIndex: "passed",
                        width: 96,
                        align: "right",
                        sorter: (a, b) => a.passed - b.passed,
                      },
                      {
                        title: "Failed",
                        dataIndex: "failed",
                        width: 96,
                        align: "right",
                        sorter: (a, b) => a.failed - b.failed,
                      },
                      {
                        title: "Pass rate",
                        width: 260,
                        sorter: (a, b) =>
                          (a.passed + a.failed ? a.passed / (a.passed + a.failed) : 0) -
                          (b.passed + b.failed ? b.passed / (b.passed + b.failed) : 0),
                        render: (_, c) => {
                          const total = c.passed + c.failed;
                          if (!total) return <Typography.Text type="secondary">{DASH}</Typography.Text>;
                          const pct = Math.round((c.passed / total) * 100);
                          return (
                            <Tooltip title={`${fmtNum(c.passed)} passed of ${fmtNum(total)} checked`}>
                              <Progress percent={pct} size="small" status={pct < 50 ? "exception" : undefined} />
                            </Tooltip>
                          );
                        },
                      },
                    ]}
                  />
                )}
              </Card>
            </>
          );
        }}
      </QueryBoundary>
    </>
  );
}