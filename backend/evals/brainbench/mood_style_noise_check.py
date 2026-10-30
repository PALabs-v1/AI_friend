"""Seeded noise check for the slice 4b measure (`mood_style.py`). No model.

Question: under an assumed reply-length noise model, how often does the
analysis return each verdict, and which rule is the one to watch? The model is
an assumption, not a measurement: each prompt has a fixed log-length (normal,
mean ln 40 words, sd `PROMPT_SD`), each reply adds lognormal noise (sd
`REPLY_SD`), and with the flag on the reply gains `lift` words at clamp +0.8 and
loses `lift` words at -0.8. Real Claude Code replies may be noisier or quieter,
so the counts below say how the rules behave, not what a real run will show.

Run: `python -m evals.brainbench.mood_style_noise_check` (from `backend/`).
"""

from __future__ import annotations

import math
import random
from collections import Counter

from evals.brainbench import mood_style as ms

PROMPT_SD = 0.5
REPLY_SD = 0.35
MEAN_WORDS = 40
BASE_SEED = 20261030
RUNS = 60
LIFTS = (0, 10, 20, 40)


def simulate_run(lift: int, seed: int) -> list[ms.Record]:
    rng = random.Random(seed)
    prompts = ms.PROMPT_COUNT
    log_mean = [
        math.log(MEAN_WORDS) + rng.gauss(0.0, PROMPT_SD) for _ in range(prompts)
    ]
    records: list[ms.Record] = []
    for flag_on in (False, True):
        for repeat in range(ms.REPEATS):
            for clamp in (ms.CLAMP_POS, ms.CLAMP_NEG, ms.CLAMP_PLACEBO):
                sign = 1 if clamp > 0 else -1 if clamp < 0 else 0
                for index in range(prompts):
                    words = math.exp(log_mean[index] + rng.gauss(0.0, REPLY_SD))
                    if flag_on:
                        words += lift * sign
                    words_final = max(1, round(words))  # tokens taken as words
                    records.append(
                        ms.Record(
                            ms.SEEDS[index // ms.PROMPTS_PER_SEED],
                            index,
                            flag_on,
                            clamp,
                            repeat,
                            words_final,
                            False,
                            words_final >= ms.TOKEN_CAP,
                        )
                    )
    return records


def check(lift: int, runs: int = RUNS, permutations: int = ms.PERMUTATIONS) -> dict:
    status: Counter[str] = Counter()
    placebo_false_fail = 0
    for k in range(runs):
        result = ms.analyze(
            simulate_run(lift, BASE_SEED + k), permutations=permutations
        )
        status[result["status"]] += 1
        if not result["rules"]["placebo_below_noise"]:
            placebo_false_fail += 1
    return {
        "lift": lift,
        "runs": runs,
        "pass": status["pass"],
        "fail": status["fail"],
        "void": status["void"],
        "placebo_rule_failed": placebo_false_fail,
    }


def main() -> None:
    print(f"prompt_sd={PROMPT_SD} reply_sd={REPLY_SD} runs={RUNS} seed={BASE_SEED}+k")
    for lift in LIFTS:
        print(check(lift))


if __name__ == "__main__":
    main()
