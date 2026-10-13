import { useQuery } from "@tanstack/react-query";
import { Button, Card, Form, Input, Modal, Select, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { api, type Me } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { useConfirmAction, useToastMutation } from "../hooks/useToastMutation";
import { qk, INVALIDATE } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import { useMe } from "../shared";

/**
 * Role labels used consistently in both the table and the create form. Previously
 * the edit dropdown showed the raw values ("admin"/"sdr"/"viewer") while the create
 * form showed the descriptive labels, so the same role read differently in two
 * places on one screen.
 */
const ROLES = [
  { value: "admin", label: "Admin", hint: "Everything, including user management" },
  { value: "sdr", label: "SDR", hint: "Work the plan: companies, tasks, calls, outreach" },
  { value: "viewer", label: "Viewer", hint: "Read only" },
] as const;

const roleLabel = (role: string) => ROLES.find((r) => r.value === role)?.label ?? role;
const roleTagColor = (role: string) =>
  role === "admin" ? "blue" : role === "sdr" ? "green" : "default";

export function Team() {
  const { me, isAdmin, canEdit, isPending: identityPending } = useMe();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const confirmAction = useConfirmAction();

  const q = useQuery({
    queryKey: qk.users(),
    queryFn: () => api<Me[]>("/api/users"),
    enabled: canEdit,
    refetchInterval: REFRESH.background,
  });

  const create = useToastMutation(
    (body: Record<string, string>) => api(`/api/users`, { method: "POST", body: JSON.stringify(body) }),
    {
      invalidates: INVALIDATE.userWrite,
      successMessage: "Teammate added",
      onSuccess: () => {
        setOpen(false);
        form.resetFields();
      },
    },
  );

  const update = useToastMutation(
    ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    { invalidates: INVALIDATE.userWrite },
  );

  if (identityPending) return <Card loading />;
  if (!canEdit) {
    return (
      <>
        <PageHeader title="Team" />
        <Card>
          <EmptyState
            title="The team list is not available to viewers"
            hint="Ask an admin to change your role if you need access."
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Team"
        subtitle="Who can see and change data in this workspace."
        extra={
          isAdmin && (
            <Button type="primary" onClick={() => setOpen(true)}>
              Add teammate
            </Button>
          )
        }
      />
      <QueryBoundary q={q} label="the team list">
        {(data) => (
          <Card size="small" styles={{ body: { padding: 0 } }}>
            {data.length === 0 ? (
              <div style={{ padding: 16 }}>
                <EmptyState
                  title="No teammates yet"
                  hint="Add the first person who needs access to this workspace."
                  action={isAdmin ? { label: "Add teammate", onClick: () => setOpen(true) } : undefined}
                />
              </div>
            ) : (
              <Table<Me>
                rowKey="id"
                size="small"
                dataSource={data}
                pagination={false}
                scroll={{ x: true }}
                columns={[
                  {
                    title: "Name",
                    dataIndex: "name",
                    sorter: (a, b) => a.name.localeCompare(b.name),
                    render: (name: string, u) => (
                      <span>
                        {name}
                        {u.id === me?.id && (
                          <Typography.Text type="secondary" style={{ marginLeft: 6, fontSize: 12 }}>
                            (you)
                          </Typography.Text>
                        )}
                      </span>
                    ),
                  },
                  {
                    title: "Email",
                    dataIndex: "email",
                    sorter: (a, b) => a.email.localeCompare(b.email),
                  },
                  {
                    title: "Role",
                    width: 200,
                    render: (_, u) =>
                      isAdmin && u.id !== me?.id ? (
                        <Select
                          size="small"
                          style={{ width: 180 }}
                          value={u.role}
                          aria-label={`Role for ${u.name}`}
                          options={ROLES.map((r) => ({
                            value: r.value,
                            label: (
                              <Tooltip title={r.hint}>
                                <span>{r.label}</span>
                              </Tooltip>
                            ),
                          }))}
                          loading={update.isPending && update.variables?.id === u.id}
                          onChange={(role) => update.mutate({ id: u.id, body: { role } })}
                        />
                      ) : (
                        <Tag color={roleTagColor(u.role)}>{roleLabel(u.role)}</Tag>
                      ),
                  },
                  {
                    title: "Status",
                    width: 140,
                    render: (_, u) => (
                      <Select
                        size="small"
                        style={{ width: 120 }}
                        value={u.active ? "active" : "inactive"}
                        aria-label={`Status for ${u.name}`}
                        disabled={!isAdmin || u.id === me?.id}
                        options={[
                          { value: "active", label: "Active" },
                          { value: "inactive", label: "Inactive" },
                        ]}
                        loading={update.isPending && update.variables?.id === u.id}
                        onChange={(v) => {
                          if (v === "inactive") {
                            // Deactivating locks a teammate out immediately and there is no
                            // self-service unlock, so make it an explicit decision.
                            confirmAction({
                              title: `Deactivate ${u.name}?`,
                              content: "They will be signed out and unable to sign in again until reactivated.",
                              okText: "Deactivate",
                              onOk: async () => {
                                await update.mutateAsync({ id: u.id, body: { active: false } });
                              },
                            });
                            return;
                          }
                          update.mutate({ id: u.id, body: { active: true } });
                        }}
                      />
                    ),
                  },
                ]}
              />
            )}
          </Card>
        )}
      </QueryBoundary>

      <Modal
        title="Add teammate"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        okText="Add teammate"
        confirmLoading={create.isPending}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ role: "sdr" }}
          onFinish={(v) => create.mutate(v)}
          requiredMark
        >
          <Form.Item name="name" label="Name" rules={[{ required: true, message: "Name is required" }]}>
            <Input autoComplete="name" />
          </Form.Item>
          <Form.Item
            name="email"
            label="Email"
            rules={[{ required: true, type: "email", message: "A valid email is required" }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select options={ROLES.map((r) => ({ value: r.value, label: r.label }))} />
          </Form.Item>
          <Form.Item
            name="password"
            label="Temporary password"
            extra="12+ characters, upper and lower case, and a digit. Share it privately and ask them to change it."
            rules={[{ required: true, min: 12, message: "At least 12 characters" }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}