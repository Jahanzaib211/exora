"""Extraction from company web pages. Pure functions over HTML, so they are unit-tested on fixtures.

What is collected: business contact details the company itself publishes on its own site (emails on its own
domain, phone links, its LinkedIn company page) and the people it lists with their titles, together with the
direct-dial number and LinkedIn profile each of those people puts on their own card. Nothing is inferred or
guessed (no email-pattern guessing), personal/free-mail addresses are discarded, and no profile is ever read
from LinkedIn itself — only from the company's page that links to it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..contact.phone import candidate as phone_candidate
from ..normalize import country_code, domain_of

PAGE_HINTS = re.compile(
    r"/(about|about-us|team|our-team|people|our-people|leadership|management|partners|attorneys|lawyers|"
    r"solicitors|staff|who-we-are|contact|contact-us)(/|$|\.)",
    re.I,
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")
ASSET_SUFFIX = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|ico)$", re.I)
TITLE_WORDS = re.compile(
    r"\b(partner|founder|co-founder|owner|director|managing|principal|chief|ceo|cfo|cto|cio|coo|ciso|president|"
    r"head of|vp|vice president|manager|counsel|solicitor|attorney|lawyer|barrister|accountant|cpa|consultant|"
    r"engineer|associate|paralegal|administrator|officer|lead|specialist|advisor|adviser|physician|doctor|dentist)\b",
    re.I,
)
NAME_RE = re.compile(r"^(?:(?:Dr|Mr|Mrs|Ms|Prof)\.?\s+)?[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-\.]+){1,3}$")
CARD_CLASS = re.compile(r"(team|member|person|people|staff|profile|bio|attorney|lawyer|partner|leader)", re.I)
# Words that make a heading a section title, not a person's name ("Our Senior Management Team", "Family Law").
NOT_A_NAME = re.compile(
    r"\b(our|the|team|management|services?|law|legal|department|group|practice|contact|about|news|insights?|"
    r"office|offices|careers|people|meet|board|senior|leadership|clients?|sectors?|expertise|solutions?|"
    r"limited|ltd|llp|llc|inc|company)\b",
    re.I,
)
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "strong")
CAREERS_RE = re.compile(r"(careers?|jobs|vacanc|join-us|join-our-team|work-with-us|recruit)", re.I)
# social network -> (host pattern, path pattern of a profile, not a share/intent link)
SOCIAL = {
    "linkedin": (r"linkedin\.com$", r"^/company/[^/]+"),
    "x": (r"(^|\.)(twitter|x)\.com$", r"^/(?!intent|share|home|search|hashtag)[A-Za-z0-9_]{2,15}/?$"),
    "facebook": (r"facebook\.com$", r"^/(?!sharer|share|dialog|plugins)[^/?]+/?$"),
    "instagram": (r"instagram\.com$", r"^/(?!p/|explore)[A-Za-z0-9_.]+/?$"),
    "youtube": (r"youtube\.com$", r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)"),
}
# Technology fingerprints found in page HTML (public markup only).
TECH = {
    "WordPress": r"/wp-content/|/wp-includes/",
    "Drupal": r"Drupal\.settings|/sites/default/files/",
    "Joomla": r"/media/jui/|Joomla!",
    "Squarespace": r"static1\.squarespace\.com|squarespace\.com/universal",
    "Wix": r"static\.wixstatic\.com",
    "Webflow": r"data-wf-page|assets\.website-files\.com",
    "Shopify": r"cdn\.shopify\.com",
    "HubSpot": r"js\.hs-scripts\.com|js\.hsforms\.net|js\.hs-analytics\.net",
    "Salesforce Pardot": r"pi\.pardot\.com",
    "Marketo": r"munchkin\.marketo\.net",
    "Google Tag Manager": r"googletagmanager\.com/gtm\.js",
    "Google Analytics": r"google-analytics\.com/|googletagmanager\.com/gtag/js",
    "Intercom": r"widget\.intercom\.io",
    "Zendesk": r"static\.zdassets\.com",
    "Drift": r"js\.driftt\.com",
    "Calendly": r"assets\.calendly\.com",
    "Cloudflare": r"/cdn-cgi/",
    "Microsoft Clarity": r"www\.clarity\.ms/tag",
    "Tawk.to live chat": r"embed\.tawk\.to",
    "LiveChat": r"cdn\.livechatinc\.com",
}
# Buying signals for ALI's offers, matched on the page's visible text (or links for WhatsApp/booking).
MENTIONS = {
    "AI adoption": r"\b(artificial intelligence|generative ai|gen ?ai|chatgpt|copilot|large language models?|"
    r"machine learning)\b",
    # cookie banners say "GDPR" everywhere; only count a named DPO, PDPL, or an explicit compliance claim
    "Data protection": r"\b(data protection officer|pdpl|gdpr[- ]compliant|uk gdpr compliance)\b",
    "Security certification": r"\b(iso ?(/ ?iec ?)?27001|cyber essentials( plus)?|soc ?2|hitrust)\b",
    "Healthcare compliance": r"\b(hipaa|cqc[- ]registered|dspt)\b",
    "Online booking": r"\b(book (an |a |your )?(appointment|consultation)|online booking)\b",
}
ARABIC = re.compile(r"[؀-ۿ]{4,}(\s+[؀-ۿ]{2,}){3,}")
WHATSAPP = re.compile(r"(wa\.me/|api\.whatsapp\.com/send|web\.whatsapp\.com/send)", re.I)
ATS_PATTERNS = {
    "greenhouse": re.compile(
        r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I
    ),
    "lever": re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I),
}
ATS_IGNORE = {"embed", "js", "api", "v1", "jobs", "careers", "search"}
ORG_TYPES = {
    "Organization", "Corporation", "LocalBusiness", "LegalService", "Attorney", "AccountingService",
    "ProfessionalService", "MedicalClinic", "MedicalOrganization", "Dentist", "Physician", "Hospital",
}  # fmt: skip


def _is_name(text: str) -> bool:
    return (
        bool(NAME_RE.match(text)) and len(text) <= 60 and not TITLE_WORDS.search(text) and not NOT_A_NAME.search(text)
    )


@dataclass
class FoundPerson:
    name: str
    title: str | None
    email: str | None
    url: str
    # Published by the company on the page this person was found on; None when absent.
    phone: str | None = None
    linkedin_url: str | None = None


@dataclass
class PageFindings:
    url: str
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    linkedin: set[str] = field(default_factory=set)
    people: list[FoundPerson] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    tech: set[str] = field(default_factory=set)
    description: str | None = None
    site_name: str | None = None
    address: str | None = None
    careers_url: str | None = None
    mentions: dict[str, str] = field(default_factory=dict)  # label -> page URL where it appears
    ats: tuple[str, str] | None = None  # (provider, board token) of a public job board
    title: str | None = None  # <title> of the page, for receipts
    text: str = ""  # visible text, for receipt excerpts


PHONE_LIKE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def phone_context(text: str, digits: str, width: int = 90) -> str | None:
    """Context for a phone number written in any format on the page ('0161 832 1234' for +441618321234)."""
    want = re.sub(r"\D", "", digits)[-7:]
    if len(want) < 7 or not text:
        return None
    for m in PHONE_LIKE.finditer(text):
        if re.sub(r"\D", "", m.group())[-7:] == want:
            return context(text, m.group(), width)
    return None


def context(text: str, needle: str, width: int = 90) -> str | None:
    """The words around `needle` in `text` (case-insensitive), for a receipt. None if not found."""
    if not text or not needle:
        return None
    i = text.lower().find(needle.lower())
    if i < 0:
        return None
    start, end = max(0, i - width), min(len(text), i + len(needle) + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _rank(url: str) -> int:
    # team/people pages first: they carry named contacts; contact pages next
    if re.search(r"team|people|partner|leader|attorney|lawyer|solicitor|staff", url, re.I):
        return 0
    return 1 if re.search(r"contact", url, re.I) else 2


def filter_candidates(urls: list[str], base: str, limit: int) -> list[str]:
    """Same-site URLs whose path looks like an about/team/contact page, ranked, deduplicated."""
    site = domain_of(base)
    out: list[str] = []
    for href in urls:
        p = urlparse(href)
        if p.scheme not in ("http", "https") or domain_of(href) != site:
            continue
        if PAGE_HINTS.search(p.path) and href.rstrip("/") != base.rstrip("/") and href not in out:
            out.append(href)
    out.sort(key=_rank)
    return out[:limit]


def candidate_links(html: str, base: str, limit: int = 6) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = [urljoin(base, a["href"].split("#")[0].strip()) for a in soup.find_all("a", href=True)]
    return filter_candidates(urls, base, limit)


def sitemap_locs(xml: str) -> list[str]:
    """<loc> entries from a sitemap or sitemap index (regex: robust to namespaces and minor XML errors)."""
    return [m.strip() for m in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.I)][:5000]


def _clean_email(raw: str, site: str | None) -> str | None:
    e = raw.strip().strip(".").lower()
    if not EMAIL_RE.fullmatch(e) or ASSET_SUFFIX.search(e):
        return None
    return e if site and domain_of(e) == site else None


# `/in/<slug>` is a personal profile; `/company/` is the organisation (already kept as a
# company social), and `share`, `post` and `?shareArticle=` are sharing links.
LINKEDIN_PROFILE = re.compile(r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/in/[^/?#]+", re.I)


def _linkedin_profile(href: str) -> str | None:
    """A personal profile URL, read only from a page the company publishes itself.

    LinkedIn's User Agreement forbids collecting member data from linkedin.com, so nothing
    here is ever fetched from LinkedIn: this accepts a `/in/` link found on the company's own
    site (which we already crawl under robots.txt) and rejects share/intent links, which
    identify no one. Callers scope it to a person's own card so the link cannot belong to a
    colleague.
    """
    m = LINKEDIN_PROFILE.match(href.strip())
    return m.group(0).rstrip("/") if m else None


def _jsonld_people(soup: BeautifulSoup, url: str, site: str | None) -> list[FoundPerson]:
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@type") == "Person" and isinstance(node.get("name"), str):
                    email = _clean_email(str(node.get("email", "")).replace("mailto:", ""), site)
                    tel = node.get("telephone")
                    phone = phone_candidate(str(tel)) if isinstance(tel, str) else None
                    linkedin = None
                    same = node.get("sameAs") or []
                    for link in [same] if isinstance(same, str) else same:
                        linkedin = _linkedin_profile(str(link))
                        if linkedin:
                            break
                    out.append(FoundPerson(node["name"].strip(), node.get("jobTitle"), email, url, phone, linkedin))
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return out


def _text(el: Tag) -> str:
    return " ".join(el.get_text(" ", strip=True).split())


def _card_people(soup: BeautifulSoup, url: str, site: str | None) -> list[FoundPerson]:
    """Team cards: a short heading that looks like a name, followed closely by a title-like line."""
    out: list[FoundPerson] = []
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "strong"]):
        name = _text(heading)
        if not _is_name(name):
            continue
        container = heading.find_parent(class_=CARD_CLASS) or heading.parent
        title = None
        for sib in heading.find_all_next(["p", "span", "div", "h4", "h5", "h6", "em", *HEADING_TAGS], limit=8):
            if isinstance(container, Tag) and container not in sib.parents:
                break  # left this person's card
            t = _text(sib)
            if not t or t == name or heading in sib.parents or sib in heading.parents:
                continue
            if sib.name in HEADING_TAGS and _is_name(t):
                break  # reached the next person's card
            if len(t) <= 100 and TITLE_WORDS.search(t) and not _is_name(" ".join(t.split()[:2])):
                title = t
                break
        if not title:
            continue
        # Contact details are read from this person's own card only — a `tel:` or profile
        # link outside it would belong to a colleague and be stored against the wrong name.
        email = phone = linkedin = None
        if isinstance(container, Tag):
            mail = container.select_one('a[href^="mailto:"]')
            if mail:
                email = _clean_email(mail["href"][7:].split("?")[0], site)
            tel = container.select_one('a[href^="tel:"]')
            if tel:
                phone = phone_candidate(tel["href"][4:])
            li = container.select_one('a[href*="linkedin.com/in/"]')
            if li:
                linkedin = _linkedin_profile(li["href"])
        out.append(FoundPerson(name, title, email, url, phone, linkedin))
    return out


INVISIBLE = {"script", "style", "noscript", "template", "svg", "head", "title", "meta"}


def _visible_text(soup: BeautifulSoup) -> str:
    """Text a visitor can read: excludes scripts, styles and cookie-consent code that mention 'GDPR' etc."""
    parts = []
    for s in soup.find_all(string=True):
        if (
            s.parent is not None
            and s.parent.name not in INVISIBLE
            and not any(p.name in INVISIBLE for p in s.parents if p is not None)
        ):
            parts.append(s)
    return " ".join(" ".join(parts).split())


def _social_of(href: str) -> tuple[str, str] | None:
    p = urlparse(href)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    for network, (host_re, path_re) in SOCIAL.items():
        if re.search(host_re, host) and re.match(path_re, p.path or "/"):
            return network, f"https://{host}{p.path.rstrip('/')}"
    return None


def _postal(addr) -> str | None:
    if isinstance(addr, str):
        return addr.strip() or None
    if isinstance(addr, dict):
        parts = [addr.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts.append(country)
        return ", ".join(str(x).strip() for x in parts if x) or None
    if isinstance(addr, list) and addr:
        return _postal(addr[0])
    return None


def _country_of(addr) -> str | None:
    """ISO country of a schema.org postal address, so a local-format phone on the page can be judged."""
    if not isinstance(addr, dict):
        return None
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    return country_code(str(country)) if country else None


def _jsonld_org(soup: BeautifulSoup, f: PageFindings, site: str | None) -> None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            types = set(types) if isinstance(types, list) else {types}
            if types & ORG_TYPES:
                if isinstance(node.get("name"), str) and not f.site_name:
                    f.site_name = node["name"].strip()[:300]
                if isinstance(node.get("description"), str) and not f.description:
                    f.description = " ".join(node["description"].split())[:1000]
                addr = node.get("address")
                f.address = f.address or _postal(addr)
                region = _country_of(addr)
                tel = node.get("telephone")
                if isinstance(tel, str):
                    # judged offline (possible/valid, international vs local format); junk is dropped here
                    cleaned = phone_candidate(tel, region)
                    if cleaned:
                        f.phones.add(cleaned)
                email = _clean_email(str(node.get("email", "")).replace("mailto:", ""), site)
                if email:
                    f.emails.add(email)
                same = node.get("sameAs") or []
                for link in [same] if isinstance(same, str) else same:
                    hit = _social_of(str(link))
                    if hit:
                        f.socials.setdefault(*hit)
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))


def extract_page(html: str, url: str) -> PageFindings:
    soup = BeautifulSoup(html, "lxml")
    site = domain_of(url)
    f = PageFindings(url=url)
    if soup.title and soup.title.string:
        f.title = " ".join(soup.title.string.split())[:300]
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        f.description = " ".join(meta["content"].split())[:1000] or None
    og_name = soup.find("meta", attrs={"property": "og:site_name"})
    if og_name and og_name.get("content"):
        f.site_name = og_name["content"].strip()[:300]
    _jsonld_org(soup, f, site)
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        hit = _social_of(href)
        if hit:
            f.socials.setdefault(*hit)
        elif (
            not f.careers_url
            and domain_of(href) == site
            and (CAREERS_RE.search(urlparse(href).path) or CAREERS_RE.search(_text(a)[:40]))
        ):
            f.careers_url = href.split("#")[0]
    for name, pattern in TECH.items():
        if re.search(pattern, html, re.I):
            f.tech.add(name)
    for provider, pattern in ATS_PATTERNS.items():
        m = pattern.search(html)
        if m and m.group(1).lower() not in ATS_IGNORE:
            f.ats = (provider, m.group(1))
            break
    visible = _visible_text(soup)
    f.text = visible
    for label, pattern in MENTIONS.items():
        if re.search(pattern, visible, re.I):
            f.mentions[label] = url
    if ARABIC.search(visible):
        f.mentions["Arabic-language content"] = url
    if WHATSAPP.search(html):
        f.mentions["WhatsApp channel"] = url
    for a in soup.select('a[href^="mailto:"]'):
        e = _clean_email(a["href"][7:].split("?")[0], site)
        if e:
            f.emails.add(e)
    for m in EMAIL_RE.findall(visible):
        e = _clean_email(m, site)
        if e:
            f.emails.add(e)
    for a in soup.select('a[href^="tel:"]'):
        cleaned = phone_candidate(a["href"][4:])
        if cleaned:
            f.phones.add(cleaned)
    for a in soup.select('a[href*="linkedin.com/company/"]'):
        f.linkedin.add(a["href"].split("?")[0].rstrip("/"))
    seen: set[str] = set()
    for p in _jsonld_people(soup, url, site) + _card_people(soup, url, site):
        key = p.name.lower()
        if key not in seen:
            seen.add(key)
            f.people.append(p)
            if p.email:
                f.emails.add(p.email)
    return f
