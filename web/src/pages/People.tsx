import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button, Card, Checkbox, Descriptions, Drawer, Input, Select, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { rowActivation, SafeLink } from "../components/SafeLink";
import { DASH, fmtDate, humanise } from "../lib/format";
import { qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import type { PersonRow } from "../types/api";

/** Seniority buckets, matching `contact/patterns.py` on the server. */
const SENIORITY: { value: string; label: string }[] = [
  { value: "owner", label: "Owner" },
  { value: "partner", label: "Partner" },
  { value: "c_level", label: "C-level" },
  { value: "director", label: "Director" },
  { value: "manager", label: "Manager" },
  { value: "other", label: "Other" },
];
const seniorityLabel = (v: string | null) => SENIORITY.find((s) => s.value === v)?.label ?? humanise(v);

/** What an email address's provenance means. */
const EMAIL_STATUS: Record<string, string> = {
  published: "Published by the company",
  role: "Shared inbox",
};

/** Where a field value came from. */
const SOURCE_LABEL: Record<string, string> = {
  website: "Company website",
  registry: "Company register",
  linkedin: "LinkedIn",
  press: "Press / news",
  manual: "Entered by the team",
  jobs: "Job board",
  github: "GitHub",
};

type PersonDetail = PersonRow & {
  sources: { field: string; value: string; source: string; url: string | null; retrieved_at: string | null }[];
};

function PersonDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: qk.person(id ?? ""),
    queryFn: () => api<PersonDetail>(`/api/people/${id}`),
    enabled: id !== null,
    refetchInterval: REFRESH.lists,
  });

  return (
    <Drawer open={id !== null} onClose={onClose} width={560} title={q.data?.name ?? "Contact"} loading={q.isPending}>
      <QueryBoundary q={q} label="this contact" skeletonRows={8}>
        {(data) => (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="Title">{data.title ?? DASH}</Descriptions.Item>
              <Descriptions.Item label="Seniority">{seniorityLabel(data.seniority)}</Descriptions.Item>
              <Descriptions.Item label="Company">
                {data.company_id ? (
                  <Link to={`/companies?open=${data.company_id}`}>{data.company}</Link>
                ) : (
                  data.company ?? DASH
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Email">
                {data.email ? (
                  <Space>
                    <span>{data.email}</span>
                    {data.email_status && (
                      <Tag bordered={false}>{EMAIL_STATUS[data.email_status] ?? humanise(data.email_status)}</Tag>
                    )}
                  </Space>
                ) : (
                  <Typography.Text type="secondary">{DASH}</Typography.Text>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Phone">{data.phone ?? DASH}</Descriptions.Item>
              <Descriptions.Item label="LinkedIn">
                {data.linkedin_url ? (
                  // Crawled third-party URL: scheme-allowlisted by SafeLink.
                  <SafeLink href={data.linkedin_url} showUrl>
                    {data.linkedin_url.replace(/^https?:\/\/(www\.|[a-z]{2,3}\.)?/, "")}
                  </SafeLink>
                ) : (
                  <Typography.Text type="secondary">{DASH}</Typography.Text>
                )}
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title="Sources" styles={{ body: { padding: 0 } }}>
              {data.sources.length === 0 ? (
                <div style={{ padding: 16 }}>
                  <EmptyState compact title="No provenance recorded" hint="Every field should name where it came from." />
                </div>
              ) : (
                <Table
                  // `value` is not unique: the same field can be sourced twice.
                  rowKey={(r, i) => `${i}:${r.field}:${r.source}:${r.value}`}
                  size="small"
                  pagination={false}
                  dataSource={data.sources}
                  scroll={{ x: 520 }}
                  columns={[
                    { title: "Field", dataIndex: "field", width: 110, ellipsis: true },
                    { title: "Value", dataIndex: "value", ellipsis: true },
                    {
                      title: "Source",
                      width: 140,
                      ellipsis: true,
                      render: (_, r) => <Tag bordered={false}>{SOURCE_LABEL[r.source] ?? humanise(r.source)}</Tag>,
                    },
                    {
                      title: "Evidence",
                      width: 90,
                      render: (_, r) => (r.url ? <SafeLink href={r.url}>Open</SafeLink> : <Typography.Text type="secondary">{DASH}</Typography.Text>),
                    },
                    {
                      title: "Retrieved",
                      width: 110,
                      // Was `r.retrieved_at.slice(0, 10)` — crashes the drawer on a null.
                      render: (_, r) => fmtDate(r.retrieved_at),
                    },
                  ]}
                />
              )}
            </Card>
          </Space>
        )}
      </QueryBoundary>
    </Drawer>
  );
}

export function People() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [seniority, setSeniority] = useState<string[]>([]);
  const [hasEmail, setHasEmail] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const openRaw = searchParams.get("open");
  const openId = openRaw && /^\d+$/.test(openRaw) ? Number(openRaw) : null;

  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (q) query.set("q", q);
  seniority.forEach((s) => query.append("seniority", s));
  if (hasEmail) query.set("has_email", "true");

  const list = useQuery({
    queryKey: qk.people(query.toString()),
    queryFn: () => api<{ items: PersonRow[]; total: number }>(`/api/people?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: REFRESH.lists,
  });

  const isFiltered = !!(q || seniority.length || hasEmail);
  const clearFilters = () => {
    setQ("");
    setSeniority([]);
    setHasEmail(false);
    setPage(1);
  };

  const openRow = (id: number) =>
    // Functional update with a fresh clone: mutating the returned instance and passing
    // it back (the previous `params.delete(); setParams(params)`) is a documented no-op
    // pattern in react-router v7 and silently kept the drawer open after close.
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("open", String(id));
        return next;
      },
      { replace: false },
    );

  const closeRow = () =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("open");
        return next;
      },
      { replace: true },
    );

  // Clamp the page when a filter or delete shrinks the result set, otherwise the table
  // renders empty on an unreachable page number.
  const total = list.data?.total ?? 0;
  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, maxPage);
  if (safePage !== page) setPage(safePage);

  return (
    <>
      <PageHeader title="People" subtitle="Named contacts published on company websites and in research sources." />
      <Card size="small" styles={{ body: { padding: 0 } }}>
        <div className="table-toolbar">
          <label className="filter-label" htmlFor="people-search">
            Search
          </label>
          <Input.Search
            id="people-search"
            placeholder="Name, title or company"
            allowClear
            style={{ width: 280 }}
            onSearch={(v) => {
              setQ(v);
              setPage(1);
            }}
          />
          <label className="filter-label" htmlFor="people-seniority">
            Seniority
          </label>
          <Select
            id="people-seniority"
            mode="multiple"
            allowClear
            placeholder="Any seniority"
            style={{ minWidth: 200 }}
            value={seniority}
            onChange={(v) => {
              setSeniority(v);
              setPage(1);
            }}
            options={SENIORITY.map((s) => ({ value: s.value, label: s.label }))}
            maxTagCount="responsive"
          />
          <Checkbox
            checked={hasEmail}
            onChange={(e) => {
              setHasEmail(e.target.checked);
              setPage(1);
            }}
          >
            Has email
          </Checkbox>
          {isFiltered && <Button size="small" onClick={clearFilters}>Clear filters</Button>}
        </div>

        <QueryBoundary
          q={list}
          label="people"
          skeletonRows={8}
          empty={
            isFiltered ? (
              <div style={{ padding: 16 }}>
                <EmptyState
                  title="No contacts match these filters"
                  hint="Try widening the seniority filter or clearing the search."
                  action={{ label: "Clear filters", onClick: clearFilters }}
                />
              </div>
            ) : (
              <div style={{ padding: 16 }}>
                <EmptyState
                  title="No contacts yet"
                  hint="People appear once a discovery source or a website crawl finds a named individual."
                />
              </div>
            )
          }
        >
          {(data) =>
            data.items.length === 0 ? (
              <div style={{ padding: 16 }}>
                {isFiltered ? (
                  <EmptyState
                    title="No contacts match these filters"
                    hint="Try widening the seniority filter or clearing the search."
                    action={{ label: "Clear filters", onClick: clearFilters }}
                  />
                ) : (
                  <EmptyState
                    title="No contacts yet"
                    hint="People appear once a discovery source or a website crawl finds a named individual."
                  />
                )}
              </div>
            ) : (
              <Table<PersonRow>
                rowKey="id"
                size="small"
                loading={list.isFetching}
                dataSource={data.items}
                scroll={{ x: 1000 }}
                // Rows open a drawer, so they must also respond to Enter/Space.
                onRow={(r) => ({
                  ...rowActivation(() => openRow(r.id), `Open ${r.name}`),
                  className: "row-clickable",
                })}
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
                  },
                }}
                columns={[
                  {
                    title: "Name",
                    dataIndex: "name",
                    sorter: (a, b) => a.name.localeCompare(b.name),
                    render: (n: string) => <span style={{ fontWeight: 600 }}>{n}</span>,
                  },
                  { title: "Title", dataIndex: "title", ellipsis: true, render: (v) => v ?? DASH },
                  {
                    title: "Seniority",
                    width: 140,
                    render: (_, p) => seniorityLabel(p.seniority),
                    filters: SENIORITY.map((s) => ({ text: s.label, value: s.value })),
                    onFilter: (value, p) => p.seniority === value,
                  },
                  {
                    title: "Company",
                    render: (_, p) => (
                      <div style={{ lineHeight: 1.3 }}>
                        <div>{p.company ?? DASH}</div>
                        {p.company_domain && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {p.company_domain}
                          </Typography.Text>
                        )}
                      </div>
                    ),
                    sorter: (a, b) => (a.company ?? "").localeCompare(b.company ?? ""),
                  },
                  { title: "Country", dataIndex: "country", width: 80, render: (v) => v ?? DASH },
                  {
                    title: "Phone",
                    dataIndex: "phone",
                    width: 150,
                    render: (v) => v ?? <Typography.Text type="secondary">{DASH}</Typography.Text>,
                  },
                  {
                    title: "LinkedIn",
                    dataIndex: "linkedin_url",
                    width: 90,
                    render: (v) =>
                      v ? (
                        <SafeLink href={v}>Profile</SafeLink>
                      ) : (
                        <Typography.Text type="secondary">{DASH}</Typography.Text>
                      ),
                  },
                  {
                    title: "Email",
                    width: 220,
                    ellipsis: true,
                    render: (_, p) =>
                      p.email ? (
                        <span>{p.email}</span>
                      ) : (
                        <Typography.Text type="secondary">{DASH}</Typography.Text>
                      ),
                    sorter: (a, b) => (a.email ?? "").localeCompare(b.email ?? ""),
                  },
                ]}
              />
            )
          }
        </QueryBoundary>
      </Card>
      <PersonDrawer id={openId} onClose={closeRow} />
    </>
  );
}