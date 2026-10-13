import { Tooltip, Typography } from "antd";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";

/**
 * External link that refuses unsafe schemes.
 *
 * React renders `href` verbatim — it warns about `javascript:` but does not block
 * it — and these URLs come from crawled third-party websites (`Company.website`,
 * `Person.linkedin_url`, `Signal.url`, `Evidence.url`). `backend` only validates
 * `website` through `domain_of`; `linkedin_url` is stored with nothing more than
 * `.split("?")[0].rstrip("/")`. Rendering those raw meant a stored `javascript:`
 * URL would execute on click. This component allowlists the scheme instead.
 */

const SAFE_SCHEME = /^(https?:|mailto:|tel:)/i;

export function isSafeHref(href: string | null | undefined): boolean {
  if (!href) return false;
  const trimmed = href.trim();
  // Relative and anchor links are inherently same-origin.
  if (trimmed.startsWith("/") || trimmed.startsWith("#")) return true;
  return SAFE_SCHEME.test(trimmed);
}

type Props = {
  href: string | null | undefined;
  children: ReactNode;
  /** Show the raw URL as a tooltip when it differs from the label. */
  showUrl?: boolean;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

export function SafeLink({ href, children, showUrl, ...rest }: Props) {
  if (!href || !isSafeHref(href)) {
    // Render inert text rather than a dead link, and explain why if it looked like a URL.
    const looksLikeUrl = !!href && /^[a-z][a-z0-9+.-]*:/i.test(href.trim());
    const body = <span style={{ color: "#8c8c8c", textDecoration: "line-through" }}>{children}</span>;
    return looksLikeUrl ? <Tooltip title="Blocked: this link uses an unsupported scheme">{body}</Tooltip> : body;
  }

  const anchor = (
    <a href={href} target="_blank" rel="noreferrer noopener nofollow" {...rest}>
      {children}
    </a>
  );

  return showUrl && href !== children ? <Tooltip title={href}>{anchor}</Tooltip> : anchor;
}

/** Domain text that links out when safe, plain text otherwise. */
export function SafeDomain({ href, domain }: { href: string | null | undefined; domain: string | null | undefined }) {
  if (!domain) return <Typography.Text type="secondary">—</Typography.Text>;
  if (!href || !isSafeHref(href)) return <span>{domain}</span>;
  return <SafeLink href={href}>{domain}</SafeLink>;
}

/**
 * Row click handler that also responds to Enter/Space, so a table row that opens a
 * drawer is reachable without a mouse. Used with antd's `onRow`.
 */
export function rowActivation(open: () => void, label: string) {
  /**
   * Anything *inside* the row that handles its own click. antd renders a row-selection
   * checkbox as a `<label class="ant-checkbox-wrapper">` containing a hidden `<input>`;
   * the click lands on an inner `<span>`, so matching only on `input` let those clicks
   * fall through and opened the drawer instead of toggling the checkbox.
   */
  const INTERACTIVE =
    "a, button, input, select, textarea, [role='button'], [role='checkbox'], .ant-checkbox-wrapper, .ant-checkbox, .ant-switch, .ant-pagination, .ant-table-row-selection";

  /**
   * `closest()` includes the element it is called on, and this row is itself marked
   * `role="button"` below - so a bare `closest(INTERACTIVE)` always matched the row and
   * every mouse click on Companies/People was swallowed as "click on an interactive
   * child". The row opened fine on Enter (a different handler) which made it look like a
   * routing bug. Exclude the row itself so only genuine controls inside it opt out.
   */
  const isInteractive = (target: EventTarget | null, row: EventTarget | null): boolean => {
    const el = target as HTMLElement | null;
    const hit = el?.closest?.(INTERACTIVE);
    return !!hit && hit !== row;
  };

  return {
    onClick: (e: MouseEvent) => {
      // Don't hijack clicks on interactive children inside the row.
      if (isInteractive(e.target, e.currentTarget)) return;
      open();
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      // Enter/Space activate the row. Other keys belong to whatever the user focused.
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    },
    tabIndex: 0,
    role: "button",
    "aria-label": label,
  };
}