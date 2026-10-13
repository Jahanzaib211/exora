import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, patch, post } from "../api";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";

type TaskStatus = "open" | "in_progress" | "done" | "cancelled";

type Task = {
  id: number;
  title: string;
  kind: string;
  status: TaskStatus;
  priority: "low" | "normal" | "high";
  due_on: string | null;
  overdue: boolean;
  company_id: number | null;
  company: string | null;
  person: string | null;
  assignee_id: number | null;
  assignee: string | null;
  created_by: number | null;
  notes: string | null;
  completed_at: string | null;
  created_at: string;
};

type Summary = {
  mine: number;
  mine_overdue: number;
  mine_today: number;
  unassigned: number;
  team: { user_id: number; name: string; open: number }[];
};

type CompanyLite = { id: number; name: string };

type TaskForm = {
  title: string;
  kind: string;
  priority: string;
  due_on: Dayjs | null;
  assignee_id: number | null;
  company_id: number | null;
  notes: string | null;
};

const PAGE_SIZE = 100;

const KIND_LABEL: Record<string, string> = {
  research: "Research",
  call: "Call",
  email: "Email",
  linkedin: "LinkedIn",
  meeting: "Meeting",
  follow_up: "Follow-up",
  other: "Other",
};
const STATUS_LABEL: Record<TaskStatus, { text: string; color: string }> = {
  open: { text: "Open", color: "default" },
  in_progress: { text: "In progress", color: "processing" },
  done: { text: "Done", color: "success" },
  cancelled: { text: "Cancelled", color: "default" },
};
const PRIORITY_COLOR: Record<Task["priority"], string> = { high: "red", normal: "blue", low: "default" };
const STATUS_OPTIONS = (Object.keys(STATUS_LABEL) as TaskStatus[]).map((v) => ({ value: v, label: STATUS_LABEL[v].text }));
const DUE_OPTIONS = [
  { value: "overdue", label: "Overdue" },
  { value: "today", label: "Due today" },
  { value: "week", label: "Next 7 days" },
  { value: "later", label: "Later" },
  { value: "none", label: "No due date" },
];
export const KIND_OPTIONS = Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }));
export const PRIORITY_OPTIONS = [
  { value: "high", label: "High" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
];

function CompanyPicker({ value, onChange }: { value?: number | null; onChange?: (value?: number) => void }) {
  const [q, setQ] = useState("");
  const hits = useQuery({
    queryKey: ["task-companies", q],
    queryFn: () => api<{ items: CompanyLite[] }>(`/api/companies?page_size=20${q ? `&q=${encodeURIComponent(q)}` : ""}`),
  });
  return (
    <Select
      allowClear
      showSearch
      filterOption={false}
      placeholder="Company (optional)"
      value={value ?? undefined}
      onSearch={setQ}
      onChange={(v) => onChange?.(v)}
      options={(hits.data?.items || []).map((c) => ({ value: c.id, label: c.name }))}
      notFoundContent={hits.isFetching ? "Searching…" : "No companies found"}
      style={{ width: "100%" }}
    />
  );
}

export function Tasks() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { me, canEdit, isAdmin } = useMe();
  const [params, setParams] = useSearchParams();
  const companyId = params.get("company_id") ? Number(params.get("company_id")) : null;
  const [assignee, setAssignee] = useState("me");
  const [statuses, setStatuses] = useState<string[]>([]);
  const [due, setDue] = useState<string | undefined>(undefined);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const [form] = Form.useForm<TaskForm>();

  const summary = useQuery({
    queryKey: ["tasks-summary"],
    queryFn: () => api<Summary>("/api/tasks/summary"),
    refetchInterval: REFRESH.lists,
  });
  const company = useQuery({
    queryKey: ["company-name", companyId],
    enabled: companyId !== null,
    queryFn: () => api<{ name: string }>(`/api/companies/${companyId}`),
  });
  const query = new URLSearchParams({ assignee, page: String(page), page_size: String(pageSize) });
  statuses.forEach((s) => query.append("status", s));
  if (due) query.set("due", due);
  if (q) query.set("q", q);
  if (companyId !== null) query.set("company_id", String(companyId));
  const list = useQuery({
    queryKey: ["tasks", query.toString()],
    queryFn: () => api<{ items: Task[]; total: number }>(`/api/tasks?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH.lists,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["tasks-summary"] });
  };
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<Task>("/api/tasks", body),
    onSuccess: () => {
      message.success("Task created");
      setCreating(false);
      form.resetFields();
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => patch<Task>(`/api/tasks/${id}`, body),
    onSuccess: () => {
      setEditing(null);
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<{ deleted: number }>(`/api/tasks/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      message.success("Task deleted");
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });

  const team = summary.data?.team || [];
  const assigneeOptions = [
    { value: "me", label: "My tasks" },
    { value: "all", label: "Everyone" },
    { value: "unassigned", label: "Unassigned" },
    ...team.filter((t) => t.user_id !== me?.id).map((t) => ({ value: String(t.user_id), label: `${t.name} (${t.open})` })),
  ];
  const peopleOptions = [
    ...(me ? [{ value: me.id, label: me.name }] : []),
    ...team.filter((t) => t.user_id !== me?.id).map((t) => ({ value: t.user_id, label: t.name })),
  ];
  const canChange = (t: Task) => isAdmin || t.created_by === me?.id || t.assignee_id === me?.id;
  const maxOpen = Math.max(1, ...team.map((t) => t.open));

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({
      kind: "research",
      priority: "normal",
      assignee_id: me?.id ?? null,
      company_id: null,
      due_on: null,
      notes: null,
      title: "",
    });
    setCreating(true);
  };
  const openEdit = (t: Task) => {
    form.setFieldsValue({
      title: t.title,
      kind: t.kind,
      priority: t.priority,
      due_on: t.due_on ? dayjs(t.due_on) : null,
      assignee_id: t.assignee_id,
      company_id: t.company_id,
      notes: t.notes,
    });
    setEditing(t);
  };
  const submit = (values: TaskForm) => {
    const body: Record<string, unknown> = {
      title: values.title,
      kind: values.kind,
      priority: values.priority,
      due_on: values.due_on ? values.due_on.format("YYYY-MM-DD") : null,
      assignee_id: values.assignee_id ?? null,
      notes: values.notes || null,
    };
    if (editing) update.mutate({ id: editing.id, body });
    else create.mutate({ ...body, company_id: values.company_id ?? null });
  };

  const resetFilters = () => {
    setAssignee("me");
    setStatuses([]);
    setDue(undefined);
    setQ("");
    setPage(1);
    params.delete("company_id");
    setParams(params);
  };

  if ((summary.isLoading || list.isLoading) && !list.data) return <Skeleton active />;

  return (
    <>
      <PageHeader
        title="Tasks"
        subtitle="Research, calls, LinkedIn and follow-ups assigned to the team"
        extra={
          canEdit && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              New task
            </Button>
          )
        }
      />
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Row gutter={[16, 16]}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="My open tasks" value={summary.data?.mine ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic
                title="Overdue for me"
                value={summary.data?.mine_overdue ?? 0}
                valueStyle={summary.data?.mine_overdue ? { color: "#d4380d" } : undefined}
              />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Due today for me" value={summary.data?.mine_today ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="Unassigned" value={summary.data?.unassigned ?? 0} />
            </Card>
          </Col>
        </Row>

        <Card size="small" title="Team workload">
          {team.length ? (
            team.map((t) => (
              <div key={t.user_id} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
                <span style={{ width: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.name}</span>
                <div style={{ flex: 1, background: "#eef2f7", borderRadius: 4, height: 8 }}>
                  <div style={{ width: `${(t.open / maxOpen) * 100}%`, background: "#2a78d6", height: 8, borderRadius: 4 }} />
                </div>
                <span style={{ width: 48, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{t.open}</span>
              </div>
            ))
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No open tasks assigned yet" />
          )}
        </Card>

        <Card size="small" title="Tasks" styles={{ body: { padding: 0 } }}>
          <div className="table-toolbar">
            <Select
              value={assignee}
              style={{ width: 180 }}
              options={assigneeOptions}
              onChange={(v) => {
                setAssignee(v);
                setPage(1);
              }}
            />
            <Select
              mode="multiple"
              allowClear
              placeholder="Status"
              style={{ minWidth: 170 }}
              value={statuses}
              onChange={(v) => {
                setStatuses(v);
                setPage(1);
              }}
              options={STATUS_OPTIONS}
              maxTagCount="responsive"
            />
            <Select
              allowClear
              placeholder="Due"
              style={{ width: 150 }}
              value={due}
              onChange={(v) => {
                setDue(v);
                setPage(1);
              }}
              options={DUE_OPTIONS}
            />
            <Input.Search
              placeholder="Search title"
              allowClear
              style={{ width: 200 }}
              onSearch={(v) => {
                setQ(v);
                setPage(1);
              }}
            />
            {companyId !== null && (
              <Tag
                closable
                onClose={() => {
                  params.delete("company_id");
                  setParams(params);
                }}
              >
                {company.data?.name ?? `Company ${companyId}`}
              </Tag>
            )}
            <Button size="small" type="text" onClick={resetFilters}>
              Clear filters
            </Button>
          </div>
          {list.isError && <Alert style={{ margin: 12 }} type="error" showIcon message={list.error.message} />}
          <Table<Task>
            rowKey="id"
            size="small"
            loading={list.isFetching}
            dataSource={list.data?.items}
            scroll={{ x: 900 }}
            locale={{ emptyText: "No tasks match these filters" }}
            pagination={{
              current: page,
              pageSize,
              total: list.data?.total,
              showSizeChanger: true,
              pageSizeOptions: [50, 100, 200],
              showTotal: (t) => `${t.toLocaleString()} tasks`,
              onChange: (p, ps) => {
                setPage(p);
                setPageSize(ps);
              },
            }}
            columns={[
              {
                title: "Task",
                render: (_, t) => (
                  <div style={{ lineHeight: 1.3 }}>
                    <div style={{ fontWeight: 600 }}>{t.title}</div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {[KIND_LABEL[t.kind] ?? t.kind, t.person].filter(Boolean).join(" · ")}
                    </Typography.Text>
                  </div>
                ),
              },
              {
                title: "Company",
                render: (_, t) =>
                  t.company && t.company_id ? <Link to={`/companies?open=${t.company_id}`}>{t.company}</Link> : "—",
              },
              { title: "Assignee", render: (_, t) => t.assignee ?? <Typography.Text type="secondary">Unassigned</Typography.Text> },
              {
                title: "Due",
                width: 130,
                render: (_, t) =>
                  t.due_on ? (
                    <Space size={6}>
                      <span>{t.due_on}</span>
                      {t.overdue && <Tag color="red">overdue</Tag>}
                    </Space>
                  ) : (
                    <Typography.Text type="secondary">—</Typography.Text>
                  ),
              },
              { title: "Priority", width: 90, render: (_, t) => <Tag bordered={false} color={PRIORITY_COLOR[t.priority]}>{t.priority}</Tag> },
              {
                title: "Status",
                width: 140,
                render: (_, t) =>
                  canEdit && canChange(t) ? (
                    <Select
                      size="small"
                      style={{ width: 130 }}
                      value={t.status}
                      options={STATUS_OPTIONS}
                      onChange={(status) => update.mutate({ id: t.id, body: { status } })}
                    />
                  ) : (
                    <Tag color={STATUS_LABEL[t.status].color}>{STATUS_LABEL[t.status].text}</Tag>
                  ),
              },
              {
                title: "",
                width: 90,
                render: (_, t) =>
                  canEdit && canChange(t) ? (
                    <Space size={2}>
                      <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(t)} />
                      <Popconfirm title="Delete this task?" onConfirm={() => remove.mutate(t.id)}>
                        <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                      </Popconfirm>
                    </Space>
                  ) : null,
              },
            ]}
          />
        </Card>
      </Space>

      <Modal
        title={editing ? "Edit task" : "New task"}
        open={creating || editing !== null}
        onCancel={() => {
          setCreating(false);
          setEditing(null);
        }}
        onOk={() => form.submit()}
        confirmLoading={create.isPending || update.isPending}
        okText={editing ? "Save" : "Create"}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item name="title" label="Title" rules={[{ required: true, max: 300 }]}>
            <Input />
          </Form.Item>
          <Space wrap style={{ width: "100%" }}>
            <Form.Item name="kind" label="Kind">
              <Select style={{ width: 160 }} options={KIND_OPTIONS} />
            </Form.Item>
            <Form.Item name="priority" label="Priority">
              <Select style={{ width: 140 }} options={PRIORITY_OPTIONS} />
            </Form.Item>
            <Form.Item name="due_on" label="Due date">
              <DatePicker />
            </Form.Item>
          </Space>
          <Form.Item name="assignee_id" label="Assignee">
            <Select allowClear placeholder="Anyone" style={{ width: "100%" }} options={peopleOptions} />
          </Form.Item>
          <Form.Item name="company_id" label="Company">
            <CompanyPicker />
          </Form.Item>
          <Form.Item name="notes" label="Notes">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} maxLength={5000} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
