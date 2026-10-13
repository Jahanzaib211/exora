import { Authenticated, Refine } from "@refinedev/core";
import routerProvider, { CatchAllNavigate } from "@refinedev/react-router";
import { App as AntdApp } from "antd";
import "@refinedev/antd/dist/reset.css";
import { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useSearchParams } from "react-router";
import { authProvider, dataProvider } from "./api";
import { BRAND } from "./brand";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { PageSpinner } from "./components/QueryBoundary";
import { ExoraThemeProvider } from "./components/ThemeProvider";
import { liveQueryClientConfig } from "./live";
import { returnTo } from "./lib/return-to";
import { Shell } from "./Shell";

/**
 * Leave /login once signed in, headed for wherever the user was trying to reach.
 *
 * This wrapper and pages/Login.tsx both navigate after a successful sign-in, and they
 * race: Login's `navigate("/tasks")` landed first and this component's hardcoded
 * `<Navigate to="/" replace />` immediately overwrote it, so signing in again after a
 * session expiry always dropped the user on Overview no matter what `?to=`/`?next=`
 * said. Honouring the requested destination makes both navigations agree, so whichever
 * one runs last leaves the user in the right place.
 *
 * With no destination recorded (someone typed /login by hand while already signed in)
 * Overview is the correct default.
 */
function LeaveLogin() {
  const [params] = useSearchParams();
  return <Navigate to={returnTo(params) ?? "/"} replace />;
}

/**
 * Every page is a separate chunk. Previously all 15 were statically imported, so the
 * Docs tab's markdown parser and the Ant Design table code were downloaded before
 * the first paint even on the Overview screen.
 */
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const Companies = lazy(() => import("./pages/Companies").then((m) => ({ default: m.Companies })));
const People = lazy(() => import("./pages/People").then((m) => ({ default: m.People })));
const Discovery = lazy(() => import("./pages/Discovery").then((m) => ({ default: m.Discovery })));
const Quality = lazy(() => import("./pages/Quality").then((m) => ({ default: m.Quality })));
const Engine = lazy(() => import("./pages/Engine").then((m) => ({ default: m.Engine })));
const Automations = lazy(() => import("./pages/Automations").then((m) => ({ default: m.Automations })));
const Outreach = lazy(() => import("./pages/Outreach").then((m) => ({ default: m.Outreach })));
const Tasks = lazy(() => import("./pages/Tasks").then((m) => ({ default: m.Tasks })));
const Calls = lazy(() => import("./pages/Calls").then((m) => ({ default: m.Calls })));
const Playbook = lazy(() => import("./pages/Playbook").then((m) => ({ default: m.Playbook })));
const Kpis = lazy(() => import("./pages/Kpis").then((m) => ({ default: m.Kpis })));
const Docs = lazy(() => import("./pages/Docs").then((m) => ({ default: m.Docs })));
const Team = lazy(() => import("./pages/Team").then((m) => ({ default: m.Team })));
const Audit = lazy(() => import("./pages/Audit").then((m) => ({ default: m.Audit })));
/**
 * Login is deliberately NOT lazy: it is the first screen an unauthenticated visitor
 * sees, and a second waterfall (HTML → index chunk → Login chunk) is exactly the
 * wrong thing to add to a sign-in screen.
 */
import { Login } from "./pages/Login";

/** Wraps a lazy page in the two things every route needs: a spinner and an error net. */
const route = (label: string, element: React.ReactNode) => (
  <AppErrorBoundary label={label}>
    <Suspense fallback={<PageSpinner label={`Loading ${label}`} />}>{element}</Suspense>
  </AppErrorBoundary>
);

export default function App() {
  return (
    <BrowserRouter>
      <ExoraThemeProvider>
        <AntdApp>
          <Refine
            authProvider={authProvider}
            dataProvider={dataProvider}
            routerProvider={routerProvider}
            options={{
              syncWithLocation: false,
              disableTelemetry: true,
              title: { text: BRAND },
              // Refine builds the app's single QueryClient from this config and
              // provides it to every useQuery below (see src/live.ts).
              reactQuery: { clientConfig: liveQueryClientConfig },
            }}
          >
            <Routes>
              <Route
                element={
                  /* `key` is required by Refine's AuthenticatedProps; the value is a
                     constant because these wrappers never remount by design. */
                  <Authenticated key="app" fallback={<CatchAllNavigate to="/login" />}>
                    <Shell />
                  </Authenticated>
                }
              >
                <Route index element={route("Overview", <Dashboard />)} />
                <Route path="/companies" element={route("Companies", <Companies />)} />
                <Route path="/people" element={route("People", <People />)} />
                <Route path="/discovery" element={route("Discovery", <Discovery />)} />
                <Route path="/quality" element={route("Data quality", <Quality />)} />
                <Route path="/engine" element={route("Data engine", <Engine />)} />
                <Route path="/automations" element={route("Automations", <Automations />)} />
                <Route path="/outreach" element={route("Outreach", <Outreach />)} />
                <Route path="/tasks" element={route("Tasks", <Tasks />)} />
                <Route path="/calls" element={route("Calls & pilots", <Calls />)} />
                <Route path="/playbook" element={route("Playbook", <Playbook />)} />
                <Route path="/analytics" element={route("Analytics", <Kpis />)} />
                <Route path="/docs" element={route("Documents", <Docs />)} />
                <Route path="/team" element={route("Team", <Team />)} />
                <Route path="/audit" element={route("Audit log", <Audit />)} />
                {/* earlier URLs */}
                <Route path="/plan" element={<Navigate to="/playbook?tab=plan" replace />} />
                <Route path="/routine" element={<Navigate to="/playbook?tab=routine" replace />} />
                <Route path="/events" element={<Navigate to="/playbook?tab=events" replace />} />
                <Route path="/kpis" element={<Navigate to="/analytics" replace />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
              <Route
                element={
                  <Authenticated key="anon" fallback={<Outlet />}>
                    <LeaveLogin />
                  </Authenticated>
                }
              >
                <Route path="/login" element={route("Sign in", <Login />)} />
              </Route>
            </Routes>
          </Refine>
        </AntdApp>
      </ExoraThemeProvider>
    </BrowserRouter>
  );
}