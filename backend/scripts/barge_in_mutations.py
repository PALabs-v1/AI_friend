"""Mutation check for the barge-in history and outcome-record gates (ADR-003).

Five adversarial reviews found regression tests that could not fail on the
code they claimed to guard. This makes that claim checkable: each mutation
below breaks one gate, and the barge-in test modules must fail for it.

    python scripts/barge_in_mutations.py            # all mutations
    python scripts/barge_in_mutations.py N4 M11     # only these (prefix match)

Every mutation is applied to a temporary copy of `backend/` (never the
checkout) and the test modules below are run against it with this
interpreter. The unmutated copy runs first and must pass: otherwise nothing
can be concluded (wrong interpreter, missing pytest, broken tree) and the
script exits 2. A mutant counts as killed only when pytest reports failing
tests (exit code 1); a collection or import error is reported as an error,
never as a kill. Exit status 1 if a mutation that is not listed as
equivalent survives or errors, or if a pattern no longer matches the source
(`tests/test_barge_in_mutation_patterns.py` catches that on every commit).
Takes about three minutes.

A mutation is "equivalent" when no observable behaviour changes; each one
names why, so the list can be challenged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
BRAIN = "app/agents/brain_agent.py"
STORE = "app/state/conversation_store.py"
TEST_MODULES = [
    "tests/test_barge_in_real_flow.py",
    "tests/test_barge_in_truncation.py",
    "tests/test_brain_agent_turn_state_lock.py",
    "tests/test_embodied_feedback.py",
    "tests/test_causal_slice.py",
    "tests/test_brain_v2_regressions.py",
]
_ROW = "\"WHERE id = $2 AND session_id = $3 AND role = 'assistant'\""


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    equivalent: str | None = None  # why no behaviour changes, if so


MUTATIONS = [
    Mutation(
        "M1_superseded_progress_leaks",
        BRAIN,
        "                    superseded.progress = progress\n                    return\n",
        "                    superseded.progress = progress\n"
        "                    self.last_audio_progress = progress\n                    return\n",
    ),
    Mutation(
        "M2_superseded_stop_not_consumed",
        BRAIN,
        "                        self._superseded_turn_id = None\n",
        "",
    ),
    Mutation(
        "M3_stale_stop_guard_deleted",
        BRAIN,
        "                    and stop_msg.turn_id != active_turn_id\n"
        "                    and not accepts_superseded",
        "                    and False",
    ),
    Mutation(
        "M4_completed_ignores_owner",
        BRAIN,
        "                    owner is None\n                    or (\n",
        "                    True\n                    or (\n",
    ),
    Mutation(
        "M5_completed_not_once",
        BRAIN,
        "                        owner == progress.utterance_id\n"
        '                        and not getattr(self, "_reply_resolved", False)\n',
        "                        owner == progress.utterance_id\n",
    ),
    Mutation(
        "M6_cancelled_ignores_generating",
        BRAIN,
        '            getattr(self, "_reply_generating", None) is False\n        ):',
        "            False\n        ):",
    ),
    Mutation(
        "M7_cut_not_addressed_by_id",
        BRAIN,
        "            heard, message_id=message_id\n        )",
        "            heard, message_id=None\n        )",
    ),
    Mutation(
        "M8_insert_wait_unbounded",
        BRAIN,
        "await asyncio.wait({log_task}, timeout=REPLY_INSERT_WAIT_S)",
        "await asyncio.wait({log_task})",
    ),
    Mutation(
        "M9_unstored_reply_still_written",
        BRAIN,
        "        if message_id is None:\n",
        "        if False:\n",
    ),
    Mutation(
        "M10_insert_ignores_brain_id",
        BRAIN,
        '                            "assistant", full_response, message_id=message_id\n',
        '                            "assistant", full_response\n',
    ),
    Mutation(
        "M11_store_rewrites_every_other_row",
        STORE,
        _ROW,
        "\"WHERE id != $2 AND session_id = $3 AND role = 'assistant'\"",
    ),
    Mutation(
        "M12_replacement_does_not_resolve",
        BRAIN,
        "                self._reply_resolved = True\n                self._reply_generating = False\n"
        '            await self._emit_outcome_record(intent, status="CANCELLED", error=reason)',
        "                self._reply_generating = False\n"
        '            await self._emit_outcome_record(intent, status="CANCELLED", error=reason)',
        equivalent="the same section nulls the intent, so no later record can be "
        "attributed to the reply",
    ),
    Mutation(
        "M13_generation_never_ends",
        BRAIN,
        "                if self._reply_turn_id == turn_id:\n"
        "                    self._reply_generating = False\n",
        "                if self._reply_turn_id == turn_id:\n",
    ),
    Mutation(
        "M14_snapshot_drops_row_id",
        BRAIN,
        'getattr(self, "_reply_message_id", None) if owns_reply else None',
        "None",
    ),
    Mutation(
        "M15_stop_leaves_generating_set",
        BRAIN,
        "            # record this reply CANCELLED again.\n"
        "            self._reply_generating = False\n",
        "            # record this reply CANCELLED again.\n",
    ),
    Mutation(
        "N3_cancelled_twice",
        BRAIN,
        "        if produced_nothing and not already_resolved:",
        "        if produced_nothing:",
    ),
    Mutation(
        "N4_row_id_in_a_second_section",
        BRAIN,
        "                if self._reply_turn_id == turn_id:\n"
        "                    self._reply_generating = False\n"
        "                    self._reply_log_task = log_task\n"
        "                    self._reply_message_id = message_id\n",
        "                if self._reply_turn_id == turn_id:\n"
        "                    self._reply_generating = False\n"
        "            await asyncio.sleep(0)\n"
        "            async with self._turn_state_lock:\n"
        "                if self._reply_turn_id == turn_id:\n"
        "                    self._reply_log_task = log_task\n"
        "                    self._reply_message_id = message_id\n",
    ),
    Mutation(
        "N5_cut_ignores_text_owner",
        BRAIN,
        '                if getattr(self, "_reply_resolved", False) or (\n'
        "                    owner is not None and active is not None and owner != active\n"
        "                ):",
        '                if getattr(self, "_reply_resolved", False):',
    ),
    Mutation(
        "N6_offset_at_end_is_a_cut",
        BRAIN,
        "                if 0 < offset < len(text):",
        "                if 0 < offset <= len(text):",
    ),
    Mutation(
        "N7_snapshot_carries_resolved_reply",
        BRAIN,
        '                and getattr(self, "_reply_turn_id", None) == interrupted_turn_id\n'
        '                and not getattr(self, "_reply_resolved", False)\n',
        '                and getattr(self, "_reply_turn_id", None) == interrupted_turn_id\n',
    ),
    Mutation(
        "N8_superseded_accepts_any_stop",
        BRAIN,
        '                        stop_msg.reason == "confirmed_command"\n'
        "                        and stop_msg.turn_id is not None",
        "                        stop_msg.turn_id is not None",
    ),
    Mutation(
        "N13_generating_false_from_the_start",
        BRAIN,
        "                self._reply_resolved = False\n                self._reply_generating = True\n",
        "                self._reply_resolved = False\n                self._reply_generating = False\n",
    ),
    Mutation(
        "N14_completed_does_not_resolve",
        BRAIN,
        "                    if owner is not None:\n                        self._reply_resolved = True\n"
        "                    delivered",
        "                    delivered",
    ),
    Mutation(
        "N17_row_id_never_recorded",
        BRAIN,
        "                    self._reply_message_id = message_id\n",
        "                    self._reply_message_id = None\n",
    ),
    Mutation(
        "X12_snapshot_ignores_text_owner",
        BRAIN,
        "                interrupted_turn_id is not None\n"
        '                and getattr(self, "_reply_turn_id", None) == interrupted_turn_id\n',
        "                interrupted_turn_id is not None\n",
    ),
    Mutation(
        "X17_reset_keeps_resolved",
        BRAIN,
        "                self._reply_turn_id = turn_id\n                self._reply_resolved = False\n",
        "                self._reply_turn_id = turn_id\n",
    ),
    Mutation(
        "N2_cancel_before_content_does_not_resolve",
        BRAIN,
        "            if produced_nothing:\n                self._reply_resolved = True\n",
        "",
        equivalent="its only caller, _on_audio_stop, runs _truncate_interrupted_reply "
        "next, which resolves the active turn's own reply on the same path",
    ),
    Mutation(
        "N9_timed_out_wait_still_writes",
        BRAIN,
        "                    REPLY_INSERT_WAIT_S,\n                )\n                return\n",
        "                    REPLY_INSERT_WAIT_S,\n                )\n",
        equivalent="the row is still being inserted, so an UPDATE by its id "
        "matches nothing; the insert then stores the full text either way",
    ),
    Mutation(
        "N11_replacement_keeps_intent",
        BRAIN,
        "                self._active_action_intent = None\n"
        "                self._reply_resolved = True\n",
        "                self._reply_resolved = True\n",
        equivalent="the reply is resolved in the same section and the next "
        "user turn's reset nulls the intent before its pipeline commits one",
    ),
    Mutation(
        "N12_end_of_generation_ignores_owner",
        BRAIN,
        "                if self._reply_turn_id == turn_id:\n"
        "                    self._reply_generating = False\n"
        "                    self._reply_log_task = log_task\n",
        "                if True:\n"
        "                    self._reply_generating = False\n"
        "                    self._reply_log_task = log_task\n",
        equivalent="a user turn reaches this section only as the owner it set at "
        "its own reset; a newer turn would first have cancelled this flow",
    ),
    Mutation(
        "N18_proactive_turn_takes_the_row_id",
        BRAIN,
        "        elif store_reply:\n            self.spawn(",
        "        elif store_reply:\n"
        "            self._reply_message_id = message_id\n"
        "            self.spawn(",
        equivalent="once a proactive turn is active the owner check stops any "
        "cut of the user reply, so the live row id is never read",
    ),
    Mutation(
        "S3_store_insert_ignores_id",
        STORE,
        "                    message_id or uuid.uuid4(),\n",
        "                    uuid.uuid4(),\n",
    ),
    Mutation(
        "S5_store_update_drops_role",
        STORE,
        _ROW,
        '"WHERE id = $2 AND session_id = $3"',
        equivalent="ids are fresh UUIDs, so the addressed row is always the "
        "assistant row the brain logged",
    ),
]


def check_patterns(root: Path = BACKEND) -> list[str]:
    """Mutations whose `old` text does not occur exactly once."""
    problems = []
    for m in MUTATIONS:
        count = (root / m.path).read_text().count(m.old)
        if count != 1:
            problems.append(f"{m.name}: pattern found {count} times in {m.path}")
    return problems


def _copy_backend(dest: Path) -> Path:
    ignore = shutil.ignore_patterns("crates", "target", "__pycache__", ".venv", "*.db")
    shutil.copytree(BACKEND, dest, ignore=ignore)
    return dest


def _run_tests(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-x",
            *TEST_MODULES,
        ],
        cwd=root,
        env={**os.environ, "CI": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def main(argv: list[str]) -> int:
    selected = [
        m for m in MUTATIONS if not argv or any(m.name.startswith(a) for a in argv)
    ]
    problems = check_patterns()
    if problems:
        print("\n".join(problems))
        return 1
    failed = False
    killed_count = 0
    with tempfile.TemporaryDirectory(prefix="barge-in-mutations-") as tmp:
        root = _copy_backend(Path(tmp) / "backend")
        baseline = _run_tests(root)
        if baseline.returncode != 0:
            print(
                f"Baseline (no mutation) did not pass (pytest exit {baseline.returncode}); "
                f"nothing can be concluded. Interpreter: {sys.executable}\n"
                + baseline.stdout[-2000:]
                + baseline.stderr[-2000:]
            )
            return 2
        originals = {p: (root / p).read_text() for p in {m.path for m in selected}}
        for m in selected:
            for path, text in originals.items():
                (root / path).write_text(text)
            target = root / m.path
            target.write_text(target.read_text().replace(m.old, m.new, 1))
            run = _run_tests(root)
            if run.returncode not in (0, 1):  # 2-5: the tests could not run
                verdict = (
                    f"ERROR (pytest exit {run.returncode}; the mutant did not run)"
                )
                failed = True
            elif run.returncode == 1:
                killed_count += 1
                verdict = "killed" + (
                    " (listed as equivalent: re-check)" if m.equivalent else ""
                )
            elif m.equivalent:
                verdict = f"survived, equivalent: {m.equivalent}"
            else:
                verdict = "SURVIVED"
                failed = True
            print(f"{m.name:44s} {verdict}")
    print(f"\n{len(selected)} mutations, {killed_count} killed (baseline passed)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
