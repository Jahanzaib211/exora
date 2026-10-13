"""Scrapy crawl of a batch of company websites. Runs in its own process:

    python -m prospector.crawl.spider <input.json >output.jsonl.gz

input:  [{"company_id": 1, "website": "https://example.com/"}, ...]
output: gzip JSONL, one line per fetched page: {"company_id", "url", "html"} (html capped),
        plus a final {"summary": ...} line. Decompress with `python -m gzip -d`.

The transport is a pair of pipes, deliberately. Writing `out.jsonl` into a temp directory handed the
scraped HTML to the real-time scanner, which flagged it often enough that the parent's read failed
with ERROR_VIRUS_INFECTED (errno 22) - reported as "could not read crawler output", retried and
never recovered, because the block lasts as long as the detection. An anonymous pipe is not a file,
so there is nothing to scan, lock or delete underneath us.

Pages per company: the home page, then about/team/contact pages found in its links and in its sitemap
(sitemap.xml, one level of sitemap index), up to MAX_PAGES. robots.txt is obeyed, one request at a time per
domain, 2 s apart. Scrapy's reactor cannot be restarted inside a long-lived server, so each batch gets a fresh
process; this also bounds memory and isolates crashes.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
from collections import defaultdict
from urllib.parse import urljoin

import scrapy
from scrapy.crawler import CrawlerProcess

from ..connectors.base import user_agent
from .extract import candidate_links, filter_candidates, sitemap_locs

MAX_HTML = 600_000
MAX_PAGES = 10  # per company, including the home page
MAX_SITEMAPS = 3  # child sitemaps followed from a sitemap index


def main() -> None:
    """Read targets from stdin, write gzip JSONL to stdout."""
    targets = json.load(sys.stdin)
    # Isolate the payload stream before anything can write to it: dup fd 1 aside, then point the
    # original at stderr so a library that prints joins the log rather than the middle of a result
    # line. Without this a single stray `print` silently corrupts the batch instead of failing loudly.
    payload = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    try:
        # `with` gives the exit order: TextIOWrapper flushes the text, GzipFile writes the trailer,
        # and a partial batch still decompresses - which is what lets the parent salvage pages from
        # a crawl that died part way through.
        with (
            gzip.GzipFile(fileobj=payload, mode="wb") as gz,
            io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as out,
        ):
            _crawl(targets, out)
    finally:
        payload.close()


def _crawl(targets: list[dict], out) -> None:
    stats = {"pages": 0, "errors": 0, "sitemaps": 0}
    scheduled: dict[int, set[str]] = defaultdict(set)

    class Sites(scrapy.Spider):
        name = "company-sites"

        async def start(self):
            for t in targets:
                cid, home = t["company_id"], t["website"]
                scheduled[cid].add(home.rstrip("/"))
                yield scrapy.Request(home, callback=self.home, errback=self.err, cb_kwargs={"cid": cid, "home": home})

        def _emit(self, cid: int, response) -> None:
            ctype = response.headers.get("Content-Type", b"").decode().lower()
            if ctype and "html" not in ctype:
                return
            out.write(json.dumps({"company_id": cid, "url": response.url, "html": response.text[:MAX_HTML]}) + "\n")
            out.flush()
            stats["pages"] += 1

        def _follow(self, cid: int, urls: list[str], home: str):
            for link in urls:
                key = link.rstrip("/")
                if key in scheduled[cid] or len(scheduled[cid]) >= MAX_PAGES:
                    continue
                scheduled[cid].add(key)
                yield scrapy.Request(link, callback=self.sub, errback=self.err, cb_kwargs={"cid": cid})

        def home(self, response, cid: int, home: str):
            self._emit(cid, response)
            yield from self._follow(cid, candidate_links(response.text, response.url, MAX_PAGES), response.url)
            yield scrapy.Request(
                urljoin(response.url, "/sitemap.xml"),
                callback=self.sitemap,
                errback=self.quiet,
                cb_kwargs={"cid": cid, "home": response.url, "depth": 0},
            )

        def sitemap(self, response, cid: int, home: str, depth: int):
            stats["sitemaps"] += 1
            locs = sitemap_locs(response.text)
            children = [u for u in locs if u.lower().endswith(".xml")]
            pages = [u for u in locs if not u.lower().endswith(".xml")]
            yield from self._follow(cid, filter_candidates(pages, home, MAX_PAGES), home)
            if depth == 0:
                # prefer page/post sitemaps that are likely to list team and contact pages
                children.sort(key=lambda u: 0 if any(k in u.lower() for k in ("page", "team", "people")) else 1)
                for child in children[:MAX_SITEMAPS]:
                    yield scrapy.Request(
                        child,
                        callback=self.sitemap,
                        errback=self.quiet,
                        cb_kwargs={"cid": cid, "home": home, "depth": 1},
                    )

        def sub(self, response, cid: int):
            self._emit(cid, response)

        def err(self, failure):
            stats["errors"] += 1

        def quiet(self, failure):  # a missing sitemap is normal, not an error
            pass

    process = CrawlerProcess(
        settings={
            "USER_AGENT": user_agent(),
            "ROBOTSTXT_OBEY": True,
            "DOWNLOAD_DELAY": 2.0,
            "CONCURRENT_REQUESTS": 8,
            "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
            "DOWNLOAD_TIMEOUT": 25,
            "RETRY_TIMES": 1,
            "DOWNLOAD_MAXSIZE": 5 * 1024 * 1024,
            "REDIRECT_MAX_TIMES": 5,
            "LOG_LEVEL": "ERROR",
            "TELNETCONSOLE_ENABLED": False,
            "COOKIES_ENABLED": False,
        }
    )
    process.crawl(Sites)
    process.start()
    out.write(json.dumps({"summary": stats}) + "\n")


if __name__ == "__main__":
    main()
