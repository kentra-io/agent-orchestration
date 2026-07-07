---
name: lifecycle-archive
description: Archive a fully-gated spec-lifecycle change — conflict-check, native fold into the living spec, relocate to changes/archive/, and a post-archive guard run. Invoke explicitly with /lifecycle-archive.
disable-model-invocation: true
---

# lifecycle-archive

Archive one fully-gated change. Everything here is native to `lifecycle` —
there is no `openspec archive` to shell out to (spec-lifecycle.md §6.2).

## Procedure

1. **Confirm every required gate is approved.** Run
   `lifecycle status --change <change>`. For a feature: `refine`,
   `design` (unless `designSkipped: true` on the refine entry), and `plan`
   must all show `approved`. For a bug: `repro` (and `fix`, if the fix
   stage ran) must show `approved`; a promoted bug additionally needs
   `design`/`plan`. If anything required is `pending` or `rejected`, stop
   and send the change back to the relevant stage skill — do not reach for
   `--force-gates` to work around a gate that simply hasn't happened yet.
2. **Run the archive:**
   ```
   lifecycle archive <change>
   ```
   This gate-checks (refusing on any un-approved required stage),
   conflict-checks (refusing loudly if another in-flight change's delta
   touches a requirement — `MODIFIED`/`REMOVED`/`RENAMED` — that this
   change also touches; never a silent drop), records pre-image digests,
   folds the delta into `openspec/specs/<capability>/spec.md` (a
   delta-less bug change skips the fold), relocates the folder to
   `openspec/changes/archive/<change>/`, records post-image digests,
   appends the ledger record(s) with the next monotonic `seq`, and then
   runs the full `lifecycle guard` as a post-archive self-check.
3. **Interpret the exit code:**
   - `0` — archived cleanly and the post-archive guard self-check passed.
     Tell the human it archived; nothing further to do.
   - `1` — refused: an un-approved required gate, a genuine cross-change
     conflict, or a fold failure. Nothing was written. Go fix the
     underlying gate/conflict and retry.
   - `2` — either could not run at all (bad flags, no `openspec/` tree,
     missing change folder, an environment failure — nothing was written;
     fix the environment issue and retry), **or** the archive already
     committed but the post-archive guard self-check found a problem
     afterward. Read the message carefully — these are different
     situations. If the archive already committed (fold + ledger append
     already happened), run `lifecycle guard --format json` yourself to
     see exactly which check failed (`archive_mutated` / `projection_drift`
     / `chain_break` / `ledger_missing`) and escalate to the human — do not
     retry the archive and do not try to self-repair the ledger or the
     archived folder.

## Never

- Never hand-edit anything under `openspec/changes/archive/` — it is the
  append-only event log; `lifecycle guard`'s immutability check exists to
  catch exactly this.
- Never pass `--force-gates` or `--force-conflicts` without the human's
  explicit, informed approval of what is being overridden — both flags are
  recorded permanently on the resulting ledger record(s), so the human is
  approving a durable admission, not a one-off convenience.
- Never treat archive exit `0` output alone as sufficient if you separately
  notice the post-archive guard step reported anything but clean — surface
  it to the human immediately rather than moving on.
