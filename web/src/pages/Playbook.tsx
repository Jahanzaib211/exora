import { Tabs } from "antd";
import { useSearchParams } from "react-router";
import { PageHeader } from "../Shell";
import { Events } from "./Events";
import { Plan } from "./Plan";
import { Routine } from "./Routine";

const TABS = [
  { key: "plan", label: "30-day plan" },
  { key: "routine", label: "Daily routine" },
  { key: "events", label: "Events" },
] as const;

export function Playbook() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  const tab = TABS.some((t) => t.key === requested) ? (requested as string) : "plan";

  return (
    <>
      <PageHeader
        title="Playbook"
        subtitle="The 30-day execution plan, the daily routine and the target events."
      />
      <Tabs
        activeKey={tab}
        onChange={(t) =>
          // Functional update with a clone: `setParams({tab})` replaced the whole query
          // string and dropped anything else that was in the URL.
          setSearchParams(
            (prev) => {
              const next = new URLSearchParams(prev);
              next.set("tab", t);
              return next;
            },
            { replace: true },
          )
        }
        items={[
          { key: "plan", label: TABS[0].label, children: <Plan /> },
          { key: "routine", label: TABS[1].label, children: <Routine /> },
          { key: "events", label: TABS[2].label, children: <Events /> },
        ]}
      />
    </>
  );
}