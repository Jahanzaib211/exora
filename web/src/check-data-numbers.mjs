#!/usr/bin/env node
// Guard: no hardcoded display numbers in web/src.
//
// Every number the user sees must come from the API and stream live (see
// src/live.ts for the refresh policy). This script flags numeric literals that
// are rendered as data, so they cannot creep back in. Run with:
//
//   npm run check:data
//
// Structural values are ALLOWED and never flagged: layout sizes (width, span,
// gutter, level, size), bounds (min, max, step), page sizes (pageSize), grid
// breakpoints (xs..xxl), SVG/chart geometry (strokeWidth, r, cx, ...), polling
// intervals (REFRESH.* constants), hex colours, CSS values (width: "100%"),
// time-window control labels ("Last 24 hours", "Next 7 days") and
// dates/identifiers glued to words ("30-day", "month-1", "L0038", "00_README...").
//
// Rules:
//   1. jsx-attr   a JSX attribute with a bare numeric value that is not on the
//                 structural allow-list, e.g. <Statistic value={42} />
//   2. jsx-expr   a bare numeric JSX expression, e.g. <b>{42}</b>
//   3. jsx-text   a number in JSX text, e.g. <span>42 companies</span>
//   4. string     a quoted string that presents a number with a data unit,
//                 e.g. "42 companies", "in 5 days", "Conversion was 90%"
//
// Known limit: a number stashed in a const/variable and rendered as {SOME_CONST}
// is not caught by a regex scanner — keep data numbers in the API layer.

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = join(SRC, "..");
const rel = (p) => relative(APP_ROOT, p).replace(/\\/g, "/");

// Attributes whose numeric values are structural, never data.
const STRUCTURAL_ATTRS = new Set([
  "xs", "sm", "md", "lg", "xl", "xxl",
  "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
  "size", "span", "column", "gutter", "min", "max", "step", "level",
  "pageSize", "collapsedWidth", "maxLength",
  // Loading-placeholder geometry (how many skeleton rows to draw).
  "skeletonRows",
  // Overlay sizing / badge truncation limits.
  "popupMatchSelectWidth", "overflowCount", "maxTagCount", "bodyStyle",
  // SVG/chart drawing geometry (axis ticks, line weights, point radii).
  "strokeWidth", "points", "opacity", "cx", "cy", "x", "x1", "x2", "y", "y1", "y2", "r",
]);

// A number right after "Last "/"Next "... labels a fixed time window of a
// control ("Last 24 hours", "Next 7 days"), not a measured value.
const WINDOW_LABEL = /(?:^|\b(?:last|next|past|this|upcoming))\s+$/i;

// Units that make a literal number read as data.
const DATA_UNITS =
  "%|\\b(?:percent|points|score|companies|people|leads|contacts|calls|emails|replies|drafts|tasks|days|day|hours|hour|hrs|minutes|minute|msgs|messages|USD|EUR|GBP)";

function listSources(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listSources(p));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(p);
  }
  return out;
}

const lineOf = (src, index) => src.slice(0, index).split("\n").length;
const excerpt = (line) => line.trim().slice(0, 120);

/**
 * Strip comments before scanning.
 *
 * The scanner works on raw source text, so a JSDoc example like
 * `pluralise(3,'company') -> '3 companies'` was reported as a hardcoded display
 * number even though nothing renders it. Comments are replaced with spaces so
 * every index still maps to the original line/column.
 */
function stripComments(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const next = src[i + 1];
    if (c === "/" && next === "/") {
      // Line comment: blank to end of line, keep the newline.
      while (i < n && src[i] !== "\n") {
        out += " ";
        i += 1;
      }
      continue;
    }
    if (c === "/" && next === "*") {
      // Block comment: blank through the closing delimiter, keep newlines.
      out += "  ";
      i += 2;
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) {
        out += src[i] === "\n" ? "\n" : " ";
        i += 1;
      }
      if (i < n) {
        out += "  ";
        i += 2;
      }
      continue;
    }
    // Skip string/template literals so a "//" inside a URL isn't treated as a comment.
    if (c === '"' || c === "'" || c === "`") {
      const quote = c;
      out += c;
      i += 1;
      while (i < n && src[i] !== quote) {
        if (src[i] === "\\") {
          out += src[i] ?? "";
          i += 1;
        }
        if (i < n) {
          out += src[i];
          i += 1;
        }
      }
      if (i < n) {
        out += quote;
        i += 1;
      }
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

function checkFile(file) {
  const raw = readFileSync(file, "utf8");
  const src = stripComments(raw);
  const hits = [];
  const add = (index, rule, why) =>
    hits.push({
      file: rel(file),
      line: lineOf(src, index),
      rule,
      why,
      src: excerpt(raw.split("\n")[lineOf(src, index) - 1] ?? ""),
    });

  // Rule 1: non-structural JSX attribute with a literal number, e.g. value={42}
  const attrRe = /(^|[\s(<])([A-Za-z_][\w]*)=\{(-?\d+(?:\.\d+)?)\}/g;
  for (let m = attrRe.exec(src); m; m = attrRe.exec(src)) {
    const attr = m[2];
    if (STRUCTURAL_ATTRS.has(attr)) continue;
    add(m.index, "jsx-attr", `${attr}={${m[3]}}`);
  }

  // Rule 2: bare numeric JSX expression, e.g. <b>{42}</b>
  const exprRe = /[^=.\w]\{\s*(-?\d+(?:\.\d+)?)\s*\}/g;
  for (let m = exprRe.exec(src); m; m = exprRe.exec(src)) {
    add(m.index, "jsx-expr", `{${m[1]}}`);
  }

  // Rule 3: number in JSX text, e.g. <span>42 companies</span>.
  // The text must sit between a tag close and a tag open ("<" + letter or "/"),
  // and must not contain code-ish characters; numbers glued to a word
  // ("month-1", "30-day") are labels, not data.
  const textRe = />[^<>{}()=;?&|]{0,200}?<(?=[A-Za-z/])/g;
  const textNum = /(?<![-\w.])\d+(?:\.\d+)?(?![\w-])/;
  for (let m = textRe.exec(src); m; m = textRe.exec(src)) {
    const body = m[0].slice(1, -1);
    if (textNum.test(body)) add(m.index, "jsx-text", body.trim().slice(0, 60));
  }

  // Rule 4: quoted string that presents a number with a data unit.
  const strRe = /"[^"\n]*"|'[^'\n]*'|`[^`]*`/g;
  for (let m = strRe.exec(src); m; m = strRe.exec(src)) {
    const quote = m[0][0];
    let body = m[0].slice(1, -1);
    if (quote === "`") body = body.replace(/\$\{[^}]*\}/g, "");
    // Pure CSS values such as width: "100%" are structural.
    if (/^[\d.]+(%|px|vw|vh|em|rem)$/.test(body)) {
      const before = src.slice(Math.max(0, m.index - 40), m.index);
      if (/[A-Za-z]\s*:\s*$/.test(before)) continue;
    }
    const numRe = new RegExp(`(?<![-\\w.$/])(\\d+(?:\\.\\d+)?)(?![\\w-])\\s*(${DATA_UNITS})`, "gi");
    for (let n = numRe.exec(body); n; n = numRe.exec(body)) {
      if (WINDOW_LABEL.test(body.slice(0, n.index))) continue;
      add(m.index, "string", m[0].slice(0, 60));
      break;
    }
  }

  return hits;
}

// Secondary check: the central live-refresh policy must stay wired up.
function checkWiring() {
  const hits = [];
  const app = readFileSync(join(SRC, "App.tsx"), "utf8");
  const live = readFileSync(join(SRC, "live.ts"), "utf8");
  if (!app.includes("reactQuery:") || !app.includes("liveQueryClientConfig")) {
    hits.push({ file: "src/App.tsx", line: 1, rule: "wiring", why: "App.tsx must pass reactQuery: { clientConfig: liveQueryClientConfig } to <Refine>" });
  }
  if (!live.includes("refetchInterval:") || !live.includes("refetchIntervalInBackground: false")) {
    hits.push({ file: "src/live.ts", line: 1, rule: "wiring", why: "live.ts must keep the default refetchInterval and refetchIntervalInBackground: false" });
  }
  return hits;
}

const files = listSources(SRC);
const violations = files.flatMap(checkFile).concat(checkWiring());

if (violations.length) {
  console.error(`check-data-numbers: ${violations.length} problem(s) in ${rel(SRC)}:\n`);
  for (const v of violations) {
    console.error(`  ${v.file}:${v.line}  [${v.rule}] ${v.why}`);
    if (v.src) console.error(`      ${v.src}`);
  }
  console.error(
    "\nDisplay numbers must come from the API (src/live.ts owns refresh policy)." +
      "\nStructural values (width, min/max, step, pageSize, breakpoints, hex colours, CSS) are allowed.",
  );
  process.exit(1);
}

console.log(`check-data-numbers: OK — ${files.length} files scanned, no hardcoded display numbers; live-refresh policy wired.`);
