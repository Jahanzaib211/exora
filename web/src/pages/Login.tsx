import { LockOutlined, MailOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Space, Typography } from "antd";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError, post } from "../api";
import { BRAND, ORG, TAGLINE } from "../brand";
import { Logo } from "../Logo";
import { refreshAuthCache } from "../lib/auth-refresh";
import { returnTo } from "../lib/return-to";

/**
 * Sign-in.
 *
 * Deliberately does NOT use Refine's `useLogin`. That hook calls
 * `invalidateAuthStore()` on every completion, including a failure, which resets the
 * identity query and makes `<Authenticated>` re-render the route — discarding the
 * mutation's `data`. The result was that a wrong password produced no visible error
 * at all: the failure state was created and destroyed within the same tick.
 *
 * Driving the request directly keeps the error in local component state, where it
 * survives until the user edits the form or tries again.
 */
export function Login() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  const signIn = useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      post<{ ok?: boolean }>("/api/auth/login", credentials),
    onSuccess: async () => {
      // The cookie is set, but Refine still holds the anonymous identity it fetched
      // while we were on this page. Refresh it or the shell renders as anonymous.
      await refreshAuthCache(queryClient);
      // Return the user where they were. `returnTo` understands both `?next=` (api.ts's
      // 401 handler) and `?to=` (Refine's <Authenticated> fallback) and rejects anything
      // that is not a same-origin path, so this cannot become an open redirect.
      // See App.tsx's LeaveLogin: it races this navigate, so both must agree on target.
      navigate(returnTo(searchParams) ?? "/", { replace: true });
    },
    onError: (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        setError("That email and password do not match an active account.");
      } else if (e instanceof ApiError && e.status === 429) {
        setError("Too many failed attempts. Wait a few minutes and try again.");
      } else {
        setError(e instanceof Error ? e.message : "Sign-in failed. Try again.");
      }
    },
  });

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "var(--exora-surface-sunken, #f5f7fb)",
        padding: 16,
      }}
    >
      <Card style={{ width: "100%", maxWidth: 400 }} styles={{ body: { paddingTop: 28 } }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <Logo size={32} />
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {BRAND}
            </Typography.Title>
            {ORG && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {ORG}
              </Typography.Text>
            )}
          </div>
        </div>
        <Typography.Paragraph type="secondary">{TAGLINE}</Typography.Paragraph>

        {error && (
          <Alert
            type="error"
            showIcon
            role="alert"
            message={error}
            style={{ marginBottom: 16 }}
            data-testid="login-error"
          />
        )}

        <Form
          layout="vertical"
          onFinish={(v) => {
            setError(null);
            signIn.mutate(v);
          }}
          // Clear the message as soon as the user starts correcting their input.
          onFieldsChange={() => setError(null)}
          requiredMark={false}
          disabled={signIn.isPending}
        >
          <Form.Item
            name="email"
            label="Email"
            rules={[{ required: true, message: "Email is required", type: "email" }]}
          >
            <Input
              autoComplete="username"
              autoFocus
              prefix={<MailOutlined />}
              placeholder="you@company.com"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="Password"
            rules={[{ required: true, message: "Password is required" }]}
          >
            <Input.Password autoComplete="current-password" prefix={<LockOutlined />} size="large" />
          </Form.Item>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Button type="primary" htmlType="submit" block size="large" loading={signIn.isPending}>
              Sign in
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Sessions expire automatically. Contact your admin if you have lost your password.
            </Typography.Text>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
