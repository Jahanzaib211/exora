import { CheckOutlined, CloseOutlined, SendOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Dropdown,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api, post } from "../api";
import { OUTREACH_ENABLED } from "../brand";
import { EmptyState } from "../components/EmptyState";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";

type Summary = {
  dry_run: boolean;
  paused: boolean;
  leads_by_status: Record<string, number>;
  pending_drafts: number;
  linkedin_due: number;
  emails_sent: number;
  postal_address_ok: boolean;
  mailboxes: number;
};

type Draft = {
  message_id: number;
  lead_id: string;
  company: string | null;
  region: string | null;
  sequence: string | null;
  kind: string;
  step: string | null;
  channel: string;
  first_touch: boolean;
  to: string | null;
  subject: string | null;
  body: string;
  opener: string | null;
  opener_source: string | null;
  warnings: string[];
  status: string;
  created_at: string | null;
};

type LinkedinTask = {
  message_id: number;
  lead_id: string;
  company: string | null;
  region: string | null;
  step: string | null;
  text: string;
  condition: string | null;
  due: string | null;
  overdue: boolean;
  person: string | null;
  title: string | null;
  target_roles: string[] | null;
  company_linkedin: string | null;
  warnings: string[];
};

type Lead = {
  lead_id: string;
  company: string | null;
  region: string | null;
  sequence: string | null;
  status: string;
  stop_reason: string | null;
  step_index: number | null;
  next_due_at: string | null;
  last_touch_at: string | null;
  email: string | null;
  contact: string | null;
};

type Decision = { message_id: number; action: string; status: string; outcome: string | null };
type TickResult = { steps: number; enrolled: number; flush: Record<string, unknown>; paused: boolean };
type PromoteResult = {
  promoted: { company_id: number; lead_id: string; created: boolean; region: string | null; sequence: string | null }[];
  enrolled: string[];
};
type CompanyPick = { id: number; name: string; domain: string | null; segment: string | null; country: string | null; status: string };

const LEAD_STATUS: Record<string, { text: string; color: string }> = {
  new: { text: "New", color: "default" },
  active: { text: "Active", color: "processing" },
  awaiting_approval: { text: "Awaiting approval", color: "orange" },
  completed: { text: "Completed", color: "success" },
  stopped: { text: "Stopped", color: "error" },
};
const LI_EVENTS = [
  { key: "sent", label: "Message sent" },
  { key: "accepted", label: "Connection accepted" },
  { key: "replied", label: "Replied" },
  { key: "skipped", label: "Skipped" },
];
const PROMOTE_STATUS = [
  { value: "ready", label: "Ready" },
  { value: "new", label: "New" },
  { value: "rejected", label: "Rejected" },
];

function LeadStatusTag({ status }: { status: string }) {
  const s = LEAD_STATUS[status];
  return <Tag color={s?.color ?? "default"}>{s?.text ?? status}</Tag>;
}

// ---- approvals -------------------------------------------------------------------------------
function ApprovalsTab() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit } = useMe();
  const [review, setReview] = useState<Draft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const drafts = useQuery({
    queryKey: ["outreach-drafts"],
    queryFn: () => api<Draft[]>("/api/outreach/drafts"),
    refetchInterval: REFRESH.lists,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["outreach-drafts"] });
    qc.invalidateQueries({ queryKey: ["outreach-summary"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" | "skip" }) => {
      const payload: Record<string, unknown> = { action };
      if (action === "approve") {
        payload.subject = subject || undefined;
        payload.body = body || undefined;
      }
      return post<Decision>(`/api/outreach/drafts/${id}/decision`, payload);
    },
    onSuccess: (r) => {
      message.success(r.action === "approve" ? "Draft approved and queued for its send window" : `Draft ${r.action}ed`);
      setReview(null);
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });

  if (drafts.isLoading) return <Skeleton active />;
  if (drafts.isError) return <Alert type="error" showIcon message={drafts.error.message} />;
  return (
    <>
      <Table<Draft>
        rowKey="message_id"
        size="small"
        dataSource={drafts.data}
        scroll={{ x: 900 }}
        locale={{ emptyText: "Nothing waiting for approval" }}
        expandable={{
          expandedRowRender: (d) => (
            <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", margin: 0 }}>{d.body}</pre>
          ),
        }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          {
            title: "Company",
            render: (_, d) => (
              <div style={{ lineHeight: 1.3 }}>
                <div style={{ fontWeight: 600 }}>{d.company ?? d.lead_id}</div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {[d.region, d.sequence, d.step].filter(Boolean).join(" · ")}
                </Typography.Text>
              </div>
            ),
          },
          { title: "Channel", width: 100, render: (_, d) => <Tag bordered={false}>{d.channel}</Tag> },
          { title: "To", dataIndex: "to", ellipsis: true },
          { title: "Subject", dataIndex: "subject", ellipsis: true, render: (v) => v ?? "—" },
          {
            title: "Guardrails",
            width: 170,
            render: (_, d) =>
              d.warnings.length ? (
                <Space size={4} wrap>
                  {d.warnings.map((w) => (
                    <Tag color="orange" key={w}>
                      {w}
                    </Tag>
                  ))}
                </Space>
              ) : (
                <Typography.Text type="secondary">No warnings</Typography.Text>
              ),
          },
          {
            title: "",
            width: 200,
            render: (_, d) =>
              canEdit ? (
                <Space size={4}>
                  <Button
                    size="small"
                    type="primary"
                    icon={<CheckOutlined />}
                    onClick={() => {
                      setReview(d);
                      setSubject(d.subject ?? "");
                      setBody(d.body);
                    }}
                  >
                    Review
                  </Button>
                  <Popconfirm title="Reject this draft? It will not be sent." onConfirm={() => decide.mutate({ id: d.message_id, action: "reject" })}>
                    <Button size="small" danger icon={<CloseOutlined />} />
                  </Popconfirm>
                </Space>
              ) : null,
          },
        ]}
      />
      <Modal
        title={review ? `Review draft · ${review.company ?? review.lead_id}` : ""}
        open={review !== null}
        onCancel={() => setReview(null)}
        footer={null}
        width={640}
        destroyOnHidden
      >
        {review && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="Human approval required"
              description="Approving queues this message for its scheduled send window. Region, suppression and footer checks run again at send time; nothing goes out on its own."
            />
            <DraftTags draft={review} />
            {review.warnings.length > 0 && (
              <Space size={4} wrap>
                {review.warnings.map((w) => (
                  <Tag color="orange" key={w}>
                    {w}
                  </Tag>
                ))}
              </Space>
            )}
            {review.channel === "email" && (
              <div>
                <div className="filter-label">Subject</div>
                <Input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={300} />
              </div>
            )}
            <div>
              <div className="filter-label">Body</div>
              <Input.TextArea value={body} onChange={(e) => setBody(e.target.value)} autoSize={{ minRows: 6, maxRows: 14 }} />
            </div>
            <Space style={{ width: "100%", justifyContent: "flex-end" }} wrap>
              <Button danger onClick={() => decide.mutate({ id: review.message_id, action: "reject" })} loading={decide.isPending}>
                Reject
              </Button>
              <Button onClick={() => decide.mutate({ id: review.message_id, action: "skip" })} loading={decide.isPending}>
                Skip
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                loading={decide.isPending}
                onClick={() => decide.mutate({ id: review.message_id, action: "approve" })}
              >
                Approve
              </Button>
            </Space>
          </Space>
        )}
      </Modal>
    </>
  );
}

function DraftTags({ draft }: { draft: Draft }) {
  return (
    <Space size={6} wrap>
      <Tag bordered={false}>{draft.kind}</Tag>
      {draft.first_touch && <Tag bordered={false}>first touch</Tag>}
      <Tag bordered={false}>{draft.sequence ?? "no sequence"}</Tag>
      {draft.region && <Tag bordered={false}>{draft.region}</Tag>}
      <Tag bordered={false}>{draft.status}</Tag>
      {draft.created_at && <Typography.Text type="secondary">drafted {draft.created_at.slice(0, 16).replace("T", " ")}</Typography.Text>}
    </Space>
  );
}

// ---- LinkedIn queue ---------------------------------------------------------------------------
function LinkedinTab() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit } = useMe();
  const tasks = useQuery({
    queryKey: ["outreach-linkedin"],
    queryFn: () => api<LinkedinTask[]>("/api/outreach/linkedin"),
    refetchInterval: REFRESH.lists,
  });
  const log = useMutation({
    mutationFn: ({ lead, event, step }: { lead: string; event: string; step: string | null }) =>
      post<{ result: string }>(`/api/outreach/linkedin/${encodeURIComponent(lead)}`, { event, step }),
    onSuccess: () => {
      message.success("LinkedIn step logged");
      qc.invalidateQueries({ queryKey: ["outreach-linkedin"] });
      qc.invalidateQueries({ queryKey: ["outreach-summary"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (e: Error) => message.error(e.message),
  });

  if (tasks.isLoading) return <Skeleton active />;
  if (tasks.isError) return <Alert type="error" showIcon message={tasks.error.message} />;
  return (
    <Table<LinkedinTask>
      rowKey="message_id"
      size="small"
      dataSource={tasks.data}
      scroll={{ x: 1000 }}
      locale={{ emptyText: "No LinkedIn steps due today" }}
      pagination={{ pageSize: 20, hideOnSinglePage: true }}
      expandable={{
        expandedRowRender: (t) => (
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", margin: 0 }}>{t.text}</pre>
        ),
      }}
      columns={[
        {
          title: "Company",
          render: (_, t) => (
            <div style={{ lineHeight: 1.3 }}>
              <div style={{ fontWeight: 600 }}>
                {t.company_linkedin ? (
                  <a href={t.company_linkedin} target="_blank" rel="noreferrer noopener">
                    {t.company ?? t.lead_id}
                  </a>
                ) : (
                  t.company ?? t.lead_id
                )}
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {[t.region, t.step].filter(Boolean).join(" · ")}
              </Typography.Text>
            </div>
          ),
        },
        {
          title: "Contact",
          render: (_, t) => (
            <div style={{ lineHeight: 1.3 }}>
              <div>{t.person ?? "—"}</div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t.title ?? ""}
              </Typography.Text>
            </div>
          ),
        },
        {
          title: "Message",
          dataIndex: "text",
          ellipsis: true,
        },
        {
          title: "Due",
          width: 140,
          render: (_, t) => (
            <Space size={6}>
              <span>{t.due ?? "—"}</span>
              {t.overdue && <Tag color="red">overdue</Tag>}
            </Space>
          ),
        },
        {
          title: "Warnings",
          width: 150,
          render: (_, t) =>
            t.warnings.length ? (
              <Space size={4} wrap>
                {t.warnings.map((w) => (
                  <Tag color="orange" key={w}>
                    {w}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">—</Typography.Text>
            ),
        },
        {
          title: "",
          width: 140,
          render: (_, t) =>
            canEdit ? (
              <Dropdown
                menu={{
                  items: LI_EVENTS.map((e) => ({ key: e.key, label: e.label })),
                  onClick: ({ key }) => log.mutate({ lead: t.lead_id, event: key, step: t.step }),
                }}
              >
                <Button size="small" loading={log.isPending}>
                  Log outcome
                </Button>
              </Dropdown>
            ) : null,
        },
      ]}
    />
  );
}

// ---- leads -----------------------------------------------------------------------------------
function LeadsTab() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string | undefined>(undefined);
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  if (status) query.set("status", status);
  const leads = useQuery({
    queryKey: ["outreach-leads", query.toString()],
    queryFn: () => api<Lead[]>(`/api/outreach/leads?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH.lists,
  });
  if (leads.isLoading) return <Skeleton active />;
  if (leads.isError) return <Alert type="error" showIcon message={leads.error.message} />;
  return (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <div className="table-toolbar">
        <Input.Search
          placeholder="Company name"
          allowClear
          style={{ width: 240 }}
          onSearch={(v) => setQ(v)}
        />
        <Select
          allowClear
          placeholder="Status"
          style={{ width: 200 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(LEAD_STATUS).map(([value, s]) => ({ value, label: s.text }))}
        />
      </div>
      <Table<Lead>
        rowKey="lead_id"
        size="small"
        dataSource={leads.data}
        scroll={{ x: 1000 }}
        locale={{ emptyText: "No leads match these filters" }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          { title: "Lead", dataIndex: "lead_id", width: 100 },
          {
            title: "Company",
            render: (_, l) => (
              <div style={{ lineHeight: 1.3 }}>
                <div style={{ fontWeight: 600 }}>{l.company}</div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {[l.region, l.sequence].filter(Boolean).join(" · ")}
                </Typography.Text>
              </div>
            ),
          },
          { title: "Status", width: 160, render: (_, l) => <LeadStatusTag status={l.status} /> },
          {
            title: "Step",
            width: 80,
            render: (_, l) => (l.step_index === null ? "—" : l.step_index),
          },
          { title: "Contact", render: (_, l) => l.contact ?? "—" },
          { title: "Email", dataIndex: "email", ellipsis: true, render: (v) => v ?? "—" },
          { title: "Next step", width: 150, render: (_, l) => (l.next_due_at ? l.next_due_at.slice(0, 16).replace("T", " ") : "—") },
          { title: "Last touch", width: 150, render: (_, l) => (l.last_touch_at ? l.last_touch_at.slice(0, 16).replace("T", " ") : "—") },
          {
            title: "Stop reason",
            dataIndex: "stop_reason",
            ellipsis: true,
            render: (v) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
          },
        ]}
      />
    </Card>
  );
}

// ---- promote ---------------------------------------------------------------------------------
function PromoteTab() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit } = useMe();
  const [statuses, setStatuses] = useState<string[]>(["ready"]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [enroll, setEnroll] = useState(true);
  const [page, setPage] = useState(1);
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  if (q) query.set("q", q);
  statuses.forEach((s) => query.append("status", s));
  const companies = useQuery({
    queryKey: ["promote-companies", query.toString()],
    queryFn: () => api<{ items: CompanyPick[]; total: number }>(`/api/companies?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH.lists,
  });
  const promote = useMutation({
    mutationFn: (ids: number[]) => post<PromoteResult>("/api/outreach/promote", { ids, enroll }),
    onSuccess: (r) => {
      message.success(`${r.promoted.length} companies promoted · ${r.enrolled.length} enrolled`);
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["promote-companies"] });
      qc.invalidateQueries({ queryKey: ["outreach-summary"] });
      qc.invalidateQueries({ queryKey: ["companies"] });
    },
    onError: (e: Error) => message.error(e.message),
  });

  return (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <div className="table-toolbar">
        <Input.Search placeholder="Name or domain" allowClear style={{ width: 220 }} onSearch={(v) => { setQ(v); setPage(1); }} />
        <Select
          mode="multiple"
          allowClear
          placeholder="Status"
          style={{ minWidth: 180 }}
          value={statuses}
          onChange={(v) => {
            setStatuses(v);
            setPage(1);
          }}
          options={PROMOTE_STATUS}
          maxTagCount="responsive"
        />
        <Checkbox checked={enroll} onChange={(e) => setEnroll(e.target.checked)}>
          Enrol in sequences after promoting
        </Checkbox>
        <div style={{ flex: 1 }} />
        {canEdit && (
          <Button
            type="primary"
            icon={<SendOutlined />}
            disabled={!selected.length}
            loading={promote.isPending}
            onClick={() => promote.mutate(selected)}
          >
            Promote {selected.length || ""}
          </Button>
        )}
      </div>
      <Table<CompanyPick>
        rowKey="id"
        size="small"
        loading={companies.isFetching}
        dataSource={companies.data?.items}
        scroll={{ x: 800 }}
        locale={{ emptyText: "No companies match these filters" }}
        rowSelection={canEdit ? { selectedRowKeys: selected, onChange: (k) => setSelected(k as number[]) } : undefined}
        pagination={{
          current: page,
          pageSize: 20,
          total: companies.data?.total,
          showTotal: (t) => `${t.toLocaleString()} companies`,
          onChange: (p) => setPage(p),
        }}
        columns={[
          {
            title: "Company",
            render: (_, c) => (
              <div style={{ lineHeight: 1.3 }}>
                <div style={{ fontWeight: 600 }}>{c.name}</div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{c.domain ?? "No website"}</Typography.Text>
              </div>
            ),
          },
          { title: "Segment", dataIndex: "segment", width: 130, render: (v) => v ?? "—" },
          { title: "Country", dataIndex: "country", width: 90, render: (v) => v ?? "—" },
          { title: "Status", dataIndex: "status", width: 120, render: (v) => <Tag bordered={false}>{v}</Tag> },
        ]}
      />
    </Card>
  );
}

// ---- page ------------------------------------------------------------------------------------
/**
 * The outreach engine is an optional, separately installed package. Without one every outreach
 * endpoint answers 503, so the console is not mounted at all: the page explains what is missing
 * instead of firing requests that can only fail.
 */
export function Outreach() {
  return OUTREACH_ENABLED ? <OutreachConsole /> : <OutreachNotConnected />;
}

function OutreachNotConnected() {
  return (
    <>
      <PageHeader title="Outreach" subtitle="Promote companies, approve drafts and log LinkedIn work" />
      <EmptyState
        title="No outreach engine is connected"
        hint={
          <>
            Discovery, companies, people, tasks and the plan all work without one. To send sequences with human
            approval from here, install an outreach engine and set <Typography.Text code>PROSPECTOR_ENGINE_ENABLED</Typography.Text>{" "}
            and <Typography.Text code>PROSPECTOR_ENGINE_CONFIG</Typography.Text> (see docs/DEPLOYMENT.md, section 8).
          </>
        }
      />
    </>
  );
}

function OutreachConsole() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit } = useMe();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "approvals";
  const summary = useQuery({
    queryKey: ["outreach-summary"],
    queryFn: () => api<Summary>("/api/outreach/summary"),
    refetchInterval: REFRESH.dashboard,
  });

  const tick = useMutation({
    mutationFn: () => post<TickResult>("/api/outreach/tick"),
    onSuccess: (r) => {
      message.success(`Ran ${r.steps} due step(s) · ${r.enrolled} lead(s) enrolled`);
      qc.invalidateQueries({ queryKey: ["outreach-summary"] });
      qc.invalidateQueries({ queryKey: ["outreach-drafts"] });
      qc.invalidateQueries({ queryKey: ["outreach-linkedin"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (e: Error) => message.error(e.message),
  });

  const s = summary.data;
  const leadChips = Object.entries(s?.leads_by_status ?? {}).map(([k, v]) => (
    <Tag key={k} bordered={false}>
      {(LEAD_STATUS[k]?.text ?? k).toLowerCase()}: {v}
    </Tag>
  ));

  return (
    <>
      <PageHeader
        title="Outreach"
        subtitle="Promote companies, approve drafts and log LinkedIn work"
        extra={
          canEdit && (
            <Popconfirm
              title="Run due steps now?"
              description="Runs the steps that are due, including approved messages in their send window. Drafts still need approval."
              onConfirm={() => tick.mutate()}
            >
              <Button icon={<ThunderboltOutlined />} loading={tick.isPending}>
                Run due steps
              </Button>
            </Popconfirm>
          )
        }
      />
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {summary.isError && <Alert type="warning" showIcon message="Outreach engine unavailable" description={summary.error.message} />}
        <Alert
          type="info"
          showIcon
          message="Human approval before anything is sent"
          description="First-touch drafts wait in the approval queue until a teammate approves them. Region rules, suppression, postal address and dry-run mode are enforced by the engine, not by this screen."
        />
        {s?.paused && <Alert type="warning" showIcon message="Sequences are paused: no steps will run until they are resumed." />}
        {s?.dry_run && <Alert type="warning" showIcon message="Test mode (dry run): messages are written out but never actually sent." />}
        {s && !s.postal_address_ok && (
          <Alert type="warning" showIcon message="The sender postal address is not configured, so the engine will not send email." />
        )}
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Drafts awaiting approval" value={s?.pending_drafts ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="LinkedIn steps due" value={s?.linkedin_due ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Emails sent" value={s?.emails_sent ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Mailboxes" value={s?.mailboxes ?? 0} />
            </Card>
          </Col>
        </Row>
        <Card size="small" title="Leads by status">
          {leadChips.length ? <Space size={6} wrap>{leadChips}</Space> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No leads in the engine yet" />}
        </Card>
        <Tabs
          activeKey={tab}
          onChange={(t) => setParams({ tab: t })}
          items={[
            { key: "approvals", label: `Approvals${s ? ` (${s.pending_drafts})` : ""}`, children: <ApprovalsTab /> },
            { key: "linkedin", label: `LinkedIn queue${s ? ` (${s.linkedin_due})` : ""}`, children: <LinkedinTab /> },
            { key: "leads", label: "Leads", children: <LeadsTab /> },
            { key: "promote", label: "Promote", children: <PromoteTab /> },
          ]}
        />
      </Space>
    </>
  );
}
