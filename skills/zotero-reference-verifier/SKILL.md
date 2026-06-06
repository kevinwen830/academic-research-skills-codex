---
name: zotero-reference-verifier
description: Use for Zotero-assisted academic reference verification, citation hallucination control, bibliography audits, DOI/arXiv/ISBN/PMID checks, Google Scholar or publisher-page collection workflows, CSL JSON/BibTeX/RIS review, and any task where Codex must verify references before using them in a paper, thesis, literature review, systematic review, manuscript revision, or citation list. Trigger when the user mentions Zotero, Google Scholar collection, Save to Zotero, reference verification, citation integrity, fake references, hallucinated citations, DOI checks, bibliography cleanup, CSL JSON, BibTeX, RIS, or "verify these references".
---

# Zotero Reference Verifier

## Core Rule

Do not treat model memory, Google Scholar snippets, or a formatted bibliography as verified evidence. Before citing a source as support for a claim, produce a reference verification ledger with one status per item:

- `verified`: identifier resolves and key metadata matches an independent source.
- `metadata-only`: item is in Zotero or a database but still lacks independent confirmation.
- `needs-identifier`: no DOI, arXiv ID, PMID, ISBN, stable publisher URL, or equivalent identifier was found.
- `conflict`: identifier resolves but author, title, year, venue, or item type conflicts.
- `unverified`: no reliable source was found.

Use unverified items only as leads, not as evidence in final prose.

## Workflow

1. Check local Zotero availability.
   - Run `python3 scripts/zotero_reference_audit.py ping`.
   - If Zotero is unavailable, ask the user to open Zotero Desktop and ensure the Zotero Connector can reach `http://127.0.0.1:23119/connector/ping`.
2. Collect or normalize candidate references.
   - Prefer DOI, PMID, arXiv ID, ISBN, publisher pages, PubMed, Crossref, OpenAlex, Semantic Scholar, library catalogs, or journal article abstract pages.
   - Use Google Scholar for discovery only when better source pages are not known yet.
   - If using Google Scholar with Zotero Connector, save small batches. Open the most relevant result or publisher page when possible, then use Save to Zotero from that page. Do not mass-save large result sets.
3. Export or query Zotero metadata.
   - Prefer Zotero-exported CSL JSON/BibTeX/RIS or the local Zotero API when available.
   - If browser automation cannot click the Zotero Connector extension, ask the user to click Save to Zotero, then continue from the exported/local metadata.
4. Run the audit script.
   - Plain references: `python3 scripts/zotero_reference_audit.py audit --input refs.txt --crossref --output reference-verification.md --json reference-verification.json`
   - Zotero CSL JSON export: `python3 scripts/zotero_reference_audit.py audit --csl-json zotero-export.json --crossref --output reference-verification.md`
5. Apply the gate.
   - Final papers, literature reviews, and citation lists must separate verified references from unresolved leads.
   - For `conflict` or `unverified`, either correct from an authoritative page or remove the item from evidence-bearing prose.

## Source Preference

Read `references/zotero-source-policy.md` when deciding where to collect or verify records. The short version:

- Best: DOI/publisher page, PubMed, arXiv, official proceedings, library catalog, Crossref/OpenAlex/Semantic Scholar metadata used as cross-checks.
- Useful but not final: Google Scholar search results and citation snippets.
- Zotero metadata reduces typing errors, but it is not proof that a cited claim is true.

## Browser And Zotero Connector

When the user asks for Google Scholar plus Zotero:

1. Open the query in the browser.
2. Prefer a paper's publisher/DOI landing page over saving directly from the Scholar result list.
3. Use the Zotero Connector save button when accessible.
4. If the extension UI is not automatable, pause for the smallest user action: "请点一下浏览器里的 Save to Zotero，然后我继续复核。"
5. After saving, resume with Zotero export/local API/audit. Never stop at "saved to Zotero" as the final quality gate.

## Output Contract

Return a compact ledger:

| status | short title | identifier | source checked | action |
|---|---|---|---|---|

Then state:

- which references are safe to cite,
- which require manual correction or removal,
- which claim-to-reference links remain unsupported.
