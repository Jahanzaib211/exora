import { DeleteOutlined, EditOutlined, PlayCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Skeleton,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, patch, post } from "../api";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";
import { TIER_COLOR } from "./Engine";

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
  automation_id: number | null;
  automation: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type Automation = {
  id: number;
  name: string;
  source: string;
  source_name: string;
  params: Record<string, unknown>;
  rotate_field: string | null;
  rotate_values: string[];
  rotate_index: number;
  next_value: string | null;
  cadence_minutes: number;
  enabled: boolean;
  builtin: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run: Run | null;
};

type Param = { name: string; label: string; kind: "text" | "select" | "int"; required: boolean; options: string[] | null; default: unknown; help: string };
type Source = { key: string; name: string; description: string; licence: string; params: Param[] };

type Signal = {
  id: number;
  kind: string;
  title: string;
  url: string | null;
  source: string;
  created_at: string;
  company_id: number;
  company: string;
  tier: string | null;
  fit_score: number | null;
  segment: string | null;
  country: string | null;
};

type Exclusion = { id: number; value: string; label: string; reason: string | null; created_at: string };

const SIGNAL_KIND: Record<string, string> = {
  fit: "Fit note",
  site_mention: "Site mention",
  hiring: "Hiring",
  funding: "Funding",
  news: "News",
};
const RUN_STATUS = { queued: "default", running: "processing", done: "success", failed: "error", cancelled: "default" } as const;

function when(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

// ---- automations ------------------------------------------------------------------------------
function AutomationsTab() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit } = useMe();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Automation | null>(null);
  const [sourceKey, setSourceKey] = useState<string | null>(null);
  const [form] = Form.useForm<Record<string, unknown>>();

  const automations = useQuery({
    queryKey: ["automations"],
    queryFn: () => api<Automation[]>("/api/automations"),
    refetchInterval: REFRESH.lists,
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/api/sources"),
    enabled: open,
    refetchInterval: REFRESH.background,
  });
  const spec = sources.data?.find((s) => s.key === sourceKey);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["automations"] });
    qc.invalidateQueries({ queryKey: ["runs"] });
    qc.invalidateQueries({ queryKey: ["engine"] });
  };
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      editing ? patch<Automation>(`/api/automations/${editing.id}`, body) : post<Automation>("/api/automations", body),
    onSuccess: () => {
      message.success(editing ? "Automation updated" : "Automation created");
      setOpen(false);
      setEditing(null);
      form.resetFields();
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => patch<Automation>(`/api/automations/${id}`, { enabled }),
    onSuccess: () => refresh(),
    onError: (e: Error) => message.error(e.message),
  });
  const run = useMutation({
    mutationFn: (id: number) => post<Run>(`/api/automations/${id}/run`),
    onSuccess: (r) => {
      message.success(`Run queued: ${r.source_name} (#${r.id})`);
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<{ deleted: number }>(`/api/automations/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      message.success("Automation deleted");
      refresh();
    },
    onError: (e: Error) => message.error(e.message),
  });

  const openCreate = () => {
    setEditing(null);
    setSourceKey(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true });
    setOpen(true);
  };
  const openEdit = (a: Automation) => {
    setEditing(a);
    setSourceKey(a.source);
    form.resetFields();
    form.setFieldsValue({
      name: a.name,
      source: a.source,
      cadence_minutes: a.cadence_minutes,
      enabled: a.enabled,
      rotate_field: a.rotate_field ?? undefined,
      rotate_values: a.rotate_values.join(", "),
      ...a.params,
    });
    setOpen(true);
  };
  const submit = (values: Record<string, unknown>) => {
    const params: Record<string, unknown> = {};
    (spec?.params || []).forEach((p) => {
      const v = values[p.name];
      params[p.name] = v === undefined || v === null || v === "" ? (p.default ?? null) : v;
    });
    const rotateValues = String(values.rotate_values ?? "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    const body: Record<string, unknown> = {
      name: String(values.name ?? "").trim(),
      cadence_minutes: values.cadence_minutes,
      enabled: values.enabled !== false,
      rotate_field: values.rotate_field ?? null,
      rotate_values: rotateValues,
    };
    // Only send params once the source spec has loaded, otherwise an edit would wipe them.
    if (spec) body.params = params;
    if (!editing) body.source = sourceKey;
    save.mutate(body);
  };

  if (automations.isLoading) return <Skeleton active />;
  if (automations.isError) return <Alert type="error" showIcon message={automations.error.message} />;

  return (
    <>
      <Card
        size="small"
        title="Automations"
        extra={
          canEdit && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              New automation
            </Button>
          )
        }
        styles={{ body: { padding: 0 } }}
      >
        <Table<Automation>
          rowKey="id"
          size="small"
          dataSource={automations.data}
          scroll={{ x: 1100 }}
          locale={{ emptyText: "No automations yet" }}
          pagination={{ pageSize: 25, hideOnSinglePage: true }}
          columns={[
            {
              title: "Automation",
              render: (_, a) => (
                <div style={{ lineHeight: 1.3 }}>
                  <div style={{ fontWeight: 600 }}>{a.name}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {Object.entries(a.params)
                      .map(([k, v]) => `${k}: ${String(v)}`)
                      .join(" · ") || "no parameters"}
                  </Typography.Text>
                </div>
              ),
            },
            { title: "Source", dataIndex: "source_name", width: 170 },
            { title: "Cadence", width: 120, render: (_, a) => `Every ${a.cadence_minutes} min` },
            {
              title: "Rotation",
              width: 170,
              render: (_, a) =>
                a.rotate_field ? (
                  <span>
                    {a.rotate_field} → {a.next_value ?? "—"}
                  </span>
                ) : (
                  <Typography.Text type="secondary">—</Typography.Text>
                ),
            },
            {
              title: "Enabled",
              width: 90,
              render: (_, a) => (
                <Switch
                  size="small"
                  checked={a.enabled}
                  disabled={!canEdit}
                  onChange={(enabled) => toggle.mutate({ id: a.id, enabled })}
                />
              ),
            },
            { title: "Next run", width: 150, render: (_, a) => when(a.next_run_at) },
            { title: "Last run", width: 150, render: (_, a) => when(a.last_run_at) },
            {
              title: "Last result",
              width: 160,
              render: (_, a) =>
                a.last_run ? (
                  <Space size={6}>
                    <Tag color={RUN_STATUS[a.last_run.status]} bordered={false}>
                      {a.last_run.status}
                    </Tag>
                    <span style={{ fontVariantNumeric: "tabular-nums" }}>
                      {a.last_run.found} found · {a.last_run.errors} err
                    </span>
                  </Space>
                ) : (
                  <Typography.Text type="secondary">never</Typography.Text>
                ),
            },
            {
              title: "",
              width: 130,
              render: (_, a) =>
                canEdit ? (
                  <Space size={2}>
                    <Button size="small" type="text" icon={<PlayCircleOutlined />} loading={run.isPending} onClick={() => run.mutate(a.id)} />
                    <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(a)} />
                    <Popconfirm title="Delete this automation?" onConfirm={() => remove.mutate(a.id)}>
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                ) : null,
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? `Edit automation: ${editing.name}` : "New automation"}
        open={open}
        onCancel={() => {
          setOpen(false);
          setEditing(null);
        }}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        okText={editing ? "Save" : "Create"}
        destroyOnHidden
        width={680}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={submit}
          onValuesChange={(changed) => {
            if ("source" in changed) {
              const core = form.getFieldsValue(["name", "cadence_minutes", "enabled", "rotate_field", "rotate_values"]);
              form.resetFields();
              form.setFieldsValue(core);
              setSourceKey(typeof changed.source === "string" ? changed.source : null);
            }
          }}
        >
          <Space wrap style={{ width: "100%" }}>
            <Form.Item name="name" label="Name" rules={[{ required: true, max: 120 }]} style={{ minWidth: 240 }}>
              <Input />
            </Form.Item>
            <Form.Item name="source" label="Source" rules={[{ required: !editing }]}>
              <Select
                style={{ width: 220 }}
                disabled={!!editing}
                placeholder="Choose a source"
                options={(sources.data || []).map((s) => ({ value: s.key, label: s.name }))}
                onChange={(v) => v && setSourceKey(v)}
              />
            </Form.Item>
          </Space>
          <Space wrap style={{ width: "100%" }}>
            <Form.Item name="cadence_minutes" label="Cadence (minutes)">
              <InputNumber min={5} max={10080} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="enabled" label="Enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item name="rotate_field" label="Rotate this setting (optional)" extra="Cycles through one value per run, e.g. city">
            <Select
              allowClear
              style={{ width: "100%" }}
              placeholder="No rotation"
              options={(spec?.params || [])
                .filter((p) => p.kind !== "int")
                .map((p) => ({ value: p.name, label: p.label || p.name }))}
            />
          </Form.Item>
          <Form.Item name="rotate_values" label="Values to rotate (comma separated)">
            <Input placeholder="One value per run" />
          </Form.Item>
          {spec && (
            <>
              <Typography.Text type="secondary">{spec.description}</Typography.Text>
              <div style={{ marginTop: 12 }}>
                {spec.params.map((p) => (
                  <Form.Item key={p.name} name={p.name} label={p.label} extra={p.help || undefined} rules={[{ required: p.required }]}>
                    {p.kind === "select" ? (
                      <Select allowClear={!p.required} options={(p.options || []).map((o) => ({ value: o, label: o }))} />
                    ) : p.kind === "int" ? (
                      <InputNumber min={1} max={5000} style={{ width: 200 }} />
                    ) : (
                      <Input />
                    )}
                  </Form.Item>
                ))}
              </div>
            </>
          )}
        </Form>
      </Modal>
    </>
  );
}

// ---- signals ---------------------------------------------------------------------------------
function SignalsTab() {
  const [kinds, setKinds] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const query = new URLSearchParams({ limit: "200" });
  kinds.forEach((k) => query.append("kind", k));
  if (q) query.set("q", q);
  const signals = useQuery({
    queryKey: ["signals", query.toString()],
    queryFn: () => api<Signal[]>(`/api/signals?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH.lists,
  });
  if (signals.isLoading) return <Skeleton active />;
  if (signals.isError) return <Alert type="error" showIcon message={signals.error.message} />;
  return (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <div className="table-toolbar">
        <Input.Search placeholder="Signal or company" allowClear style={{ width: 240 }} onSearch={setQ} />
        <Select
          mode="multiple"
          allowClear
          placeholder="Kind"
          style={{ minWidth: 200 }}
          value={kinds}
          onChange={setKinds}
          options={Object.entries(SIGNAL_KIND).map(([value, label]) => ({ value, label }))}
          maxTagCount="responsive"
        />
      </div>
      <Table<Signal>
        rowKey="id"
        size="small"
        dataSource={signals.data}
        scroll={{ x: 900 }}
        locale={{ emptyText: "No signals match these filters" }}
        pagination={{ pageSize: 25, hideOnSinglePage: true }}
        columns={[
          { title: "When", width: 150, render: (_, s) => when(s.created_at) },
          { title: "Kind", width: 130, render: (_, s) => <Tag bordered={false}>{SIGNAL_KIND[s.kind] ?? s.kind}</Tag> },
          {
            title: "Signal",
            render: (_, s) =>
              s.url ? (
                <a href={s.url} target="_blank" rel="noreferrer noopener">
                  {s.title}
                </a>
              ) : (
                s.title
              ),
          },
          {
            title: "Company",
            render: (_, s) => (
              <div style={{ lineHeight: 1.3 }}>
                <Link to={`/companies?open=${s.company_id}`}>{s.company}</Link>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {[s.segment, s.country].filter(Boolean).join(" · ")}
                </Typography.Text>
              </div>
            ),
          },
          {
            title: "Tier",
            width: 110,
            render: (_, s) =>
              s.tier ? <Tag color={TIER_COLOR[s.tier] ?? "default"} bordered={false}>{s.tier}</Tag> : "—",
          },
          { title: "Fit", width: 70, align: "right", render: (_, s) => s.fit_score ?? "—" },
        ]}
      />
    </Card>
  );
}

// ---- exclusions ------------------------------------------------------------------------------
function ExclusionsTab() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { canEdit, isAdmin } = useMe();
  const exclusions = useQuery({
    queryKey: ["exclusions"],
    queryFn: () => api<Exclusion[]>("/api/exclusions"),
    enabled: canEdit,
    refetchInterval: REFRESH.background,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<{ deleted: number }>(`/api/exclusions/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      message.success("Exclusion removed");
      qc.invalidateQueries({ queryKey: ["exclusions"] });
    },
    onError: (e: Error) => message.error(e.message),
  });
  if (!canEdit) return <Card size="small">Viewers cannot see or change exclusions.</Card>;
  if (exclusions.isLoading) return <Skeleton active />;
  if (exclusions.isError) return <Alert type="error" showIcon message={exclusions.error.message} />;
  return (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <Table<Exclusion>
        rowKey="id"
        size="small"
        dataSource={exclusions.data}
        locale={{ emptyText: "Nothing excluded: deleted companies can be rediscovered" }}
        pagination={{ pageSize: 25, hideOnSinglePage: true }}
        columns={[
          { title: "Value", dataIndex: "value" },
          { title: "Label", dataIndex: "label" },
          {
            title: "Reason",
            dataIndex: "reason",
            render: (v) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
          },
          { title: "Added", width: 150, render: (_, e) => when(e.created_at) },
          {
            title: "",
            width: 90,
            render: (_, e) =>
              isAdmin ? (
                <Popconfirm title="Remove this exclusion? The company may be rediscovered." onConfirm={() => remove.mutate(e.id)}>
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              ) : null,
          },
        ]}
      />
    </Card>
  );
}

// ---- page ------------------------------------------------------------------------------------
export function Automations() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "automations";
  return (
    <>
      <PageHeader
        title="Automations & signals"
        subtitle="Scheduled discovery runs, the exclusion list and every signal across the database"
      />
      <Tabs
        activeKey={tab}
        onChange={(t) => setParams({ tab: t })}
        items={[
          { key: "automations", label: "Automations", children: <AutomationsTab /> },
          { key: "signals", label: "Signals", children: <SignalsTab /> },
          { key: "exclusions", label: "Exclusions", children: <ExclusionsTab /> },
        ]}
      />
    </>
  );
}
