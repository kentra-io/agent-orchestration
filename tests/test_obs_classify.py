"""Classifier fixtures use the REAL observed error texts from the two
incidents (harness tasks/orchestration-box-auth-expiry.md and
tasks/orchestration-transient-api-error-kills-run.md)."""

from orchestration.obs.classify import classify


def test_exit_zero_is_success():
    v = classify(0, "", "", None)
    assert v.kind == "success" and v.remedy is None


def test_gate_pause_from_checkpoint_agent():
    v = classify(1, "", "", "human_gate")
    assert v.kind == "gate-pause"


def test_gate_pause_from_eoferror_tail():
    v = classify(1, "", "EOFError: EOF when reading a line", None)
    assert v.kind == "gate-pause"


def test_oauth_expiry():
    v = classify(1, "OAuth session expired and could not be refreshed", "", None)
    assert v.kind == "oauth-expired"
    assert "cb login" in v.remedy


def test_api_transient():
    v = classify(1, "API Error: Connection closed mid-response", "", None)
    assert v.kind == "api-transient"
    assert "resume" in v.remedy


def test_unknown_keeps_detail():
    v = classify(3, "something odd", "boom", None)
    assert v.kind == "unknown"
    assert "something odd" in v.detail and "boom" in v.detail
