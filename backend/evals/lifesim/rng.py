"""Named, independent random streams derived from one persona seed.

Each process draws from its own stream, so adding a new process or changing
how many numbers one process consumes never shifts the output of another.
String seeding in `random.Random` hashes with SHA-512, so it is stable across
interpreter runs and unaffected by PYTHONHASHSEED.
"""

from __future__ import annotations

import random


def stream(seed: int, *names: str) -> random.Random:
    return random.Random("lifesim:" + str(seed) + ":" + ":".join(names))
