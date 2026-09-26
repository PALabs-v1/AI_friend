"""Nothing open is left without an owner (docs/brain-research-v3/work/COVERAGE.md).

Deterministic doc gate, same spirit as `test_doc_drift.py`: every open
register item, every open finding and every decision record must have a row
in the coverage matrix, and every row must name an ID that exists and an
owner from the allowed set. A new finding logged without an owner fails
here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3 = REPO / "docs" / "brain-research-v3"
COVERAGE = V3 / "work" / "COVERAGE.md"
REGISTER = REPO / "docs" / "brain-research" / "01-problems.md"
FINDINGS = V3 / "findings.md"
DECISIONS = V3 / "decisions"
NF_TABLE = V3 / "research" / "neuro" / "fidelity-table.md"

OWNERS = {f"W{n}" for n in range(1, 13)} | {
    "SW",
    "P6",
    "P8",
    "P9",
    "P10",
    "CONSTRAINT",
    "CLOSED",
}
# Register statuses that mean nothing is left to do.
DONE_STATUS = re.compile(r"^(FIXED|DECIDED)\b")


def _coverage_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in COVERAGE.read_text().splitlines():
        match = re.match(r"^\| ([A-Z][A-Z0-9.-]*) \| ([A-Z0-9]+) \|", line)
        if match and match.group(1) != "ID":
            item, owner = match.groups()
            assert item not in rows, f"{item} appears twice in COVERAGE.md"
            rows[item] = owner
    return rows


def _register() -> dict[str, str]:
    """Register ID -> status cell."""
    items = {}
    for line in REGISTER.read_text().splitlines():
        match = re.match(r"^\| ([MAVSB]-\d+) \|", line)
        if match:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            items[match.group(1)] = cells[-1]
    return items


def _findings() -> dict[str, str]:
    """Finding ID -> its Status line."""
    text = FINDINGS.read_text()
    items = {}
    for match in re.finditer(r"^## (F-\d{3}):", text, re.MULTILINE):
        body = text[match.end() :].split("\n## ", 1)[0]
        status = re.search(r"\*\*Status\*\*: (.*)", body)
        items[match.group(1)] = status.group(1) if status else ""
    return items


def _decisions() -> set[str]:
    return {p.name[:6] for p in DECISIONS.glob("DR-[0-9][0-9][0-9]-*.md")}


def _nf_findings() -> set[str]:
    """NF-n IDs from the neuroscience fidelity table's data rows."""
    ids = set()
    for line in NF_TABLE.read_text().splitlines():
        match = re.match(r"^\| (NF-\d+) \|", line)
        if match:
            ids.add(match.group(1))
    return ids


def test_every_row_has_a_valid_owner():
    rows = _coverage_rows()
    assert rows, "COVERAGE.md has no rows"
    bad = {item: owner for item, owner in rows.items() if owner not in OWNERS}
    assert not bad, f"unknown owners: {bad}"


def test_every_open_register_item_is_owned():
    rows = _coverage_rows()
    missing = [
        item
        for item, status in _register().items()
        if not DONE_STATUS.match(status) and item not in rows
    ]
    assert not missing, f"open register items with no owner: {missing}"


def test_every_open_finding_is_owned():
    rows = _coverage_rows()
    missing = []
    for item, status in _findings().items():
        if status.lower().startswith("fixed"):
            continue
        # A finding split into items is covered by its F-NNN.k rows.
        if item not in rows and not any(r.startswith(item + ".") for r in rows):
            missing.append(item)
    assert not missing, f"open findings with no owner: {missing}"


def test_every_decision_is_owned():
    rows = _coverage_rows()
    missing = sorted(_decisions() - set(rows))
    assert not missing, f"decisions with no owner: {missing}"


def test_every_nf_finding_is_owned():
    """Every mechanism the neuroscience fidelity table flagged (defect,
    label-only, or dead code) has a coverage row, same as an F-finding.
    A row adopted into a workstream and shipped can move to CLOSED with its
    evidence (`test_closed_rows_name_their_evidence`); until then it stays
    listed, so nothing the audit found is silently forgotten."""
    rows = _coverage_rows()
    missing = sorted(_nf_findings() - set(rows))
    assert not missing, f"NF findings with no owner: {missing}"


def test_no_row_names_an_id_that_does_not_exist():
    register, findings, decisions, nf = (
        _register(),
        _findings(),
        _decisions(),
        _nf_findings(),
    )
    stale = []
    for item in _coverage_rows():
        if re.fullmatch(r"[MAVSB]-\d+", item):
            known = item in register
        elif item.startswith("NF-"):
            known = item in nf
        elif item.startswith("F-"):
            known = item.split(".")[0] in findings
        elif item.startswith("DR-"):
            known = item in decisions
        else:
            # C0-/C1-/G-/X-/SW- rows cite their source in the table itself.
            known = bool(re.fullmatch(r"(C0|C1|G|X|SW)-\d+", item))
        if not known:
            stale.append(item)
    assert not stale, f"rows naming unknown IDs: {stale}"


def test_closed_rows_name_their_evidence():
    text = COVERAGE.read_text()
    for line in text.splitlines():
        if re.match(r"^\| [A-Z][A-Z0-9.-]* \| CLOSED \|", line):
            note = line.rstrip("|").split("|")[-1].strip()
            assert len(note) > 20, f"CLOSED row without evidence: {line}"
