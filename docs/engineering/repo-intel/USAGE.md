# Using repo-intel in Claude and Codex sessions

Written for engineers and agents starting a session on AI Friend. All commands run from the repository root with `python -m tools.repo_intel`. The index lives in `.repo-intel/` (gitignored); regenerate it, never hand-edit it.

## 1. Start of every session

```
python -m tools.repo_intel resume        # coverage line, drift, next action
python -m tools.repo_intel reconcile     # exit 2 means the tree changed since the last inventory
python -m tools.repo_intel inventory     # refresh after any edit, then:
python -m tools.repo_intel index         # rebuild symbols and edges
```

`index` refuses a file whose bytes differ from the inventory hash, so run `inventory` first after every edit.

## 2. Before you build or change anything (the preflight)

Do these in order. Each answer names its confidence.

1. **Does it already exist?** `symbols NAME`, then `grep` for synonyms. A miss under one name is not absence: check the roles (`subjects`, `refs`) before concluding a feature is missing.
2. **Who uses it?** `refs NAME` and `deps PATH --reverse`. Read the confidence tag on every edge. `proven` came from the syntax tree, `lexical` from a Rust or JS pattern match, `inferred` from a name match only.
3. **What does it talk to over the bus?** `subjects --filter audio`. A subject with a publisher and no subscriber (or the reverse) may be a forwarder the extractor cannot follow. The architecture map lists these; `docs/engineering/repo-intel/ARCHITECTURE_MAP.md` explains the known cases.
4. **What could be hiding it from static analysis?** `unreferenced` prints the number of dynamic constructs (importlib, computed getattr, plugin discovery). If that number is not zero, "no static reference" is a lead, not a verdict.
5. **Record what you decide as a claim** (section 3) so the next session does not redo the work, and so a later edit invalidates it.
6. After the change, run `inventory`, `index`, `claims recheck`, and the tests that cover the diff.

### The preflight and the completion gate (automated)

```
python -m tools.repo_intel preflight NAME_OR_PATH   # definitions, references by confidence, tests that call it, related claims, impact for a file
python -m tools.repo_intel impact PATH [--depth N]  # transitive importers, tests reached, bus subjects, claims citing those files
python -m tools.repo_intel changes                  # added, changed-since-analysis, never-read, stale claims
python -m tools.repo_intel status [--json]          # revision, dirty state, stage counts, claim states
python -m tools.repo_intel gate [--strict]          # exit 1 while evidence is stale, disputed, failed or behind the tree
python -m tools.repo_intel map [--out FILE]         # the architecture map
```

Run `gate` before reporting any task DONE. It exits 1 and prints why while: files are not parsed at their current hash, a processing failure is open, files changed since analysis, a claim is stale or disputed (`--strict` also blocks never-verified claims). A wrong claim is not deleted; `claims supersede OLD --by NEW` keeps it in the ledger as `superseded` and takes it out of the gate.

Claim states against the directive's names: `verified` = VERIFIED (deterministic check plus independent reader); `supported` = PARTIALLY_VERIFIED (deterministic check or intact evidence only, never runtime-verified unless the check is `runtime`); `unverified` = UNVERIFIED; `refuted` = CONTRADICTED (the contradicting evidence is stored on the claim); `stale` = STALE; `disputed` and `superseded` are additional.

Invalidation: a quote that moved is re-bound; a vanished quote, a changed checked file, or any change to a code file for a claim whose check searched the whole index (callers, edges, subjects; documentation edits do not count) makes the claim `stale`; a claim that lists `depends_on_claims` goes stale when one of them does. Re-run `claims verify all`, then read what flipped. For a negative claim ("no caller"), the detail records the search scope, which is the whole structural index, and the number of dynamic constructs that scope cannot see.

## 3. Writing a claim

One JSON object per line:

```json
{"text": "state.subconscious is published by subconscious_agent and no code subscribes to it.",
 "kind": "debt:integration", "severity": "low", "classification": "n/a",
 "source": "repo-intel subject matrix",
 "evidence": [{"path": "backend/app/agents/subconscious_agent.py", "start": 774, "end": 774, "quote": "Topics.STATE_SUBCONSCIOUS,"}],
 "check": {"type": "subject_wired", "subject": "state.subconscious", "min_publishers": 1, "max_subscribers": 0},
 "subsystem": "agents", "depends_on_claims": []}
```

`python -m tools.repo_intel claims add FILE --author NAME --family claude|codex|human`, then `claims verify all`.

Evidence quotes must occur in the cited lines, or the claim is refused. `kind` is `doc-drift`, `architecture`, `behavior` or `debt:<category>` (correctness, reliability, performance, security, maintainability, integration, testing, architecture). `classification` is the verdict on an earlier document: `still_supported`, `superseded`, `contradicted`, `incomplete`, `unverifiable`.

Check types: `file_exists`, `file_missing`, `contains`, `not_contains`, `regex` (min/max matches), `symbol_defined`, `symbol_missing`, `no_static_callers`, `called_from_tests`, `edge_exists`, `edge_count`, `subject_wired`, `runtime` (argv list, allowlisted programs, no shell, only with `--runtime`).

State your claim at the strength its check proves. A check that proves one module is untested does not support a sentence about four.

## 4. Independent review

The author never reviews their own claim. A reviewer (another model, another session, or a person) runs:

```
python -m tools.repo_intel claims packet 12
python -m tools.repo_intel claims review 12 --reader codex-1 --family codex --verdict agree --note "read memory_store.py:747 and the callers; confirmed"
```

The packet shows the claim and the cited lines only. The reviewer should open other files and search for themselves. Outcomes: check passed and reviewer agreed gives `verified`; any disagreement, or a failed check with an agreeing reviewer, gives `disputed` and needs a human decision. Same-family review is weaker than cross-family review; the review row records the family.

## 5. After an edit

```
python -m tools.repo_intel inventory && python -m tools.repo_intel index && python -m tools.repo_intel claims recheck
```

A quote that only moved is re-bound with a history entry. A quote that vanished, or a checked file that changed, makes the claim `stale`. Re-run `claims verify all` and read what flipped.

## 6. Regenerating the reports

```
python -m tools.repo_intel audit --out docs/engineering/repo-intel --bench
```

`ARCHITECTURE_MAP.md`, `DISCREPANCY_REPORT.md`, `DEBT_REGISTRY.md`, `NOTE_QUALITY.md` and `BENCHMARK_TABLE.md` are generated. Do not edit them; fix the claim or the code and regenerate.

## 7. Limits you must keep in mind

- Rust and JS/TS are parsed lexically (comments and strings blanked, braces counted), not by rust-analyzer or tree-sitter, so their edges are at most `lexical`.
- Calls through `getattr`, `importlib`, plugin discovery and config-selected classes are invisible. Forwarders that take a subject as a parameter hide publishers.
- `inferred` call edges match a unique method name across the repo and can be wrong.
- `verified` means one deterministic check plus one independent reader. It does not mean the code behaves correctly at runtime.
- GPU, external-service and live-NATS behaviour is not exercised by this tool.
