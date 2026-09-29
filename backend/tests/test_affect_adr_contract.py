"""The W2 ADR records the runtime and measurement contract reviewers need."""

from pathlib import Path

ADR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "brain-research-v3"
    / "adr"
    / "ADR-W2-affect-input.md"
)


def test_affect_adr_pins_failure_scoring_deadline_and_flag_off_effects():
    text = ADR.read_text()

    assert "AFFECT_VALENCE_ESTIMATOR_TIMEOUT_S` defaults to `0.15` seconds" in text
    assert "Failures are scored as `0.0`" in text
    assert "every seed" in text
    for finding in ("A-4 / NF-17", "A-5 / NF-16", "A-6 / DR-025", "F-009"):
        assert f"| {finding} |" in text
    for metric in (
        "persistence",
        "decay",
        "recovery",
        "saturation",
        "One conversation's long-term effect",
    ):
        assert metric in text
