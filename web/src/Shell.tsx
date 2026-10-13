import {
  ApartmentOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BookOutlined,
  CalendarOutlined,
  CloudDownloadOutlined,
  FundOutlined,
  LogoutOutlined,
  PhoneOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  SearchOutlined,
  SendOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useGetIdentity } from "@refinedev/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AutoComplete, Avatar, Button, Drawer, Dropdown, Input, Layout, Menu, Space, Tag, Tooltip, Typography } from "antd";
import type { InputRef } from "antd";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { api, post, type Me } from "./api";
import { BRAND, ORG } from "./brand";
import { ThemeToggle } from "./components/ThemeProvider";
import { clearAuthCache } from "./lib/auth-refresh";
import { qk } from "./lib/query-keys";
import { Logo } from "./Logo";
import { BRAND_BLUE } from "./theme";

/**
 * Below this width the Sider collapses to zero and navigation moves behind the
 * header button. Keep in step with the `.filter-rail` breakpoint in index.css and
 * with `Layout`'s `lg` token (992px) — the four hardcoded `992`s this replaced were
 * the reason the filter rail never collapsed on mobile.
 */
export const SIDER_BP = 992;

export const NAV = [
  {
    type: "group" as const,
    label: "Prospect",
    children: [
      { key: "/companies", icon: <ApartmentOutlined />, label: "Companies" },
      { key: "/people", icon: <UserOutlined />, label: "People" },
    ],
  },
  {
    type: "group" as const,
    label: "Sourcing",
    children: [
      { key: "/discovery", icon: <CloudDownloadOutlined />, label: "Discovery" },
      { key: "/quality", icon: <SafetyCertificateOutlined />, label: "Data quality" },
      { key: "/engine", icon: <ThunderboltOutlined />, label: "Data engine" },
      { key: "/automations", icon: <RobotOutlined />, label: "Automations" },
    ],
  },
  {
    type: "group" as const,
    label: "Engage",
    children: [
      { key: "/outreach", icon: <SendOutlined />, label: "Outreach" },
      { key: "/tasks", icon: <ScheduleOutlined />, label: "Tasks" },
      { key: "/calls", icon: <PhoneOutlined />, label: "Calls & pilots" },
    ],
  },
  {
    type: "group" as const,
    label: "Plan",
    children: [
      { key: "/", icon: <AppstoreOutlined />, label: "Overview" },
      { key: "/playbook", icon: <CalendarOutlined />, label: "Playbook" },
      { key: "/analytics", icon: <FundOutlined />, label: "Analytics" },
      { key: "/docs", icon: <BookOutlined />, label: "Documents" },
    ],
  },
  {
    type: "group" as const,
    label: "Admin",
    children: [
      { key: "/team", icon: <SettingOutlined />, label: "Team" },
      { key: "/audit", icon: <AuditOutlined />, label: "Audit log" },
    ],
  },
];

type Hit = { id: number; name: string; domain?: string | null; company?: string | null };

/** Debounce a rapidly-changing value. */
function useDebounced<T>(value: T, delay = 250): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return settled;
}

/**
 * Global search.
 *
 * Two problems this fixes:
 *  1. No debounce — one request pair per keystroke, and because the raw query was in
 *     the cache key, every character created a new cache entry that lived for 5 minutes.
 *  2. State lived in `Shell`, so typing re-rendered the whole shell *and* the routed
 *     page behind it. A ten-character query meant ten full re-renders of a 200-row
 *     companies table. Isolating the input in its own component fixes both.
 */
function GlobalSearch({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const [raw, setRaw] = useState("");
  const q = useDebounced(raw.trim(), 250);
  const enabled = q.length >= 2;
  const inputRef = useRef<InputRef>(null);

  // Cmd-K / Ctrl-K focuses search from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const companies = useQuery({
    queryKey: qk.searchCompanies(q),
    queryFn: () => api<{ items: Hit[] }>(`/api/companies?page_size=6&q=${encodeURIComponent(q)}`),
    enabled,
    staleTime: 30_000,
  });
  const people = useQuery({
    queryKey: qk.searchPeople(q),
    queryFn: () => api<{ items: Hit[] }>(`/api/people?page_size=6&q=${encodeURIComponent(q)}`),
    enabled,
    staleTime: 30_000,
  });

  const searching = companies.isFetching || people.isFetching;
  const options = useMemo(() => {
    const c = (companies.data?.items ?? []).map((hit) => ({
      value: `c:${hit.id}`,
      label: (
        <Space>
          <span>{hit.name}</span>
          {hit.domain && <Typography.Text type="secondary">{hit.domain}</Typography.Text>}
        </Space>
      ),
    }));
    const p = (people.data?.items ?? []).map((hit) => ({
      value: `p:${hit.id}`,
      label: (
        <Space>
          <span>{hit.name}</span>
          {hit.company && <Typography.Text type="secondary">{hit.company}</Typography.Text>}
        </Space>
      ),
    }));
    return [
      { label: "Companies", options: c },
      { label: "People", options: p },
    ].filter((g) => g.options.length > 0);
  }, [companies.data, people.data]);

  return (
    <AutoComplete
      value={raw}
      options={options}
      onSearch={setRaw}
      onChange={setRaw}
      onSelect={(v: string) => {
        const [kind, id] = v.split(":");
        setRaw("");
        navigate(kind === "c" ? `/companies?open=${id}` : `/people?open=${id}`);
        onNavigate?.();
      }}
      popupMatchSelectWidth={420}
      notFoundContent={
        enabled && !searching ? <Typography.Text type="secondary">No matches for “{q}”</Typography.Text> : null
      }
      style={{ width: 360, maxWidth: "100%" }}
    >
      <Input
        ref={inputRef}
        aria-label="Search companies and people"
        placeholder="Search companies and people"
        prefix={<SearchOutlined />}
        allowClear
        suffix={
          <Tooltip title="Focus search (Ctrl+K)">
            <kbd
              style={{
                fontSize: 10,
                color: "var(--exora-muted, #8c8c8c)",
                border: "1px solid var(--exora-border, #e6e9ef)",
                borderRadius: 4,
                padding: "1px 4px",
              }}
            >
              ⌘K
            </kbd>
          </Tooltip>
        }
      />
    </AutoComplete>
  );
}

/** Navigation rendered inside the Sider or the mobile Drawer. */
function NavMenu({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const selected = "/" + (pathname.split("/")[1] || "");
  return (
    <Menu
      theme="dark"
      mode="inline"
      selectedKeys={[selected]}
      items={NAV}
      onClick={(e) => {
        navigate(e.key);
        onNavigate?.();
      }}
      style={{ borderInlineEnd: "none" }}
    />
  );
}

function Brand({ collapsed }: { collapsed: boolean }) {
  if (collapsed) return null;
  return (
    <div style={{ padding: "16px 20px 12px", color: "#fff", display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
      <Logo size={26} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 15, letterSpacing: 0.2 }}>{BRAND}</div>
        {ORG && <div style={{ color: "#7d8ba3", fontSize: 12 }}>{ORG}</div>}
      </div>
    </div>
  );
}

function useIsNarrow() {
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.innerWidth < SIDER_BP,
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    let below = window.innerWidth < SIDER_BP;
    const onResize = () => {
      const now = window.innerWidth < SIDER_BP;
      if (now !== below) {
        below = now;
        setNarrow(now);
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return narrow;
}

export function Shell({ children }: { children?: ReactNode }) {
  const navigate = useNavigate();
  const { data: me } = useGetIdentity<Me>();
  const queryClient = useQueryClient();
  const [signingOut, setSigningOut] = useState(false);
  const narrow = useIsNarrow();
  // Above the breakpoint the Sider is always present; below it we render a Drawer
  // instead, so there is no collapsed state to fight antd's `breakpoint` handler with.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  const menuButton = (
    <Button
      type="text"
      aria-label={narrow ? "Open navigation menu" : undefined}
      aria-expanded={narrow ? drawerOpen : undefined}
      icon={<MenuUnfoldOutlined />}
      onClick={() => (narrow ? setDrawerOpen(true) : undefined)}
      style={{ flex: "0 0 auto" }}
      data-testid="nav-toggle"
    />
  );

  return (
    <Layout style={{ minHeight: "100vh" }}>
      {!narrow && (
        <Layout.Sider
          width={216}
          collapsedWidth={216}
          style={{ position: "sticky", top: 0, height: "100vh", overflow: "auto" }}
        >
          <Brand collapsed={false} />
          <NavMenu />
        </Layout.Sider>
      )}

      {narrow && (
        <Drawer
          placement="left"
          open={drawerOpen}
          onClose={closeDrawer}
          width={260}
          closable={false}
          styles={{ body: { padding: 0, background: "#0f1b2d" } }}
          aria-label="Navigation"
        >
          <Brand collapsed={false} />
          <NavMenu onNavigate={closeDrawer} />
        </Drawer>
      )}

      <Layout>
        <Layout.Header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            borderBottom: "1px solid var(--exora-border, #e6e9ef)",
            position: "sticky",
            top: 0,
            zIndex: 10,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0, flex: 1 }}>
            {narrow && menuButton}
            <div style={{ minWidth: 0, flex: 1, display: "flex" }}>
              <GlobalSearch onNavigate={closeDrawer} />
            </div>
            <ThemeToggle />
          </div>
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [
                {
                  key: "logout",
                  icon: <LogoutOutlined />,
                  label: signingOut ? "Signing out…" : "Sign out",
                  disabled: signingOut,
                  /*
                    Sign out is handled here rather than through Refine's `useLogout`:
                    that hook swallows the promise, so the shell can render the
                    signed-in user again on the way to /login. Clearing the cached
                    identity ourselves makes the transition deterministic.
                  */
                  onClick: async () => {
                    setSigningOut(true);
                    try {
                      await post("/api/auth/logout");
                    } catch {
                      // Clearing local state is still right if the request failed.
                    }
                    await clearAuthCache(queryClient);
                    navigate("/login", { replace: true });
                  },
                },
              ],
            }}
          >
            <button
              type="button"
              aria-label={`Account menu for ${me?.name ?? "current user"}`}
              style={{
                background: "none",
                border: 0,
                cursor: "pointer",
                padding: "2px 6px",
                borderRadius: 6,
                color: "inherit",
              }}
            >
              <Space>
                <Avatar size="small" style={{ background: BRAND_BLUE }}>
                  {(me?.name || "?").slice(0, 1)}
                </Avatar>
                <span>{me?.name}</span>
                {me?.role && (
                  <Tag bordered={false} style={{ textTransform: "capitalize" }}>
                    {me.role}
                  </Tag>
                )}
              </Space>
            </button>
          </Dropdown>
        </Layout.Header>
        <Layout.Content style={{ padding: narrow ? 12 : 20, minWidth: 0 }}>{children ?? <Outlet />}</Layout.Content>
      </Layout>
    </Layout>
  );
}

export function PageHeader({ title, subtitle, extra }: { title: string; subtitle?: ReactNode; extra?: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-end",
        gap: 12,
        marginBottom: 16,
        flexWrap: "wrap",
      }}
    >
      <div style={{ minWidth: 0 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {subtitle && (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {subtitle}
          </Typography.Text>
        )}
      </div>
      {extra && <Space wrap>{extra}</Space>}
    </div>
  );
}