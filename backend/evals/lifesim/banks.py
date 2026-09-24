"""Frozen template banks for utterances and probe questions (R11).

A bank file maps a template family to its declared placeholders and its
surface variants::

    {"families": {"pref_change": {"placeholders": ["old", "new", "thing"],
                                  "templates": [{"id": "pref_change#000", "text": "..."}]}}}

`banks/MANIFEST.json` records each file's sha256. `load` refuses to run on a
mismatch, so nobody silently regenerates (and re-randomizes) the benchmark's
own fixtures. Changing a bank is a deliberate act: edit, then `freeze`.

Every template's ``{fields}`` must be a subset of its family's declared
placeholders; that is checked at load time, not discovered mid-generation.
"""

from __future__ import annotations

import hashlib
import json
import random
import string
from dataclasses import dataclass
from pathlib import Path

BANK_DIR = Path(__file__).parent / "banks"
BANK_FILES = ("utterances.json", "probes.json")
MANIFEST = "MANIFEST.json"


class BankIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Template:
    id: str
    family: str
    text: str

    def render(self, **values: str) -> str:
        return self.text.format(**values)


def fields_of(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class Bank:
    def __init__(self, families: dict[str, dict]) -> None:
        self.placeholders: dict[str, frozenset[str]] = {}
        self.templates: dict[str, tuple[Template, ...]] = {}
        for fam, spec in sorted(families.items()):
            allowed = frozenset(spec.get("placeholders", ()))
            temps = []
            for row in spec["templates"]:
                extra = fields_of(row["text"]) - allowed
                if extra:
                    raise BankIntegrityError(
                        f"{row['id']}: undeclared placeholders {sorted(extra)}"
                    )
                temps.append(Template(row["id"], fam, row["text"]))
            if not temps:
                raise BankIntegrityError(f"family {fam!r} has no templates")
            self.placeholders[fam] = allowed
            self.templates[fam] = tuple(temps)

    def __contains__(self, family: str) -> bool:
        return family in self.templates

    def families(self) -> list[str]:
        return sorted(self.templates)

    def pick(
        self, family: str, rng: random.Random, avoid: set[str] | None = None
    ) -> Template:
        """A template from ``family``, preferring ones not in ``avoid`` (recently used ids)."""
        options = self.templates[family]
        if avoid:
            fresh = [t for t in options if t.id not in avoid]
            if fresh:
                return rng.choice(fresh)
        return rng.choice(options)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(bank_dir: Path) -> dict[str, dict]:
    families: dict[str, dict] = {}
    for name in BANK_FILES:
        path = bank_dir / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for fam, spec in data["families"].items():
            if fam in families:
                raise BankIntegrityError(
                    f"family {fam!r} defined in more than one bank file"
                )
            families[fam] = spec
    return families


def load(bank_dir: Path = BANK_DIR, verify: bool = True) -> Bank:
    if verify:
        verify_manifest(bank_dir)
    return Bank(_read(bank_dir))


def verify_manifest(bank_dir: Path = BANK_DIR) -> dict[str, str]:
    mpath = bank_dir / MANIFEST
    if not mpath.exists():
        raise BankIntegrityError(f"{mpath} missing; banks were never frozen")
    manifest = json.loads(mpath.read_text())
    hashes = {}
    for name in BANK_FILES:
        path = bank_dir / name
        if not path.exists():
            continue
        got = _sha(path)
        want = manifest["files"].get(name)
        if got != want:
            raise BankIntegrityError(
                f"{name} sha256 {got[:12]} != frozen {str(want)[:12]}; the bank changed since it was frozen. "
                "If that was deliberate, run `python -m evals.lifesim banks freeze`."
            )
        hashes[name] = got
    return hashes


def freeze(bank_dir: Path = BANK_DIR) -> dict:
    bank = Bank(_read(bank_dir))  # validates placeholders before anything is frozen
    files = {
        name: _sha(bank_dir / name) for name in BANK_FILES if (bank_dir / name).exists()
    }
    manifest = {
        "files": files,
        "families": {f: len(bank.templates[f]) for f in bank.families()},
        "templates": sum(len(t) for t in bank.templates.values()),
    }
    (bank_dir / MANIFEST).write_text(
        json.dumps(manifest, sort_keys=True, indent=1) + "\n"
    )
    return manifest
