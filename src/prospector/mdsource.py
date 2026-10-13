"""Parse 09_30_Day_Execution_Plan_and_KPIs.md: the markdown file is the source of truth for the plan.

Only content comes from the file (titles, dates, targets, thresholds, assumptions). Team state (task status,
routine ticks, notes, calls) lives in the database and is kept across re-syncs, matched by stable keys.
The only things defined in code are how each KPI/funnel row maps to a live measurement, and event metadata
the doc does not state (country, which sequences an event suits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MONTHS = {
    m: i
    for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)
}
DASH = r"[–—-]"


class PlanParseError(ValueError):
    pass


@dataclass
class TaskSpec:
    key: str
    track: str
    title: str


@dataclass
class WeekSpec:
    label: str
    start: date
    end: date
    tasks: list[TaskSpec]
    targets: dict
    focus: str = ""


@dataclass
class KpiSpec:
    key: str
    name: str
    unit: str
    direction: str
    target: float
    threshold: float | None
    min_sample: int
    action: str
    source: str
    raw_target: str
    raw_threshold: str


@dataclass
class FunnelSpec:
    key: str
    name: str
    assumption: str
    source: str
    plan_low: float
    plan_high: float
    is_assumption: bool
    raw: str


@dataclass
class RoutineSpec:
    id: int
    minutes: int
    title: str
    live_counter: str | None
    detail: str = ""


@dataclass
class EventSpec:
    name: str
    city: str
    start: date
    end: date
    country: str = ""
    segments: list[str] = field(default_factory=list)


@dataclass
class PlanSpec:
    path: Path
    campaign_start: date
    weeks: list[WeekSpec]
    kpis: list[KpiSpec]
    funnel: list[FunnelSpec]
    routine: list[RoutineSpec]
    events: list[EventSpec]
    outcome: str


# ---- mapping rows of the doc to live measurements (code, not content) -------------------------------
KPI_MEASURES = [
    # (pattern in KPI name, key, source)
    (r"linkedin acceptance", "li_acceptance", "engine"),
    (r"email reply rate", "email_reply_rate", "engine"),
    (r"hard bounce", "hard_bounce", "engine"),
    (r"spam rate", "spam_rate", "manual"),
    (r"replies\s*→\s*calls|replies -> calls", "replies_to_calls", "calls"),
    (r"calls\s*→\s*pilot|calls -> pilot", "calls_to_pilot", "calls"),
    (r"time to reply", "reply_time", "engine"),
]
KPI_MIN_SAMPLE_DEFAULT = {
    "li_acceptance": 20,
    "hard_bounce": 5,
    "replies_to_calls": 5,
    "calls_to_pilot": 5,
    "reply_time": 1,
}
FUNNEL_MEASURES = [
    (r"linkedin requests", "li_requests"),
    (r"^accepted", "li_accepted"),
    (r"reply to dms", "li_replies"),
    (r"cold emails", "emails_sent"),
    (r"email replies", "email_replies"),
    (r"conversations", "conversations"),
    (r"discovery calls", "calls"),
    (r"pilots", "pilots"),
]
ROUTINE_COUNTERS = [
    (r"inbound repl", "open_replies"),
    (r"linkedin|connection requests", "linkedin_due"),
    (r"queue|bounces|suppression", "pending_approvals"),
    (r"tomorrow", "due_tomorrow"),
]
EVENT_META = {
    # city -> country; name pattern -> sequences
    "cities": {
        "riyadh": "SA",
        "nuremberg": "DE",
        "london": "GB",
        "lisbon": "PT",
        "dubai": "AE",
        "ny": "US",
        "new york": "US",
    },
    "segments": [
        (r"health", ["SEQ-HEALTH", "SEQ-KSA"]),
        (r"it-sa", ["SEQ-PARTNER", "SEQ-EU"]),
        (r"legal", ["SEQ-LAW"]),
        (r"web summit", ["SEQ-STARTUP", "SEQ-EU"]),
        (r"black hat", ["SEQ-KSA", "SEQ-PARTNER"]),
        (r"gitex", ["SEQ-KSA", "SEQ-PARTNER", "SEQ-STARTUP"]),
        (r"leap", ["SEQ-KSA", "SEQ-STARTUP"]),
    ],
}


# ---- helpers ----------------------------------------------------------------------------------------
def strip_md(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", strip_md(text).lower()).strip("-")[:80]


def sections(md: str) -> dict[str, str]:
    """'## Heading' -> body text (until the next ## heading)."""
    out: dict[str, str] = {}
    parts = re.split(r"^##\s+(.+?)\s*$", md, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        out[parts[i].strip()] = parts[i + 1]
    return out


def find_section(secs: dict[str, str], prefix: str) -> str:
    for title, body in secs.items():
        if title.lower().startswith(prefix.lower()):
            return body
    raise PlanParseError(f"section '## {prefix}...' not found")


def table(body: str) -> list[dict[str, str]]:
    rows = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        raise PlanParseError("expected a markdown table")
    header = [strip_md(c) for c in rows[0].strip("|").split("|")]
    out = []
    for ln in rows[2:]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        out.append(dict(zip(header, cells, strict=False)))
    return out


def parse_day_range(text: str, year: int) -> tuple[date, date]:
    """'5–9 Oct', '26–29 Oct 2026', '1–3 Mar 2027' -> (start, end)."""
    m = re.search(rf"(\d{{1,2}})\s*{DASH}\s*(\d{{1,2}})\s+([A-Za-z]{{3}})[a-z]*\.?(?:\s+(\d{{4}}))?", text)
    if not m:
        raise PlanParseError(f"cannot read a date range from {text!r}")
    month = MONTHS[m.group(3).lower()]
    y = int(m.group(4)) if m.group(4) else year
    return date(y, month, int(m.group(1))), date(y, month, int(m.group(2)))


def split_sentences(cell: str) -> list[str]:
    cell = strip_md(cell)
    # Split on '. ' only where the next character starts a new sentence (not "02 §4", "No.").
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", cell)
    return [p.strip().rstrip(".") for p in parts if p.strip()]


def numbers(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]


# ---- parsers ----------------------------------------------------------------------------------------
def parse_weeks(body: str, year: int) -> list[WeekSpec]:
    weeks = []
    for row in table(body):
        week_cell = row.get("Week", "")
        label_m = re.search(r"W(\d+)", week_cell)
        if not label_m:
            continue
        label = f"W{label_m.group(1)}"
        start, end = parse_day_range(week_cell, year)
        tasks: list[TaskSpec] = []
        targets: dict = {}
        for track_col, track in (("Build", "build"), ("Outreach", "outreach"), ("Content", "content")):
            for sentence in split_sentences(row.get(track_col, "")):
                tasks.append(TaskSpec(key=f"{label}:{track}:{slug(sentence)}", track=track, title=sentence))
                low = sentence.lower()
                if "linkedin" in low:
                    m = re.search(r"\+\s*(\d+)|≈\s*(\d+)", sentence)
                    if m:
                        targets["linkedin_requests"] = int(m.group(1) or m.group(2))
                if low.startswith("email") or "/inbox/day" in low:
                    m = re.search(rf"(\d+)\s*{DASH}\s*(\d+)\s*/\s*inbox", sentence)
                    if m:
                        targets["emails_per_inbox_day"] = [int(m.group(1)), int(m.group(2))]
        weeks.append(WeekSpec(label=label, start=start, end=end, tasks=tasks, targets=targets))
    if not weeks:
        raise PlanParseError("no W1..Wn rows in the week-by-week table")
    return weeks


def parse_kpis(body: str) -> list[KpiSpec]:
    out = []
    for row in table(body):
        name = strip_md(row.get("KPI", ""))
        target_raw = strip_md(row.get("Target", ""))
        thr_raw = strip_md(row.get("Kill / fix threshold", row.get("Threshold", "")))
        key, source = None, "manual"
        for pat, k, src in KPI_MEASURES:
            if re.search(pat, name, re.IGNORECASE):
                key, source = k, src
                break
        key = key or slug(name)
        tnums = numbers(target_raw)
        if not tnums:
            raise PlanParseError(f"KPI {name!r}: no target number in {target_raw!r}")
        direction = "higher" if re.search(r"≥|>=|>", target_raw) else "lower"
        unit = "hours" if "hour" in target_raw.lower() else ("pct" if "%" in target_raw else "count")
        threshold, action, min_sample = None, "", KPI_MIN_SAMPLE_DEFAULT.get(key, 0)
        if thr_raw and thr_raw not in {"—", "-"}:
            cond, _, action = thr_raw.partition("→")
            cond_nums = numbers(cond)
            if cond_nums:
                threshold = cond_nums[0]
            m = re.search(r"after\s+(\d+)\s+sends?", cond)
            if m:
                min_sample = int(m.group(1))
            action = action.strip().rstrip(".")
            action = action[:1].upper() + action[1:]
        out.append(
            KpiSpec(key, name, unit, direction, tnums[0], threshold, min_sample, action, source, target_raw, thr_raw)
        )
    return out


def parse_funnel(body: str) -> list[FunnelSpec]:
    out = []
    for row in table(body):
        name = strip_md(row.get("Stage", ""))
        key = next((k for pat, k in FUNNEL_MEASURES if re.search(pat, name, re.IGNORECASE)), slug(name))
        raw = strip_md(row.get("30 days", ""))
        m = re.search(rf"~?\s*([\d,]+)(?:\s*{DASH}\s*([\d,]+))?", raw)
        if not m:
            raise PlanParseError(f"funnel stage {name!r}: no number in {raw!r}")
        low = float(m.group(1).replace(",", ""))
        high = float(m.group(2).replace(",", "")) if m.group(2) else low
        assumption = strip_md(row.get("Assumption", ""))
        out.append(
            FunnelSpec(
                key=key,
                name=name,
                assumption=assumption,
                source=strip_md(row.get("Source", "")),
                plan_low=low,
                plan_high=high,
                is_assumption="my assumption" in assumption.lower(),
                raw=raw,
            )
        )
    return out


def parse_routine(body: str) -> list[RoutineSpec]:
    out = []
    for m in re.finditer(r"^\s*(\d+)\.\s+\*\*(\d+)\s*min:?\*\*:?\s*(.+)$", body, re.MULTILINE):
        title = strip_md(m.group(3)).rstrip(".")
        detail = ""
        # "(reply playbook in 03)" is a pointer to another document: show it as a note, not in the title
        ref = re.search(r"\s*\(([^)]*\b(?:in|see)\s+(\d{2})\b[^)]*)\)", title)
        if ref:
            title = (title[: ref.start()] + title[ref.end() :]).strip()
            detail = f"See document {ref.group(2)}: {ref.group(1).rsplit(' in ', 1)[0].strip()}."
        counter = next((c for pat, c in ROUTINE_COUNTERS if re.search(pat, title, re.IGNORECASE)), None)
        out.append(
            RoutineSpec(id=int(m.group(1)), minutes=int(m.group(2)), title=title, live_counter=counter, detail=detail)
        )
    if not out:
        raise PlanParseError("no '1. **15 min:** ...' items in the daily routine")
    return out


def parse_events(body: str) -> list[EventSpec]:
    out = []
    # "- Name, City: 26–29 Oct 2026" or "- Legalweek NY: 1–3 Mar 2027" (city inside the name)
    for m in re.finditer(r"^\s*-\s+(.+?)(?:,\s*([^:]+))?:\s*(.+)$", body, re.MULTILINE):
        name, city, when = strip_md(m.group(1)), strip_md(m.group(2) or ""), m.group(3)
        start, end = parse_day_range(when, 0)
        cities = EVENT_META["cities"]
        if not city:
            city = next(
                (c.upper() if len(c) <= 2 else c.title() for c in cities if re.search(rf"\b{c}\b", name, re.I)), ""
            )
        country = cities.get(city.lower(), "")
        segments = next((s for pat, s in EVENT_META["segments"] if re.search(pat, name, re.IGNORECASE)), [])
        out.append(EventSpec(name=name, city=city, start=start, end=end, country=country, segments=segments))
    return out


def parse_plan(path: Path) -> PlanSpec:
    md = Path(path).read_text(encoding="utf-8-sig")
    m = re.search(r"Start date:\s*\*\*\s*\w*\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", md)
    if not m:
        raise PlanParseError("'Start date: **Mon 5 Oct 2026**' line not found")
    start = date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    secs = sections(md)
    funnel_body = find_section(secs, "Funnel")
    outcome = re.search(r"\*\*Realistic month-1 outcome:\*\*\s*(.+)", funnel_body)
    return PlanSpec(
        path=Path(path),
        campaign_start=start,
        weeks=parse_weeks(find_section(secs, "Week-by-week"), start.year),
        kpis=parse_kpis(find_section(secs, "KPIs")),
        funnel=parse_funnel(funnel_body),
        routine=parse_routine(find_section(secs, "Daily")),
        events=parse_events(find_section(secs, "Events")),
        outcome=strip_md(outcome.group(1)) if outcome else "",
    )
