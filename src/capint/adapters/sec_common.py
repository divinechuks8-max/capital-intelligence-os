"""Shared plumbing for every SEC EDGAR adapter (Form 4, 13F, 13D/13G, ...).

Extracted once a third adapter needed the same pieces a second one had
already duplicated (13F copied Form 4's atom-feed regexes and CIK
helpers) — same "extract on the third consumer" discipline as
capint.scoring.anomaly. Nothing here is adapter-specific; each adapter
still owns its own document schema and parsing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from xml.etree import ElementTree

import httpx

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
ACCESSION_RE = re.compile(r"accession-number=([\d-]+)")
CIK_IN_PATH_RE = re.compile(r"/data/(\d+)/")


@dataclass(frozen=True)
class FilingRef:
    accession_number: str  # e.g. "0001193125-26-389607"
    cik_for_path: str  # any CIK EDGAR will resolve this accession under
    form_type: str
    filed_at: datetime  # SEC acceptance timestamp (tz-aware) — a candidate publication_time


def parse_cik(raw: str) -> str:
    """Normalizes to SEC's canonical 10-digit zero-padded form."""
    return raw.strip().lstrip("0").zfill(10) if raw.strip() else raw.strip()


def xml_text(el: ElementTree.Element | None, path: str, ns: dict[str, str] | None = None) -> str | None:
    if el is None:
        return None
    found = el.find(path, ns) if ns else el.find(path)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def strip_namespaces(root: ElementTree.Element) -> ElementTree.Element:
    """Some EDGAR XML schemas vary their namespace URI by schemaVersion, or
    mix namespaced and unnamespaced elements. Stripping every tag down to
    its local name lets callers use plain (namespace-agnostic) .find()
    paths instead of juggling multiple NS maps per document."""
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


class RateLimitedSecClient:
    """Thin HTTP wrapper enforcing SEC's fair-access policy
    (https://www.sec.gov/os/webmaster-faq#developers): a real identifying
    User-Agent, and a conservative minimum gap between requests (default
    0.2s, ~5 req/s, comfortably under SEC's ~10 req/s guidance)."""

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
        timeout: float = 20.0,
    ) -> None:
        if not user_agent.strip():
            raise ValueError(
                "SEC EDGAR requires an identifying User-Agent (org/individual + contact email) "
                "per its fair-access policy — refusing to send anonymous requests. "
                "Set SEC_EDGAR_USER_AGENT."
            )
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)

    def get(self, url: str) -> httpx.Response:
        self._throttle()
        resp = self._client.get(url, headers=self._headers)
        self._last_request_at = time.monotonic()
        resp.raise_for_status()
        return resp

    def get_prefix(self, url: str, num_bytes: int) -> bytes:
        """Fetches only the first `num_bytes` of a (possibly large)
        resource via an HTTP Range request — used to read a submission's
        SGML header without downloading the whole filing."""
        self._throttle()
        headers = {**self._headers, "Range": f"bytes=0-{num_bytes - 1}"}
        resp = self._client.get(url, headers=headers)
        self._last_request_at = time.monotonic()
        if resp.status_code not in (200, 206):
            resp.raise_for_status()
        return resp.content
