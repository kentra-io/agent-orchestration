"""execute-change launcher (worktree + cb run + conductor run wiring) — filled in M8.

`checkpoint_env` (M5) is the one piece of the launcher built early: the
TMPDIR-relocation half of P4/ADR-0002 that a workflow *template* cannot
discharge on its own (there is no YAML key for "where"). See
`orchestration/launch/checkpoint_env.py` and `workflows/README.md`'s
ADR-0002 reconciliation note.
"""
