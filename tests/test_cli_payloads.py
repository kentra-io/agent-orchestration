from orchestration.cli import payloads


def test_production_payload_golden():
    assert payloads.production_payload(repo="/r", change_id="1-a") == {
        "repo": "/r",
        "change_id": "1-a",
        "box": {"enabled": True},
        "conductor": {},
        "wait": False,
    }


def test_production_payload_optionals():
    p = payloads.production_payload(repo="/r", change_id="1-a", branch="feat/x", issue=7)
    assert p["branch"] == "feat/x" and p["issue"] == 7


def test_production_payload_repo_gh_override():
    # The optional "owner/repo" override rides top-level, like issue/branch.
    p = payloads.production_payload(repo="/r", change_id="1-a", repo_gh="acme/widgets")
    assert p["repo_gh"] == "acme/widgets"
    # Absent by default -- the launcher derives it from the origin remote.
    assert "repo_gh" not in payloads.production_payload(repo="/r", change_id="1-a")


def test_stub_payload_golden():
    assert payloads.stub_payload(
        repo="/r",
        change_id="1-a",
        plan_fixture_path="/r/.orchestration-stub/1-a/plan.json",
        stub_script_path="/r/.orchestration-stub/1-a/stub_script.json",
    ) == {
        "repo": "/r",
        "change_id": "1-a",
        "box": {"enabled": False},
        "conductor": {
            "provider": "stub",
            "plan_fixture_path": "/r/.orchestration-stub/1-a/plan.json",
            "env": {"CONDUCTOR_STUB_SCRIPT": "/r/.orchestration-stub/1-a/stub_script.json"},
        },
        "wait": False,
    }
