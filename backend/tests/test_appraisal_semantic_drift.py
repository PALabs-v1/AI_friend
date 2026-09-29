from app.cognitive.appraisal import AppraisalEngine


def test_echo_prone_system2_semantic_appraisal_is_removed():
    """F-009 regression: no duplicate LLM path can overwrite stage-4 affect."""
    assert not hasattr(AppraisalEngine, "appraise_semantic_drift")
