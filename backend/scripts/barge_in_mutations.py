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
import re
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
    "tests/test_bargein_w5_regressions.py",
    "tests/test_bargein_state_machine_a.py",
    "tests/test_bargein_state_machine_b.py",
    "tests/test_barge_in_real_flow.py",
    "tests/test_barge_in_truncation.py",
    "tests/test_brain_agent_turn_state_lock.py",
    "tests/test_embodied_feedback.py",
    "tests/test_causal_slice.py",
    "tests/test_brain_v2_regressions.py",
    "tests/test_playback_progress.py",
    "tests/test_clock_seam.py",
]
RUN_TIMEOUT_S = 600  # a mutant that hangs the tests is an error, not a verdict
_ROW = "\"WHERE id = $2 AND session_id = $3 AND role = 'assistant'\""


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    equivalent: str | None = None  # why no behaviour changes, if so
    additional: tuple[tuple[str, str], ...] = ()


MUTATIONS = [
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
        "M11_store_rewrites_every_other_row",
        STORE,
        _ROW,
        "\"WHERE id != $2 AND session_id = $3 AND role = 'assistant'\"",
    ),
    Mutation(
        "N9_timed_out_wait_still_writes",
        BRAIN,
        "                    REPLY_INSERT_WAIT_S,\n                )\n                return\n",
        "                    REPLY_INSERT_WAIT_S,\n                )\n",
    ),
    Mutation(
        "Y14_turn_reset_keeps_old_progress",
        BRAIN,
        "                self.last_assistant_response = None\n"
        "                self.last_audio_progress = None\n"
        "                self._active_action_intent = None\n",
        "                self.last_assistant_response = None\n"
        "                self._active_action_intent = None\n",
        equivalent=(
            "since the W5 ledger `last_audio_progress` is diagnostic only: every "
            "cut reads its own reply's progress from the ledger entry, and a "
            "resolution clears the field for its own turn"
        ),
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
    Mutation(
        "W5_one_interruption_felt_twice",
        BRAIN,
        "        if reply.interruption_felt:\n            return True\n",
        "",
    ),
    Mutation(
        "W5_grace_window_expires_immediately",
        BRAIN,
        "            await clock.sleep(Config.PROACTIVE_GRACE_WINDOW_S)\n",
        "            await clock.sleep(0)\n",
    ),
    Mutation(
        "W5_decline_does_not_respect_mid_utterance",
        BRAIN,
        "        if self._is_user_mid_utterance() or (\n",
        "        if False or (\n",
    ),
    Mutation(
        "W5_self_interrupt_threshold_is_exclusive",
        BRAIN,
        "            and importance < Config.SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE\n",
        "            and importance <= Config.SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE\n",
    ),
    Mutation(
        "W5_chat_input_redelivery_is_accepted",
        BRAIN,
        "        if not self._remember_accepted_utterance(msg.utterance_id):\n",
        "        if False:\n",
    ),
    Mutation(
        "W5_terminal_wait_resolves_without_transport",
        BRAIN,
        "            await clock.sleep(Config.REPLY_TERMINAL_WAIT_S)\n",
        "            await clock.sleep(0)\n",
    ),
    # Review of W5-A (see ADR-W5 section 8): the user-final stop must stay
    # unscoped, cut every started reply, and cut nothing while Stage 2 is
    # still deciding a speculative intent.
    Mutation(
        "W5_user_final_stop_scoped_to_active_turn",
        BRAIN,
        "                utterance_id=msg.utterance_id,\n            )\n",
        "                utterance_id=msg.utterance_id,\n"
        '                turn_id=getattr(self, "_active_response_turn_id", None),\n'
        "            )\n",
    ),
    Mutation(
        "W5_user_final_cuts_only_unstarted",
        BRAIN,
        "                if reply.started and not reply.resolved:\n",
        "                if False:\n",
    ),
    Mutation(
        "W5_speculative_pending_still_cuts",
        BRAIN,
        "            return False\n        # With a speculative intent pending nothing is cut here:",
        "            return False\n"
        "        for reply in tuple(self._reply_ledger.values()):\n"
        "            if reply.started and not reply.resolved:\n"
        '                await self._cut_reply(reply, "confirmed_user_speech", publish=False)\n'
        "        # With a speculative intent pending nothing is cut here:",
    ),
    Mutation(
        # Machine B: a self-thought interrupt before the first chunk cut the
        # user reply without publishing its scoped stop.
        "W5_self_thought_interrupt_silent_before_first_chunk",
        BRAIN,
        "            if not active_reply.started:\n",
        "            if False:\n",
    ),
    Mutation(
        # Machine B: an unscoped confirmed stop missed the ledger and the
        # pre-W5 truncation recorded a second outcome (I2).
        "W5_unscoped_confirmed_stop_bypasses_ledger",
        BRAIN,
        "        target = stop_msg.turn_id or active_id\n",
        "        target = stop_msg.turn_id\n",
    ),
    # ---- W5 ledger ports of gates the single-slot deletion moved ----
    Mutation(
        "W5_stale_progress_leaks_into_the_active_turn",  # was M1, Z42
        BRAIN,
        "                if active_turn_id and progress.utterance_id != active_turn_id:\n",
        "                if False:\n",
    ),
    Mutation(
        "W5_stale_stop_is_addressed",  # was M3, N8
        BRAIN,
        'addressed = target == active_id or stop_msg.reason == "confirmed_command"',
        "addressed = True",
    ),
    Mutation(
        "W5_unstarted_flow_end_does_not_resolve",  # was N2, M12, M13
        BRAIN,
        "            return\n        reason = entry.cancel_reason or ended\n",
        "            return\n        return\n        reason = None\n",
    ),
    Mutation(
        "W5_cancel_reason_not_recorded",  # was N3
        BRAIN,
        "                    prior_entry.cancel_reason = reason\n",
        "                    pass\n",
    ),
    Mutation(
        "W5_row_id_only_inside_the_lock",  # was N4, N17, M14
        BRAIN,
        "            if entry is not None:\n"
        "                message_id = entry.message_id or message_id\n"
        "                entry.message_id = message_id\n"
        "                log_task = entry.log_task\n",
        "",
        additional=(
            (
                (
                    "            if entry is not None:\n"
                    "                entry.log_task = log_task\n"
                    "        async with self._turn_state_lock:\n"
                ),
                "        async with self._turn_state_lock:\n",
            ),
        ),
    ),
    Mutation(
        "W5_pacing_user_reply_not_in_flight",  # the pacing-window bug
        BRAIN,
        'active_reply and active_reply.source == "user" and not active_reply.resolved\n',
        'active_reply and active_reply.source == "user" and not active_reply.resolved\n'
        "            and active_reply.started\n",
    ),
    Mutation(
        "W5_proactive_reply_takes_the_user_turns_intent",  # was N18
        BRAIN,
        "                if not is_subconscious:\n"
        '                    entry.intent = getattr(self, "_active_action_intent", None)\n',
        "                if True:\n"
        '                    entry.intent = getattr(self, "_active_action_intent", None)\n',
    ),
    Mutation(
        "W5_out_of_order_lifecycle_applied",  # was M5
        BRAIN,
        "            if result is not LifecycleApplyResult.APPLIED:\n                return\n",
        "            if result is LifecycleApplyResult.PROTOCOL_ERROR:\n                return\n",
    ),
    Mutation(
        "W5_insert_ignores_brain_id",  # was M10
        BRAIN,
        'store.log_message("assistant", full_response, message_id=message_id)\n'
        "                )\n            if entry is not None:\n",
        'store.log_message("assistant", full_response)\n'
        "                )\n            if entry is not None:\n",
    ),
    Mutation(
        "W5_record_offset_is_trimmed_length",  # was Y20
        BRAIN,
        "            actual_delivered_text=heard,\n            character_offset=heard_offset,\n",
        "            actual_delivered_text=heard,\n            character_offset=len(heard),\n",
    ),
    Mutation(
        "W5_fully_heard_reply_is_rewritten",  # was N6
        BRAIN,
        "        if heard != text:  # a reply never stored has no row",
        "        if True:  # a reply never stored has no row",
    ),
    Mutation(
        "W5_flushed_take_resolves_the_reply",
        BRAIN,
        '                if event.state == "INTERRUPTED" and event.flushed:\n'
        "                    return\n                entry.started = True\n",
        "                entry.started = True\n",
    ),
    Mutation(
        "W5_final_text_reaches_the_entry_after_done",  # was Y19
        BRAIN,
        "        # end of generation and the row-id write in `_finish_reply`.\n"
        "        entry = self._reply_ledger.get(turn_id)\n"
        "        if entry is not None and not entry.resolved:\n"
        "            entry.text = full_response\n",
        "        entry = None\n",
    ),
    # --- Codex cold critic round 1 (ADR-W5 section 9) ---
    Mutation(
        "W5_failed_first_chunk_stays_started",  # critic r1 #1
        BRAIN,
        "                entry.started = False\n            raise\n",
        "                pass\n            raise\n",
    ),
    Mutation(
        "W5_resolved_reply_leaves_the_ledger_last",  # critic r1 #2
        BRAIN,
        "        self._reply_ledger.pop(entry.turn_id, None)\n"
        '        resolved = getattr(self, "_resolved_replies", None)\n',
        '        resolved = getattr(self, "_resolved_replies", None)\n',
        additional=(
            (
                (
                    "            await self._store_heard_reply(heard, entry.log_task, "
                    "entry.message_id)\n"
                ),
                (
                    "            await self._store_heard_reply(heard, entry.log_task, "
                    "entry.message_id)\n        self._reply_ledger.pop(entry.turn_id, None)\n"
                ),
            ),
        ),
    ),
    Mutation(
        "W5_overflow_write_not_shielded",  # critic r1 #2
        BRAIN,
        "            await asyncio.shield(\n                self.spawn(\n"
        "                    self._resolve_reply(\n",
        "            await (\n                (\n                    self._resolve_reply(\n",
    ),
    Mutation(
        "W5_teardown_unbounded",  # critic r1 #3
        BRAIN,
        "await asyncio.wait({task}, timeout=GENERATION_TEARDOWN_WAIT_S)",
        "await asyncio.wait({task})",
    ),
    Mutation(
        "W5_no_publish_fence",  # critic r1 #3
        BRAIN,
        "        return task is not None and task.cancelling() > 0\n",
        "        return False\n",
    ),
    Mutation(
        "W5_timed_out_teardown_leaves_the_reply",  # critic r1 #3
        BRAIN,
        '            await self._end_generation(entry.turn_id, "generation_cancelled", task)\n',
        "            pass\n",
    ),
    Mutation(
        "W5_fallback_is_not_the_reply_text",  # critic r1 #4
        BRAIN,
        '                full_response = f"{spoken} {fallback_text}" if spoken else fallback_text\n',
        "                full_response = full_response\n",
    ),
    Mutation(
        "W5_cut_reply_not_addressable_after_resolving",  # stop felt once, not by timing
        BRAIN,
        '            if done is not None and done.status == "TRUNCATED":\n',
        "            if False:\n",
    ),
    Mutation(
        "W5_cancelled_sleeper_retained",  # critic r1 #5
        "app/clock.py",
        "            if sleeper in self._sleepers:\n                self._sleepers.remove(sleeper)\n",
        "            pass\n",
    ),
]


def check_patterns(root: Path = BACKEND) -> list[str]:
    """Mutations whose `old` text does not occur exactly once."""
    problems = []
    for m in MUTATIONS:
        patterns = ((m.old, m.new), *m.additional)
        source = (root / m.path).read_text()
        for old, _ in patterns:
            count = source.count(old)
            if count != 1:
                problems.append(f"{m.name}: pattern found {count} times in {m.path}")
    return problems


def _copy_backend(dest: Path) -> Path:
    ignore = shutil.ignore_patterns(
        "crates", "target", "__pycache__", ".venv", "*.db", "*.db-*"
    )
    shutil.copytree(BACKEND, dest, ignore=ignore)
    return dest


def _run_tests(root: Path) -> subprocess.CompletedProcess | None:
    """The barge-in modules against `root`; None if they hung. No `-x`: with
    it pytest reports a collection error as exit 1, indistinguishable from
    failing tests."""
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                *TEST_MODULES,
            ],
            cwd=root,
            env={**os.environ, "CI": "1"},
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None


def classify(returncode: int | None, output: str) -> str:
    """'passed', 'killed' (tests failed) or 'error' (the tests did not run
    properly: hang, collection/import error, an erroring test, usage error).
    Only 'killed' is evidence that a test detects the mutation."""
    if returncode is None:
        return "error"
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    if re.search(r"\berrors?\b", summary) or returncode not in (0, 1):
        return "error"
    return "killed" if returncode == 1 else "passed"


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
        base = classify(
            baseline and baseline.returncode, baseline.stdout if baseline else ""
        )
        if base != "passed":
            tail = (
                (baseline.stdout[-2000:] + baseline.stderr[-2000:])
                if baseline
                else "timed out"
            )
            print(
                f"Baseline (no mutation) did not pass ({base}); nothing can be "
                f"concluded. Interpreter: {sys.executable}\n{tail}"
            )
            return 2
        originals = {p: (root / p).read_text() for p in {m.path for m in selected}}
        for m in selected:
            for path, text in originals.items():
                (root / path).write_text(text)
            target = root / m.path
            mutated = target.read_text()
            for old, new in ((m.old, m.new), *m.additional):
                mutated = mutated.replace(old, new, 1)
            target.write_text(mutated)
            run = _run_tests(root)
            outcome = classify(run and run.returncode, run.stdout if run else "")
            if outcome == "error":
                what = (
                    "hung" if run is None else f"pytest exit {run.returncode}, errors"
                )
                verdict = f"ERROR ({what}; not evidence either way)"
                failed = True
            elif outcome == "killed":
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
