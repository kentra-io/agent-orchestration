from orchestration.launch.change import build_conductor_argv


def test_argv_without_web_is_unchanged():
    argv = build_conductor_argv(
        conductor_bin="conductor", workflow="w.yaml", silent=True, provider=None, inputs={}
    )
    assert "--web" not in argv and "--web-port" not in argv


def test_argv_with_web_appends_flags():
    argv = build_conductor_argv(
        conductor_bin="conductor",
        workflow="w.yaml",
        silent=True,
        provider="stub",
        inputs={"a": "1"},
        web=True,
        web_port=42001,
    )
    i = argv.index("--web")
    assert argv[i + 1 : i + 3] == ["--web-port", "42001"]
