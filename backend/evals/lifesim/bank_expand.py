"""One-time expansion of the template banks through local Claude Code, then freeze.

This is the only latent step in lifesim, and it never runs during generation:
it is run once, by hand, the output is reviewed, committed and frozen with a
sha256 manifest (`banks.freeze`), and every generation afterwards reads the
frozen files. Re-running it is a deliberate benchmark version change.

    python -m evals.lifesim.bank_expand --target 20            # all families below 20
    python -m evals.lifesim.bank_expand --families pref_change --dry-run

Validation is deterministic and strict, because a paraphrase that drops a
value placeholder would produce a turn annotated as stating a fact its text
never states:

* every new template must use exactly the placeholders that every existing
  template in the family uses (the family's required set), and no others
  beyond the family's declared placeholders;
* no braces other than placeholders, one line, 3-60 words;
* no duplicate of an existing template after normalising case and spacing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from . import banks

PROMPT = """You write surface variants for a synthetic benchmark of a home robot's memory.
Each variant is one thing a person says out loud to the robot that lives with them, or one
question the robot's evaluator asks about that person's life (the family name and examples
make clear which).

Family: {family}
Placeholders you must keep, spelled exactly, each used exactly once: {required}
Optional placeholders you may also use: {optional}

Existing variants (match their meaning and what they state; do not copy them):
{examples}

Write {n} NEW variants. Requirements:
- Natural spoken English from many kinds of people: different ages, regions, registers, lengths
  (some 4 words, some 25), some with filler, some blunt. No names, places, dates or brands
  written out: those only ever come from placeholders.
- Keep the same meaning and the same facts as the examples. Do not add facts.
- Vary vocabulary and sentence shape on purpose; avoid reusing the examples' key verbs where a
  natural alternative exists.
- Output ONLY a JSON array of strings. No commentary.
"""


def required_fields(templates: list[dict]) -> set[str]:
    sets = [banks.fields_of(t["text"]) for t in templates]
    return set.intersection(*sets) if sets else set()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def validate(
    candidates: list[str], family_spec: dict
) -> tuple[list[str], list[tuple[str, str]]]:
    existing = family_spec["templates"]
    declared = set(family_spec.get("placeholders", ()))
    required = required_fields(existing)
    seen = {_norm(t["text"]) for t in existing}
    ok, rejected = [], []
    for c in candidates:
        if not isinstance(c, str):
            rejected.append((repr(c), "not a string"))
            continue
        c = c.strip()
        try:
            fields = banks.fields_of(c)
        except ValueError as exc:
            rejected.append((c, f"bad format string: {exc}"))
            continue
        if fields - declared:
            rejected.append((c, f"undeclared {sorted(fields - declared)}"))
        elif not required <= fields:
            rejected.append((c, f"missing required {sorted(required - fields)}"))
        elif "\n" in c or not 3 <= len(c.split()) <= 60:
            rejected.append((c, "length or newline"))
        elif _norm(c) in seen:
            rejected.append((c, "duplicate"))
        else:
            seen.add(_norm(c))
            ok.append(c)
    return ok, rejected


def ask_claude(prompt: str, model: str | None) -> list:
    cmd = ["claude", "-p", "--no-session-persistence", "--tools", ""]
    if model:
        cmd += ["--model", model]
    res = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=600, check=False
    )
    if res.returncode != 0:
        raise RuntimeError(f"claude -p failed: {res.stderr[-400:]}")
    m = re.search(r"\[.*\]", res.stdout, re.DOTALL)
    if not m:
        raise RuntimeError(f"no JSON array in reply: {res.stdout[:300]}")
    return json.loads(m.group(0))


def expand(
    bank_dir: Path,
    target: int,
    families: set[str] | None,
    model: str | None,
    dry_run: bool,
    log,
) -> dict:
    report = {"added": {}, "rejected": {}}
    for name in banks.BANK_FILES:
        path = bank_dir / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for fam, spec in sorted(data["families"].items()):
            if families and fam not in families:
                continue
            need = target - len(spec["templates"])
            if need <= 0:
                continue
            required = required_fields(spec["templates"])
            prompt = PROMPT.format(
                family=fam,
                required=", ".join("{" + f + "}" for f in sorted(required)) or "(none)",
                optional=", ".join(
                    "{" + f + "}"
                    for f in sorted(set(spec.get("placeholders", ())) - required)
                )
                or "(none)",
                examples="\n".join(f"- {t['text']}" for t in spec["templates"]),
                n=need + 4,  # ask for spares; validation rejects some
            )
            if dry_run:
                log(f"[dry-run] {fam}: would request {need + 4}\n{prompt}")
                continue
            ok, rejected = validate(ask_claude(prompt, model), spec)
            ok = ok[:need]
            start = len(spec["templates"])
            for i, text in enumerate(ok):
                spec["templates"].append(
                    {"id": f"{fam}#x{start + i:03d}", "text": text}
                )
            report["added"][fam] = len(ok)
            if rejected:
                report["rejected"][fam] = rejected
            log(f"{fam}: +{len(ok)} ({len(rejected)} rejected)")
            path.write_text(
                json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
            )
    if not dry_run:
        report["manifest"] = banks.freeze(bank_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m evals.lifesim.bank_expand",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--target", type=int, default=20)
    p.add_argument("--families", nargs="*")
    p.add_argument(
        "--model",
        default=None,
        help="claude model; default is Claude Code's default (the best available)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--report", type=Path, help="write the added/rejected report here as JSON"
    )
    a = p.parse_args(argv)
    report = expand(
        banks.BANK_DIR,
        a.target,
        set(a.families) if a.families else None,
        a.model,
        a.dry_run,
        lambda s: print(s, flush=True),
    )
    if a.report:
        a.report.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
