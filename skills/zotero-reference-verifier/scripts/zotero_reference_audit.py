#!/usr/bin/env python3
"""Zotero-assisted reference audit utility.

This script is intentionally conservative. It verifies identifiers and metadata
signals; it does not claim that a source supports a manuscript claim without
human/full-text review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"\barXiv[:\s]*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)\b", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID[:\s]*([0-9]{4,12})\b", re.IGNORECASE)
ISBN_RE = re.compile(r"\b(?:ISBN(?:-1[03])?[:\s]*)?((?:97[89][-\s]?)?[0-9][0-9Xx][0-9Xx][-\s0-9Xx]{7,17})\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
TRAILING_DOI_PUNCT = ".,;:)]}>\"'"


@dataclass
class Candidate:
    raw: str
    title: str | None = None
    year: str | None = None
    doi: str | None = None
    arxiv: str | None = None
    pmid: str | None = None
    isbn: str | None = None
    zotero_key: str | None = None


@dataclass
class AuditResult:
    status: str
    short_title: str
    identifier: str
    source_checked: str
    action: str
    details: dict[str, Any]


def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().rstrip(TRAILING_DOI_PUNCT)
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^doi:\s*", "", value, flags=re.IGNORECASE)
    return value.lower()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def title_words(value: str | None) -> set[str]:
    if not value:
        return set()
    words = re.findall(r"[a-z0-9]+", value.lower())
    stop = {"a", "an", "and", "of", "the", "to", "in", "for", "on", "with", "by"}
    return {word for word in words if len(word) > 2 and word not in stop}


def overlap_score(a: str | None, b: str | None) -> float:
    left = title_words(a)
    right = title_words(b)
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), len(right))


def guess_title(reference: str) -> str | None:
    clean = normalize_space(reference)
    if not clean:
        return None
    quoted = re.search(r"[\"“](.+?)[\"”]", clean)
    if quoted:
        return quoted.group(1)
    after_year = re.search(r"\b(?:19|20)\d{2}\b\)?\.?\s*(.+)", clean)
    if after_year:
        tail = re.sub(r"https?://\S+", "", after_year.group(1)).strip()
        tail = DOI_RE.sub("", tail).strip(" .")
        parts = [part.strip() for part in re.split(r"\.\s+", tail) if part.strip()]
        if parts:
            return parts[0][:180]
    parts = [part.strip() for part in re.split(r"\.\s+", clean) if part.strip()]
    if len(parts) >= 2:
        return parts[1][:180]
    return clean[:180]


def split_reference_text(text: str) -> list[str]:
    blocks = [normalize_space(block) for block in re.split(r"\n\s*\n", text) if normalize_space(block)]
    if len(blocks) > 1:
        return blocks
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    return lines


def candidate_from_text(reference: str) -> Candidate:
    doi_match = DOI_RE.search(reference)
    arxiv_match = ARXIV_RE.search(reference)
    pmid_match = PMID_RE.search(reference)
    isbn_match = ISBN_RE.search(reference)
    year_match = YEAR_RE.search(reference)
    return Candidate(
        raw=reference,
        title=guess_title(reference),
        year=year_match.group(0) if year_match else None,
        doi=normalize_doi(doi_match.group(0)) if doi_match else None,
        arxiv=arxiv_match.group(1) if arxiv_match else None,
        pmid=pmid_match.group(1) if pmid_match else None,
        isbn=normalize_space(isbn_match.group(1)) if isbn_match else None,
    )


def candidates_from_csl(path: str) -> list[Candidate]:
    data = json.loads(read_text(path))
    if isinstance(data, dict):
        data = [data]
    candidates: list[Candidate] = []
    for item in data:
        title = item.get("title")
        issued = item.get("issued", {})
        year = None
        if isinstance(issued, dict):
            parts = issued.get("date-parts") or []
            if parts and parts[0]:
                year = str(parts[0][0])
        doi = normalize_doi(item.get("DOI") or item.get("doi"))
        arxiv = None
        for field in ("note", "URL", "id"):
            value = str(item.get(field, ""))
            match = ARXIV_RE.search(value)
            if match:
                arxiv = match.group(1)
                break
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
        candidates.append(
            Candidate(
                raw=raw,
                title=title,
                year=year,
                doi=doi,
                arxiv=arxiv,
                pmid=str(item.get("PMID")) if item.get("PMID") else None,
                isbn=item.get("ISBN") or item.get("ISBN-13") or item.get("ISBN-10"),
                zotero_key=str(item.get("id")) if item.get("id") else None,
            )
        )
    return candidates


def http_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "zotero-reference-verifier/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def crossref_lookup(doi: str, mailto: str | None = None) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(doi, safe="")
    url = f"https://api.crossref.org/works/{encoded}"
    if mailto:
        url += "?" + urllib.parse.urlencode({"mailto": mailto})
    try:
        return http_json(url).get("message")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def year_from_crossref(message: dict[str, Any]) -> str | None:
    for field in ("published-print", "published-online", "published", "issued", "created"):
        date_parts = (message.get(field) or {}).get("date-parts") or []
        if date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return None


def audit_candidate(candidate: Candidate, use_crossref: bool, mailto: str | None) -> AuditResult:
    short_title = candidate.title or candidate.raw[:80]
    identifier = candidate.doi or (f"arXiv:{candidate.arxiv}" if candidate.arxiv else None) or (f"PMID:{candidate.pmid}" if candidate.pmid else None) or (f"ISBN:{candidate.isbn}" if candidate.isbn else "")

    if candidate.doi and use_crossref:
        message = crossref_lookup(candidate.doi, mailto=mailto)
        if not message:
            return AuditResult("conflict", short_title, candidate.doi, "Crossref DOI lookup", "DOI did not resolve; verify on publisher page or remove.", {"candidate": asdict(candidate)})
        source_title = (message.get("title") or [None])[0]
        source_year = year_from_crossref(message)
        title_score = overlap_score(candidate.title, source_title)
        year_ok = not candidate.year or not source_year or candidate.year == source_year
        status = "verified" if title_score >= 0.55 and year_ok else "conflict"
        action = "Safe for bibliography metadata; still verify claim-level support." if status == "verified" else "Metadata conflict; compare Zotero item against DOI landing page."
        return AuditResult(
            status,
            short_title,
            candidate.doi,
            "Crossref DOI lookup",
            action,
            {
                "candidate": asdict(candidate),
                "crossref_title": source_title,
                "crossref_year": source_year,
                "title_overlap": round(title_score, 3),
                "year_match": year_ok,
            },
        )

    if candidate.doi:
        return AuditResult("metadata-only", short_title, candidate.doi, "identifier extracted", "Run with --crossref or verify on DOI/publisher page.", {"candidate": asdict(candidate)})
    if candidate.arxiv or candidate.pmid or candidate.isbn:
        return AuditResult("metadata-only", short_title, identifier, "identifier extracted", "Verify this identifier in its authoritative registry.", {"candidate": asdict(candidate)})
    return AuditResult("needs-identifier", short_title, "", "none", "Find DOI, arXiv ID, PMID, ISBN, publisher page, or stable catalog URL before citing.", {"candidate": asdict(candidate)})


def markdown_report(results: list[AuditResult]) -> str:
    lines = [
        "# Reference Verification Ledger",
        "",
        "| status | short title | identifier | source checked | action |",
        "|---|---|---|---|---|",
    ]
    for result in results:
        row = [result.status, result.short_title, result.identifier, result.source_checked, result.action]
        escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(escaped) + " |")
    lines.append("")
    unresolved = [r for r in results if r.status in {"conflict", "unverified", "needs-identifier"}]
    lines.append(f"Verified: {sum(1 for r in results if r.status == 'verified')}")
    lines.append(f"Needs action: {len(unresolved)}")
    return "\n".join(lines) + "\n"


def cmd_ping(_args: argparse.Namespace) -> int:
    url = "http://127.0.0.1:23119/connector/ping"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            body = response.read().decode("utf-8", errors="replace")
        print(json.dumps({"zotero_connector": "available", "url": url, "response": body}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - CLI should show environment state, not traceback
        print(json.dumps({"zotero_connector": "unavailable", "url": url, "error": str(exc)}, ensure_ascii=False))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    if args.csl_json:
        candidates = candidates_from_csl(args.csl_json)
    else:
        candidates = [candidate_from_text(ref) for ref in split_reference_text(read_text(args.input))]
    results = [audit_candidate(candidate, args.crossref, args.mailto) for candidate in candidates]
    report = markdown_report(results)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    if args.json:
        Path(args.json).write_text(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(result.status == "conflict" for result in results) else 0


def cmd_extract(args: argparse.Namespace) -> int:
    candidates = [candidate_from_text(ref) for ref in split_reference_text(read_text(args.input))]
    print(json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=False, indent=2))
    return 0


def cmd_self_test(_args: argparse.Namespace) -> int:
    sample = "Smith, J. (2020). Testing Scholarly Metadata. Journal of Tests. https://doi.org/10.1234/ABC.DEF"
    candidate = candidate_from_text(sample)
    assert candidate.doi == "10.1234/abc.def"
    assert candidate.year == "2020"
    assert overlap_score("Testing Scholarly Metadata", "Testing scholarly metadata: a note") > 0.55
    report = markdown_report([audit_candidate(candidate, use_crossref=False, mailto=None)])
    assert "metadata-only" in report
    print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)

    ping = sub.add_parser("ping", help="Check whether Zotero Connector local server is reachable")
    ping.set_defaults(func=cmd_ping)

    extract = sub.add_parser("extract", help="Extract candidate identifiers from plain bibliography text")
    extract.add_argument("--input", required=True, help="Input text file, or - for stdin")
    extract.set_defaults(func=cmd_extract)

    audit = sub.add_parser("audit", help="Create a reference verification ledger")
    group = audit.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Plain text bibliography file, or - for stdin")
    group.add_argument("--csl-json", help="CSL JSON exported from Zotero")
    audit.add_argument("--crossref", action="store_true", help="Resolve DOI metadata through Crossref")
    audit.add_argument("--mailto", help="Email for polite Crossref API requests")
    audit.add_argument("--output", help="Markdown ledger path")
    audit.add_argument("--json", help="Detailed JSON output path")
    audit.set_defaults(func=cmd_audit)

    self_test = sub.add_parser("self-test", help="Run local parser checks without network")
    self_test.set_defaults(func=cmd_self_test)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    start = time.time()
    code = args.func(args)
    if getattr(args, "verbose", False):
        print(f"elapsed={time.time() - start:.3f}s", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
