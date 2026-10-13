import { CheckOutlined, CloseOutlined, DownloadOutlined, FileWordOutlined, LinkedinFilled, MailOutlined, PhoneOutlined, ScheduleOutlined, TeamOutlined } from "@ant-design/icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Result,
  Row,
  Select,
  Skeleton,
  Slider,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, patch, post, type Me } from "../api";
import { EmptyState } from "../components/EmptyState";
import { rowActivation } from "../components/SafeLink";
import { qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import { KIND_OPTIONS, PRIORITY_OPTIONS } from "./Tasks";

export type CompanyRow = {
  id: number;
  name: string;
  domain: string | null;
  website: string | null;
  country: string | null;
  city: string | null;
  segment: string | null;
  phone: string | null;
  general_email: string | null;
  linkedin_url: string | null;
  quality_score: number | null;
  status: "new" | "ready" | "rejected" | "in_outreach";
  people: number;
  lei: string | null;
  registry_status: string | null;
  last_crawled_at: string | null;
  updated_at: string | null;
};
type Facet = { value: string; count: number };
type Facets = { segment: Facet[]; country: Facet[]; status: Facet[]; total: number; people: number };
type SourceRow = { field: string; value: string; source: string; url: string | null; retrieved_at: string; confidence: number };
type PersonLite = {
  id: number;
  name: string;
  title: string | null;
  seniority: string | null;
  email: string | null;
  email_status: string | null;
  phone: string | null;
  linkedin_url: string | null;
};
type CompanyDetail = CompanyRow & {
  address: string | null;
  company_number: string | null;
  size_band: string | null;
  people_list: PersonLite[];
  signals: { kind: string; title: string; url: string | null; on: string | null; source: string }[];
  sources: SourceRow[];
  checks: { check: string; label: string; passed: boolean; detail: string | null }[];
  notes_count: number;
};
type Note = {
  id: number;
  body: string;
  user_id: number | null;
  user: string | null;
  created_at: string | null;
  updated_at: string | null;
};
type CompanyTask = {
  id: number;
  title: string;
  kind: string;
  status: string;
  priority: string;
  due_on: string | null;
  overdue: boolean;
  assignee: string | null;
  person: string | null;
  completed_at: string | null;
};

// Exports stream a file, not JSON: fetch the bytes, then hand the browser a download.
async function download(path: string): Promise<void> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "X-Requested-With": "prospector" },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] || "export";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

export const STATUS_LABEL: Record<CompanyRow["status"], { text: string; color: string }> = {
  new: { text: "New", color: "default" },
  ready: { text: "Ready", color: "green" },
  rejected: { text: "Rejected", color: "red" },
  in_outreach: { text: "In outreach", color: "blue" },
};
export const SOURCE_LABEL: Record<string, string> = {
  "seed-sheet": "Target sheet",
  osm: "OpenStreetMap",
  gleif: "GLEIF",
  website: "Company website",
};
export const SENIORITY_LABEL: Record<string, string> = {
  owner: "Owner / founder",
  c_level: "C-level",
  partner: "Partner",
  director: "Director / head",
  manager: "Manager",
  staff: "Staff",
};

/**
 * Quality score as a compact badge.
 *
 * A 22px circular `Progress` was nearly unreadable — at that diameter the ring is a
 * few pixels of arc and every score looks identical. A short horizontal bar with the
 * number beside it conveys the same information and stays legible in a dense table.
 */
export function ScoreBadge({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return (
      <Typography.Text type="secondary" aria-label="Not scored">
        —
      </Typography.Text>
    );
  }
  const color = score >= 80 ? "#14995b" : score >= 60 ? "#d48806" : "#d4380d";
  const band = score >= 80 ? "high" : score >= 60 ? "medium" : "low";
  return (
    <Space size={8} align="center">
      <Progress
        percent={score}
        showInfo={false}
        strokeColor={color}
        size="small"
        style={{ width: 44, margin: 0 }}
      />
      <span className="num" style={{ minWidth: 22, textAlign: "right" }} aria-label={`Quality ${score}, ${band}`}>
        {score}
      </span>
    </Space>
  );
}

function SourceHint({ sources, field, children }: { sources: SourceRow[]; field: string; children: React.ReactNode }) {
  const rows = sources.filter((s) => s.field === field);
  if (!rows.length) return <>{children}</>;
  return (
    <Tooltip
      title={
        <div>
          {rows.map((r, i) => (
            <div key={i}>
              {SOURCE_LABEL[r.source] ?? r.source} · {r.retrieved_at.slice(0, 10)}
              {r.value !== rows[0].value ? ` · “${r.value}”` : ""}
            </div>
          ))}
        </div>
      }
    >
      <span style={{ borderBottom: "1px dotted #9aa5b8", cursor: "help" }}>{children}</span>
    </Tooltip>
  );
}

function CompanyDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { me, canEdit, isAdmin } = useMe();
  const [note, setNote] = useState("");
  const [editingNote, setEditingNote] = useState<number | null>(null);
  const [editBody, setEditBody] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["company", id],
    queryFn: () => api<CompanyDetail>(`/api/companies/${id}`),
    enabled: id !== null,
    refetchInterval: REFRESH.lists,
  });
  const notes = useQuery({
    queryKey: ["company-notes", id],
    queryFn: () => api<Note[]>(`/api/companies/${id}/notes`),
    enabled: id !== null,
    refetchInterval: REFRESH.lists,
  });
  const tasks = useQuery({
    queryKey: ["company-tasks", id],
    queryFn: () => api<CompanyTask[]>(`/api/companies/${id}/tasks`),
    enabled: id !== null,
    refetchInterval: REFRESH.lists,
  });
  const setStatus = useMutation({
    mutationFn: (status: string) => patch(`/api/companies/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["company", id] });
      qc.invalidateQueries({ queryKey: ["companies"] });
    },
    onError: (e: Error) => message.error(e.message),
  });
  const refreshNotes = () => {
    qc.invalidateQueries({ queryKey: ["company-notes", id] });
    qc.invalidateQueries({ queryKey: ["company", id] });
  };
  const addNote = useMutation({
    mutationFn: (body: string) => post<Note>(`/api/companies/${id}/notes`, { body }),
    onSuccess: () => {
      setNote("");
      refreshNotes();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const saveNote = useMutation({
    mutationFn: ({ noteId, body }: { noteId: number; body: string }) => patch<Note>(`/api/notes/${noteId}`, { body }),
    onSuccess: () => {
      setEditingNote(null);
      refreshNotes();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const deleteNote = useMutation({
    mutationFn: (noteId: number) => api<{ deleted: number }>(`/api/notes/${noteId}`, { method: "DELETE" }),
    onSuccess: () => {
      message.success("Note deleted");
      refreshNotes();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const enrich = useMutation({
    mutationFn: () => post<{ queued: number; runs: number[]; skipped: number }>(`/api/companies/${id}/enrich`),
    onSuccess: (r) => {
      message.success(
        r.skipped ? `${r.queued} queued for a crawl · ${r.skipped} skipped without a website` : `${r.queued} queued for a fresh crawl`,
      );
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["engine"] });
    },
    onError: (e: Error) => message.error(e.message),
  });
  const profile = useMutation({
    mutationFn: () => download(`/api/companies/${id}/profile.docx`),
    onError: (e: Error) => message.error(e.message),
  });
  const s = data?.sources ?? [];
  return (
    <Drawer
      open={id !== null}
      onClose={onClose}
      width={720}
      loading={isLoading}
      title={
        data && (
          <Space direction="vertical" size={0}>
            <Space>
              {data.name}
              <Tag color={STATUS_LABEL[data.status].color}>{STATUS_LABEL[data.status].text}</Tag>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {[data.segment, [data.city, data.country].filter(Boolean).join(", ")].filter(Boolean).join(" · ")}
            </Typography.Text>
          </Space>
        )
      }
      extra={
        data &&
        canEdit &&
        data.status !== "in_outreach" && (
          <Space>
            <Button icon={<CloseOutlined />} onClick={() => setStatus.mutate("rejected")} disabled={data.status === "rejected"}>
              Reject
            </Button>
            <Button type="primary" icon={<CheckOutlined />} onClick={() => setStatus.mutate("ready")} disabled={data.status === "ready"}>
              Mark ready
            </Button>
          </Space>
        )
      }
    >
      {data && (
        <Tabs
          items={[
            {
              key: "overview",
              label: "Overview",
              children: (
                <Space direction="vertical" size="large" style={{ width: "100%" }}>
                  <Space wrap>
                    {canEdit && (
                      <Button icon={<DownloadOutlined />} loading={enrich.isPending} onClick={() => enrich.mutate()}>
                        Enrich
                      </Button>
                    )}
                    {canEdit && (
                      <Button icon={<FileWordOutlined />} loading={profile.isPending} onClick={() => profile.mutate()}>
                        Word profile
                      </Button>
                    )}
                    <Link to={`/tasks?company_id=${data.id}`}>Tasks for this company</Link>
                  </Space>
                  <Descriptions column={2} size="small" bordered>
                    <Descriptions.Item label="Quality">
                      <ScoreBadge score={data.quality_score} />
                    </Descriptions.Item>
                    <Descriptions.Item label="People">{data.people}</Descriptions.Item>
                    <Descriptions.Item label="Website" span={2}>
                      {data.website ? (
                        <SourceHint sources={s} field="website">
                          <a href={data.website} target="_blank" rel="noreferrer noopener">
                            {data.domain}
                          </a>
                        </SourceHint>
                      ) : (
                        "—"
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="Phone">
                      {data.phone ? <SourceHint sources={s} field="phone">{data.phone}</SourceHint> : "—"}
                    </Descriptions.Item>
                    <Descriptions.Item label="Email">
                      {data.general_email ? (
                        <SourceHint sources={s} field="general_email">{data.general_email}</SourceHint>
                      ) : (
                        "—"
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="LinkedIn" span={2}>
                      {data.linkedin_url ? (
                        <a href={data.linkedin_url} target="_blank" rel="noreferrer noopener">
                          Company page
                        </a>
                      ) : (
                        "—"
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="Address" span={2}>
                      {data.address ? <SourceHint sources={s} field="address">{data.address}</SourceHint> : "—"}
                    </Descriptions.Item>
                    <Descriptions.Item label="LEI">{data.lei ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="Registry status">{data.registry_status ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="Size">{data.size_band ?? "—"}</Descriptions.Item>
                    <Descriptions.Item label="Website crawled">
                      {data.last_crawled_at ? data.last_crawled_at.slice(0, 10) : "Not yet"}
                    </Descriptions.Item>
                  </Descriptions>
                </Space>
              ),
            },
            {
              key: "people",
              label: `People (${data.people_list.length})`,
              children: data.people_list.length ? (
                <Table<PersonLite>
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 25, hideOnSinglePage: true }}
                  dataSource={data.people_list}
                  columns={[
                    { title: "Name", dataIndex: "name" },
                    { title: "Title", dataIndex: "title", ellipsis: true },
                    { title: "Seniority", render: (_, p) => (p.seniority ? SENIORITY_LABEL[p.seniority] : "—") },
                    { title: "Email", render: (_, p) => p.email ?? "—" },
                    { title: "Phone", dataIndex: "phone", width: 140, render: (v) => v ?? "—" },
                    {
                      title: "LinkedIn",
                      dataIndex: "linkedin_url",
                      width: 84,
                      render: (v) =>
                        v ? (
                          <a href={v} target="_blank" rel="noreferrer noopener">
                            Profile
                          </a>
                        ) : (
                          "—"
                        ),
                    },
                  ]}
                />
              ) : (
                <Empty description="No named people yet. Crawl the website from Discovery." />
              ),
            },
            {
              key: "notes",
              label: `Notes (${data.notes_count})`,
              children: (
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  {canEdit && (
                    <div>
                      <Input.TextArea
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        autoSize={{ minRows: 2, maxRows: 6 }}
                        maxLength={5000}
                        placeholder="Add a note for the team"
                      />
                      <Button
                        style={{ marginTop: 8 }}
                        type="primary"
                        size="small"
                        disabled={!note.trim()}
                        loading={addNote.isPending}
                        onClick={() => addNote.mutate(note.trim())}
                      >
                        Add note
                      </Button>
                    </div>
                  )}
                  {notes.isLoading ? (
                    <Skeleton active paragraph={{ rows: 2 }} />
                  ) : notes.isError ? (
                    <Alert type="error" showIcon message={notes.error.message} />
                  ) : notes.data?.length ? (
                    <List
                      size="small"
                      dataSource={notes.data}
                      renderItem={(n) => (
                        <List.Item
                          actions={
                            canEdit && (isAdmin || n.user_id === me?.id)
                              ? [
                                  <Button
                                    key="edit"
                                    size="small"
                                    type="text"
                                    onClick={() => {
                                      setEditingNote(n.id);
                                      setEditBody(n.body);
                                    }}
                                  >
                                    Edit
                                  </Button>,
                                  <Popconfirm key="delete" title="Delete this note?" onConfirm={() => deleteNote.mutate(n.id)}>
                                    <Button size="small" type="text" danger>
                                      Delete
                                    </Button>
                                  </Popconfirm>,
                                ]
                              : undefined
                          }
                        >
                          {editingNote === n.id ? (
                            <Space direction="vertical" style={{ width: "100%" }}>
                              <Input.TextArea
                                value={editBody}
                                onChange={(e) => setEditBody(e.target.value)}
                                autoSize={{ minRows: 2, maxRows: 6 }}
                                maxLength={5000}
                              />
                              <Space>
                                <Button
                                  size="small"
                                  type="primary"
                                  loading={saveNote.isPending}
                                  onClick={() => saveNote.mutate({ noteId: n.id, body: editBody.trim() })}
                                >
                                  Save
                                </Button>
                                <Button size="small" onClick={() => setEditingNote(null)}>
                                  Cancel
                                </Button>
                              </Space>
                            </Space>
                          ) : (
                            <List.Item.Meta
                              title={
                                <Typography.Text style={{ whiteSpace: "pre-wrap" }}>{n.body}</Typography.Text>
                              }
                              description={`${n.user ?? "Unknown"} · ${(n.updated_at ?? n.created_at ?? "").slice(0, 16).replace("T", " ")}`}
                            />
                          )}
                        </List.Item>
                      )}
                    />
                  ) : (
                    <Empty description="No notes yet" />
                  )}
                </Space>
              ),
            },
            {
              key: "signals",
              label: `Signals (${data.signals.length})`,
              children: data.signals.length ? (
                <List
                  size="small"
                  dataSource={data.signals}
                  renderItem={(g) => (
                    <List.Item>
                      <List.Item.Meta
                        title={g.url ? <a href={g.url} target="_blank" rel="noreferrer noopener">{g.title}</a> : g.title}
                        description={`${g.kind} · ${SOURCE_LABEL[g.source] ?? g.source}`}
                      />
                    </List.Item>
                  )}
                />
              ) : (
                <Empty description="No signals recorded" />
              ),
            },
            {
              key: "sources",
              label: `Sources (${s.length})`,
              children: (
                <Table<SourceRow>
                  rowKey={(r) => `${r.field}|${r.source}|${r.value}`}
                  size="small"
                  pagination={{ pageSize: 30, hideOnSinglePage: true }}
                  dataSource={s}
                  columns={[
                    { title: "Field", dataIndex: "field", width: 120 },
                    { title: "Value", dataIndex: "value", ellipsis: true },
                    { title: "Source", render: (_, r) => <Tag bordered={false}>{SOURCE_LABEL[r.source] ?? r.source}</Tag> },
                    {
                      title: "Evidence",
                      render: (_, r) =>
                        r.url ? (
                          <a href={r.url} target="_blank" rel="noreferrer noopener">
                            Open
                          </a>
                        ) : (
                          "—"
                        ),
                    },
                    { title: "Retrieved", render: (_, r) => r.retrieved_at.slice(0, 10), width: 100 },
                  ]}
                />
              ),
            },
            {
              key: "quality",
              label: "Quality checks",
              children: (
                <List
                  size="small"
                  dataSource={data.checks}
                  renderItem={(c) => (
                    <List.Item>
                      <Space>
                        {c.passed ? <CheckOutlined style={{ color: "#14995b" }} /> : <CloseOutlined style={{ color: "#d4380d" }} />}
                        <span>{c.label}</span>
                      </Space>
                      <Typography.Text type="secondary">{c.detail}</Typography.Text>
                    </List.Item>
                  )}
                />
              ),
            },
            {
              key: "tasks",
              label: "Tasks",
              children: tasks.isLoading ? (
                <Skeleton active paragraph={{ rows: 3 }} />
              ) : tasks.isError ? (
                <Alert type="error" showIcon message={tasks.error.message} />
              ) : tasks.data?.length ? (
                <Table<CompanyTask>
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  dataSource={tasks.data}
                  locale={{ emptyText: "No tasks for this company" }}
                  columns={[
                    {
                      title: "Task",
                      render: (_, t) => (
                        <div style={{ lineHeight: 1.3 }}>
                          <div style={{ fontWeight: 600 }}>{t.title}</div>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {[t.kind, t.person].filter(Boolean).join(" · ")}
                          </Typography.Text>
                        </div>
                      ),
                    },
                    {
                      title: "Assignee",
                      render: (_, t) => t.assignee ?? <Typography.Text type="secondary">Unassigned</Typography.Text>,
                    },
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
                    {
                      title: "Status",
                      width: 130,
                      render: (_, t) => <Tag bordered={false}>{t.status.replace("_", " ")}</Tag>,
                    },
                  ]}
                />
              ) : (
                <Empty description="No tasks for this company" />
              ),
            },
          ]}
        />
      )}
    </Drawer>
  );
}

type Filters = {
  q: string;
  segment: string[];
  country: string[];
  status: string[];
  min_quality: number;
  has_email: boolean;
  has_phone: boolean;
  has_people: boolean;
};
const EMPTY: Filters = { q: "", segment: [], country: [], status: [], min_quality: 0, has_email: false, has_phone: false, has_people: false };

function toQuery(f: Filters, page: number, pageSize: number, sort: string): string {
  const p = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort });
  if (f.q) p.set("q", f.q);
  f.segment.forEach((v) => p.append("segment", v));
  f.country.forEach((v) => p.append("country", v));
  f.status.forEach((v) => p.append("status", v));
  if (f.min_quality) p.set("min_quality", String(f.min_quality));
  if (f.has_email) p.set("has_email", "true");
  if (f.has_phone) p.set("has_phone", "true");
  if (f.has_people) p.set("has_people", "true");
  return p.toString();
}

export function Companies() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit } = useMe();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState("quality");
  const [selected, setSelected] = useState<number[]>([]);
  const openRaw = params.get("open");
  const openId = openRaw && /^\d+$/.test(openRaw) ? Number(openRaw) : null;
  const facets = useQuery({
    queryKey: qk.companyFacets(),
    queryFn: () => api<Facets>("/api/companies/facets"),
    refetchInterval: REFRESH.lists,
  });
  const query = toQuery(filters, page, pageSize, sort);
  const list = useQuery({
    queryKey: qk.companies(query),
    queryFn: () => api<{ items: CompanyRow[]; total: number }>(`/api/companies?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH.lists,
  });
  const bulk = useMutation({
    mutationFn: (status: string) => post<{ updated: number }>("/api/companies/bulk", { ids: selected, status }),
    onSuccess: (r) => {
      message.success(`${r.updated} updated`);
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["companies"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
    },
    onError: (e: Error) => message.error(e.message),
  });
  const [taskOpen, setTaskOpen] = useState(false);
  const [taskForm] = Form.useForm();
  const taskUsers = useQuery({
    queryKey: ["users"],
    queryFn: () => api<Me[]>("/api/users"),
    enabled: canEdit && taskOpen,
    refetchInterval: REFRESH.background,
  });
  const bulkTask = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      post<{ created: number }>("/api/tasks/bulk", { ...body, company_ids: selected }),
    onSuccess: (r) => {
      message.success(`${r.created} tasks created`);
      setTaskOpen(false);
      taskForm.resetFields();
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["tasks-summary"] });
    },
    onError: (e: Error) => message.error(e.message),
  });
  const set = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    // A new filter invalidates the current selection: those rows may not be in the
    // result set any more, and the bulk bar would act on invisible rows.
    setSelected([]);
    setPage(1);
  };

  // Clamp the page when a filter shrinks the result set, otherwise the table renders
  // empty on a page number that no longer exists.
  const total = list.data?.total ?? 0;
  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, maxPage);
  if (safePage !== page) setPage(safePage);
  const exportFile = useMutation({
    mutationFn: (path: string) => download(path),
    onError: (e: Error) => message.error(e.message),
  });
  const companiesExport = (format: "csv" | "xlsx") => {
    /*
      Either export what is on screen or export the selection — never both.
      Appending `ids` to a query that already carries the same filters produced each
      selected company twice in the file.
    */
    if (selected.length > 0) {
      const p = new URLSearchParams({ format });
      selected.forEach((id) => p.append("ids", String(id)));
      return `/api/export/companies?${p}`;
    }
    const p = new URLSearchParams(toQuery(filters, safePage, pageSize, sort));
    p.set("format", format);
    return `/api/export/companies?${p}`;
  };
  const selectedLabel = selected.length ? ` · ${selected.length} selected` : "";
  const exportItems = [
    {
      key: "companies-csv",
      label: `Companies (CSV)${selectedLabel}`,
      onClick: () => exportFile.mutate(companiesExport("csv")),
    },
    {
      key: "companies-xlsx",
      label: `Companies (Excel)${selectedLabel}`,
      onClick: () => exportFile.mutate(companiesExport("xlsx")),
    },
    { key: "people-csv", label: "People (CSV)", onClick: () => exportFile.mutate("/api/export/people?format=csv") },
    { key: "people-xlsx", label: "People (Excel)", onClick: () => exportFile.mutate("/api/export/people?format=xlsx") },
  ];
  const facetOpts = (rows: Facet[] | undefined, label?: (v: string) => string) =>
    (rows || []).map((r) => ({ value: r.value, label: `${label ? label(r.value) : r.value} (${r.count})` }));
  const activeCount = useMemo(
    () =>
      [filters.segment.length, filters.country.length, filters.status.length, filters.min_quality, filters.has_email, filters.has_phone, filters.has_people].filter(Boolean)
        .length,
    [filters],
  );

  /*
    `useSearchParams` returns a live object. The previous code mutated it and passed the
    same instance back, which react-router v7 treats as "no change" — so closing a
    drawer did not clear `?open=` and the drawer reopened on the next render. Build a
    fresh URLSearchParams inside a functional update instead.
  */
  const openRow = (id: number) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("open", String(id));
        return next;
      },
      { replace: false },
    );

  const closeRow = () =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("open");
        return next;
      },
      { replace: true },
    );

  return (
    <>
      <PageHeader
        title="Companies"
        subtitle={facets.data ? `${facets.data.total.toLocaleString()} companies · ${facets.data.people.toLocaleString()} people` : undefined}
        extra={
          <Space>
            {canEdit && (
              <Dropdown menu={{ items: exportItems }}>
                <Button icon={<DownloadOutlined />} loading={exportFile.isPending}>
                  Export
                </Button>
              </Dropdown>
            )}
            <Typography.Text type="secondary">Sort</Typography.Text>
            <Select
              value={sort}
              onChange={setSort}
              style={{ width: 150 }}
              options={[
                { value: "quality", label: "Quality score" },
                { value: "updated", label: "Recently updated" },
                { value: "newest", label: "Newest" },
                { value: "name", label: "Name" },
              ]}
            />
          </Space>
        }
      />
      <Row gutter={[16, 16]} wrap>
        {/*
          The rail is a full-width block below the `lg` breakpoint and a fixed 248px
          column above it. Previously `<Row wrap={false}>` forced the 248px rail at every
          width, so the table was crushed on a phone.
        */}
        <Col xs={24} xl={248} className="filter-rail">
          <Card
            size="small"
            title="Filters"
            extra={
              activeCount ? (
                <Button type="link" size="small" onClick={() => set(EMPTY)} aria-label="Clear all filters">
                  Clear ({activeCount})
                </Button>
              ) : null
            }
          >
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <div>
                <label className="filter-label" htmlFor="companies-search">
                  Search
                </label>
                <Input.Search
                  id="companies-search"
                  placeholder="Name or domain"
                  allowClear
                  onSearch={(q) => set({ q })}
                />
              </div>
              <div>
                <label className="filter-label" htmlFor="companies-segment">
                  Segment
                </label>
                <Select
                  id="companies-segment"
                  mode="multiple"
                  allowClear
                  style={{ width: "100%" }}
                  placeholder="Any"
                  value={filters.segment}
                  options={facetOpts(facets.data?.segment)}
                  onChange={(segment) => set({ segment })}
                  maxTagCount="responsive"
                />
              </div>
              <div>
                <label className="filter-label" htmlFor="companies-country">
                  Country
                </label>
                <Select
                  id="companies-country"
                  mode="multiple"
                  allowClear
                  style={{ width: "100%" }}
                  placeholder="Any"
                  value={filters.country}
                  options={facetOpts(facets.data?.country)}
                  onChange={(country) => set({ country })}
                  maxTagCount="responsive"
                />
              </div>
              <div>
                <label className="filter-label" htmlFor="companies-status">
                  Status
                </label>
                <Select
                  id="companies-status"
                  mode="multiple"
                  allowClear
                  style={{ width: "100%" }}
                  placeholder="Any"
                  value={filters.status}
                  options={facetOpts(facets.data?.status, (v) => STATUS_LABEL[v as CompanyRow["status"]]?.text ?? v)}
                  onChange={(status) => set({ status })}
                  maxTagCount="responsive"
                />
              </div>
              <div>
                <label className="filter-label" htmlFor="companies-quality">
                  Minimum quality: {filters.min_quality || "any"}
                </label>
                <Slider
                  id="companies-quality"
                  min={0}
                  max={100}
                  step={10}
                  value={filters.min_quality}
                  // onChangeComplete so a drag does not fire a request per pixel.
                  onChangeComplete={(v) => set({ min_quality: v as number })}
                />
              </div>
              <Space direction="vertical" size={4}>
                <Checkbox checked={filters.has_email} onChange={(e) => set({ has_email: e.target.checked })}>
                  Has email
                </Checkbox>
                <Checkbox checked={filters.has_phone} onChange={(e) => set({ has_phone: e.target.checked })}>
                  Has phone
                </Checkbox>
                <Checkbox checked={filters.has_people} onChange={(e) => set({ has_people: e.target.checked })}>
                  Has named people
                </Checkbox>
              </Space>
            </Space>
          </Card>
        </Col>
        <Col flex="auto" style={{ minWidth: 0 }}>
          <Card size="small" styles={{ body: { padding: 0 } }}>
            {/*
              When the list query fails, say so. `dataSource={list.data?.items}` alone
              rendered an empty table indistinguishable from "no companies match",
              which reads as a data problem rather than a connection problem.
            */}
            {list.isError ? (
              <Result
                status="error"
                title="Could not load companies"
                subTitle={list.error?.message ?? "The server did not respond as expected."}
                extra={
                  <Button type="primary" onClick={() => void list.refetch()} loading={list.isFetching}>
                    Retry
                  </Button>
                }
              />
            ) : (
              <>
            {selected.length > 0 && canEdit && (
              <div className="bulk-bar">
                <span>{selected.length} selected</span>
                <Space wrap>
                  <Button
                    size="small"
                    onClick={() => bulk.mutate("ready")}
                    loading={bulk.isPending && bulk.variables === "ready"}
                  >
                    Mark ready
                  </Button>
                  {/* Rejecting is not reversible from this screen, so confirm first. */}
                  <Popconfirm
                    title={`Reject ${selected.length} compan${selected.length === 1 ? "y" : "ies"}?`}
                    description="They will stop appearing in outreach. You can change the status again later."
                    okText="Reject"
                    onConfirm={() => bulk.mutate("rejected")}
                  >
                    <Button size="small" loading={bulk.isPending && bulk.variables === "rejected"}>
                      Reject
                    </Button>
                  </Popconfirm>
                  <Button size="small" icon={<ScheduleOutlined />} onClick={() => setTaskOpen(true)}>
                    Create task
                  </Button>
                  <Button size="small" type="text" onClick={() => setSelected([])}>
                    Clear
                  </Button>
                </Space>
              </div>
            )}
            <Table<CompanyRow>
              rowKey="id"
              size="small"
              loading={list.isFetching}
              dataSource={list.data?.items}
              scroll={{ x: 900 }}
              rowSelection={
                canEdit
                  ? {
                      selectedRowKeys: selected,
                      onChange: (k) => setSelected(k as number[]),
                      // Changing page changes which rows exist; drop the selection so the
                      // bulk bar can never act on rows the user cannot see.
                      preserveSelectedRowKeys: false,
                    }
                  : undefined
              }
              onRow={(r) => ({
                ...rowActivation(() => openRow(r.id), `Open ${r.name}`),
                className: "row-clickable",
              })}
              locale={{
                emptyText: (
                  <EmptyState
                    compact
                    title={activeCount ? "No companies match these filters" : "No companies yet"}
                    hint={
                      activeCount
                        ? "Try widening the filters."
                        : "Companies appear once a discovery source or an import adds them."
                    }
                    action={activeCount ? { label: "Clear filters", onClick: () => set(EMPTY) } : undefined}
                  />
                ),
              }}
              pagination={{
                current: safePage,
                pageSize,
                total,
                showSizeChanger: true,
                pageSizeOptions: [25, 50, 100, 200],
                showTotal: (t, range) => `${range[0]}–${range[1]} of ${t.toLocaleString()}`,
                onChange: (p, ps) => {
                  setPage(p);
                  setPageSize(ps);
                  setSelected([]);
                },
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
                { title: "Segment", dataIndex: "segment", width: 120, render: (v) => v ?? "—" },
                { title: "Location", width: 150, render: (_, c) => [c.city, c.country].filter(Boolean).join(", ") || "—" },
                { title: "Quality", width: 90, render: (_, c) => <ScoreBadge score={c.quality_score} /> },
                {
                  title: "Contact data",
                  width: 140,
                  render: (_, c) => (
                    <Space size={10} style={{ color: "#5b6577" }}>
                      <Tooltip title={`${c.people} named people`}>
                        <span><UserCount n={c.people} /></span>
                      </Tooltip>
                      <Tooltip title={c.general_email ?? "No email"}>
                        <MailOutlined style={{ opacity: c.general_email ? 1 : 0.25 }} />
                      </Tooltip>
                      <Tooltip title={c.phone ?? "No phone"}>
                        <PhoneOutlined style={{ opacity: c.phone ? 1 : 0.25 }} />
                      </Tooltip>
                      <Tooltip title={c.linkedin_url ? "LinkedIn company page" : "No LinkedIn page"}>
                        <LinkedinFilled style={{ opacity: c.linkedin_url ? 1 : 0.25 }} />
                      </Tooltip>
                    </Space>
                  ),
                },
                {
                  title: "Status",
                  width: 110,
                  render: (_, c) => {
                    const label = STATUS_LABEL[c.status] ?? { color: "default", text: c.status ?? "—" };
                    return <Tag color={label.color}>{label.text}</Tag>;
                  },
                },
              ]}
            />
              </>
            )}
          </Card>
        </Col>
      </Row>
      <CompanyDrawer id={openId} onClose={closeRow} />
      <Modal
        title={`Create a task for ${selected.length} selected compan${selected.length === 1 ? "y" : "ies"}`}
        open={taskOpen}
        onCancel={() => setTaskOpen(false)}
        onOk={() => taskForm.submit()}
        confirmLoading={bulkTask.isPending}
        okText="Create tasks"
        destroyOnHidden
      >
        <Form
          form={taskForm}
          layout="vertical"
          initialValues={{ kind: "research", priority: "normal" }}
          onFinish={(v) =>
            bulkTask.mutate({
              title: v.title,
              kind: v.kind,
              priority: v.priority,
              due_on: v.due_on ? v.due_on.format("YYYY-MM-DD") : null,
              assignee_id: v.assignee_id ?? null,
            })
          }
        >
          <Form.Item name="title" label="Title" rules={[{ required: true, max: 300 }]}>
            <Input />
          </Form.Item>
          <Space wrap style={{ width: "100%" }}>
            <Form.Item name="kind" label="Kind">
              <Select style={{ width: 160 }} options={KIND_OPTIONS} />
            </Form.Item>
            <Form.Item name="priority" label="Priority">
              <Select style={{ width: 130 }} options={PRIORITY_OPTIONS} />
            </Form.Item>
            <Form.Item name="due_on" label="Due date">
              <DatePicker />
            </Form.Item>
          </Space>
          <Form.Item name="assignee_id" label="Assignee">
            <Select
              allowClear
              placeholder="Anyone"
              style={{ width: "100%" }}
              options={(taskUsers.data || []).map((u) => ({ value: u.id, label: u.name }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function UserCount({ n }: { n: number }) {
  return (
    <span style={{ fontVariantNumeric: "tabular-nums", opacity: n ? 1 : 0.35 }}>
      <TeamOutlined /> {n}
    </span>
  );
}
