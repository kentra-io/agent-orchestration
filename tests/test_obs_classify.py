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


def test_bare_provider_exit_no_diagnostics_is_resumable():
    """Issue #7 remaining tail: the historical empty-diagnostics shape must
    not fold to a bare, remedy-less `unknown` — completed milestones are
    checkpointed, so resume is safe-by-design."""
    v = classify(1, "", "claude subprocess exited 1 (no diagnostics)", None)
    assert v.kind == "provider-exit"
    assert v.remedy is not None
    assert "orch resume" in v.remedy


def test_bare_provider_exit_no_diagnostics_different_exit_code():
    v = classify(137, "", "claude subprocess exited 137 (no diagnostics)", None)
    assert v.kind == "provider-exit"
    assert "orch resume" in v.remedy


def test_oauth_expiry_unchanged_even_though_it_names_a_subprocess_exit():
    """Regression pin: a recognizable oauth-expired shape must keep its
    existing classification even if it also happens to mention a subprocess
    exit AND the empty-diagnostics placeholder — the specific pattern takes
    precedence over the bare-exit shape by branch order, not because the
    text fails to match the provider-exit pattern too."""
    v = classify(
        1,
        "claude subprocess exited 1: OAuth session expired and could not be "
        "refreshed (no stderr or stdout diagnostics)",
        "",
        None,
    )
    assert v.kind == "oauth-expired"
    assert "cb login" in v.remedy


def test_fully_unrecognizable_garbage_stays_bare_unknown():
    """Garbage that does NOT match the subprocess-exit shape stays a bare,
    remedy-less `unknown` — deliberately conservative: only the specific
    empty-diagnostics provider-exit shape earns a resume suggestion, since
    genuinely uncharacterized text carries no evidence resume is the right
    call (as opposed to e.g. a real bug in the change)."""
    v = classify(3, "something odd", "boom", None)
    assert v.kind == "unknown"
    assert v.remedy is None
