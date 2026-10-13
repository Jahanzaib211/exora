import { useQuery } from "@tanstack/react-query";
import { Badge, Card, Checkbox, Progress, Space, Tag, Tooltip, Typography } from "antd";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { useToastMutation } from "../hooks/useToastMutation";
import { clampPct, fmtDate } from "../lib/format";
import { INVALIDATE, qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import type { RoutineResponse } from "../types/api";

/** What each live counter means. Kept exhaustive so a new counter cannot render blank. */
const COUNTER_LABEL: Record<string, string> = {
  open_replies: "replies waiting",
  linkedin_due: "LinkedIn tasks due",
  pending_approvals: "drafts to approve",
  due_tomorrow: "leads due tomorrow",
};

export function Routine() {
  const { canEdit } = useMe();

  const q = useQuery({
    queryKey: qk.routine(),
    queryFn: () => api<RoutineResponse>("/api/routine"),
    refetchInterval: REFRESH.dashboard,
  });

  const toggle = useToastMutation((id: number) => api(`/api/routine/${id}/toggle`, { method: "POST" }), {
    invalidates: [...INVALIDATE.planWrite],
  });

  return (
    <>
      <PageHeader title="Daily routine" subtitle="The 60-minute loop from the plan document, with live counts." />
      <QueryBoundary q={q} label="the daily routine">
        {(data) => {
          const total = data.items.reduce((a, i) => a + i.minutes, 0);
          const done = data.items.filter((i) => i.done);
          return (
            <Space direction="vertical" size="large" style={{ width: "100%" }}>
              {/* `/api/routine` does not report engine availability, so no EngineNote here:
                  showing one would need a value the endpoint never sends. */}
              <Card title={`${total}-minute routine · ${fmtDate(data.day)}`}>
                {data.items.length === 0 ? (
                  <EmptyState
                    title="No routine items"
                    hint="The routine is parsed from the “Daily routine” section of the plan document."
                  />
                ) : (
                  <>
                    <Progress
                      percent={clampPct((done.length / data.items.length) * 100)}
                      format={() => `${done.length}/${data.items.length}`}
                    />
                    <Space direction="vertical" size="small" style={{ width: "100%", marginTop: 16 }}>
                      {data.items.map((item) => (
                        <Card size="small" key={item.id}>
                          <Space align="start" style={{ width: "100%" }}>
                            <Checkbox
                              checked={item.done}
                              disabled={!canEdit || (toggle.isPending && toggle.variables === item.id)}
                              aria-label={`${item.done ? "Mark incomplete" : "Mark complete"}: ${item.title}`}
                              onChange={() => toggle.mutate(item.id)}
                            />
                            <Space direction="vertical" size={2} style={{ minWidth: 0, flex: 1 }}>
                              <Space wrap size={8}>
                                <Tag style={{ marginInlineEnd: 0 }}>{item.minutes} min</Tag>
                                <Typography.Text delete={item.done} strong>
                                  {item.title}
                                </Typography.Text>
                                {item.live_counter && (
                                  <Tooltip
                                    title={
                                      item.live_count === null
                                        ? "Live count unavailable — engine offline"
                                        : `${item.live_count} ${COUNTER_LABEL[item.live_counter] ?? ""}`
                                    }
                                  >
                                    <Badge
                                      count={
                                        item.live_count === null
                                          ? "—"
                                          : `${item.live_count} ${COUNTER_LABEL[item.live_counter] ?? ""}`
                                      }
                                      color={item.live_count ? "#2f5bea" : "#bfbfbf"}
                                      overflowCount={999}
                                    />
                                  </Tooltip>
                                )}
                              </Space>
                              {item.detail && (
                                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                                  {item.detail}
                                </Typography.Text>
                              )}
                              {item.team_done.length > 0 && (
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  Done today by: {item.team_done.join(", ")}
                                </Typography.Text>
                              )}
                            </Space>
                          </Space>
                        </Card>
                      ))}
                    </Space>
                  </>
                )}
              </Card>
            </Space>
          );
        }}
      </QueryBoundary>
    </>
  );
}