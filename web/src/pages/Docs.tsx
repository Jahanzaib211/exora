import { useQuery } from "@tanstack/react-query";
import { Card, Col, Input, Menu, Row, Space, Typography } from "antd";
import { useMemo } from "react";
import Markdown from "react-markdown";
import { useSearchParams } from "react-router";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryBoundary } from "../components/QueryBoundary";
import { fmtDateTime, humanise } from "../lib/format";
import { qk } from "../lib/query-keys";
import { REFRESH } from "../live";
import { PageHeader } from "../Shell";
import type { Doc, DocGroup } from "../types/api";

/** Opened when the URL names no document: the first of these that the catalogue serves, else its first entry. */
const START_DOCS = ["README.md", "00_README_Start_Here.md"];

export function Docs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = (searchParams.get("filter") ?? "").trim();

  const groups = useQuery({
    queryKey: qk.docs(),
    queryFn: () => api<DocGroup[]>("/api/docs"),
    refetchInterval: REFRESH.static,
  });

  // Never request a hardcoded path: which documents exist depends on PROSPECTOR_DOCS_ROOT.
  const startDoc = useMemo(() => {
    const paths = (groups.data ?? []).flatMap((g) => g.docs.map((d) => d.path));
    return START_DOCS.find((p) => paths.includes(p)) ?? paths[0] ?? "";
  }, [groups.data]);
  const selected = searchParams.get("doc") || startDoc;

  const doc = useQuery({
    queryKey: qk.doc(selected),
    queryFn: () => api<Doc>(`/api/docs/content?path=${encodeURIComponent(selected)}`),
    refetchInterval: REFRESH.static,
    enabled: !!selected,
  });

  const items = useMemo(
    () =>
      (groups.data ?? [])
        .map((g) => ({
          key: g.group,
          label: g.group,
          type: "group" as const,
          children: g.docs
            .filter((d) => !filter || `${d.title} ${d.path}`.toLowerCase().includes(filter.toLowerCase()))
            .map((d) => ({ key: d.path, label: d.title, title: d.path })),
        }))
        // Previously the group header survived with zero matching children, so
        // filtering left a column of empty headings.
        .filter((g) => g.children.length > 0),
    [groups.data, filter],
  );

  const setParam = (key: string, value: string | null) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === null || value === "") next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );

  return (
    <>
      <PageHeader
        title="Documents"
        subtitle="The playbook and documentation, served read-only from the markdown in this repository."
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={7}>
          <Card size="small" title="Document suite" styles={{ body: { padding: 8 } }}>
            <label className="sr-only" htmlFor="docs-filter">
              Filter documents
            </label>
            <Input.Search
              id="docs-filter"
              placeholder="Filter documents"
              allowClear
              defaultValue={filter}
              onSearch={(v) => setParam("filter", v)}
              onChange={(e) => {
                const v = e.target.value;
                if (!v) setParam("filter", null);
              }}
            />
            <QueryBoundary q={groups} label="the document list" skeletonRows={8}>
            {() =>
              items.length === 0 ? (
                <div style={{ padding: 16 }}>
                  <EmptyState compact title="No documents match" hint="Try a different search term." />
                </div>
              ) : (
                <Menu
                  mode="inline"
                  selectedKeys={[selected]}
                  items={items}
                  onClick={(e) => setParam("doc", e.key)}
                  style={{ borderInlineEnd: "none", maxHeight: "70vh", overflow: "auto" }}
                />
              )
            }
          </QueryBoundary>
          </Card>
        </Col>
        <Col xs={24} lg={17}>
          <Card
            title={doc.data?.title ?? "Document"}
            extra={
              doc.data && (
                <Space size={8} wrap>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {doc.data.path}
                  </Typography.Text>
                  {doc.data.modified && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      updated {fmtDateTime(doc.data.modified)}
                    </Typography.Text>
                  )}
                </Space>
              )
            }
          >
            <QueryBoundary
              q={doc}
              label="this document"
              skeletonRows={12}
              empty={
                <EmptyState
                  title="Pick a document"
                  hint="Choose one from the list to read it here."
                />
              }
            >
              {(data) => (
                <article className="md">
                  {/*
                    No `rehype-raw` and the default `urlTransform`, so raw HTML in the
                    markdown is escaped and `javascript:` links are neutralised. Safe to
                    render untrusted documents as-is.
                  */}
                  <Markdown remarkPlugins={[remarkGfm]}>{data.markdown}</Markdown>
                </article>
              )}
            </QueryBoundary>
          </Card>
        </Col>
      </Row>
    </>
  );
}

/** Group heading fallback for docs that carry no group label. */
export const groupLabel = (g: DocGroup) => g.group || humanise("documents");