---
id: ADR-0006
title: Agent-box Claude auth is a dedicated long-lived setup-token, never a copied session
category: security
date: 2026-07-27
status: accepted
---

## Context and Problem Statement

Mode-B agent boxes need working Claude auth, and until now they received it
as a snapshot of the host's OAuth session (a copied
`~/.claude/.credentials.json` materialized into the box, or `cb login`).
Harness issue #3 forensics (kentra-io/harness#3) showed why this is
structurally broken: Claude OAuth refresh tokens are single-use and
rotating, so a credential snapshot *forks* the session. Whichever holder
refreshes first wins; the live source session (the human's interactive use)
wins the race and the box's copy is invalidated server-side — observed as
`expiresAt: 0` alongside a locally-"valid" refresh token. The 2026-07-23
"running box self-refreshes" spike conclusion was disproven under
concurrency: it held only while no concurrent refresher existed. The host
keychain and `~/.claude/.credentials.json` are two independent OAuth
sessions, compounding the confusion. What credential custody chain should
orchestration boxes use so agent auth cannot be killed by an interactive
session?

## Decision Drivers

- No refresh-rotation race: box auth must not share a rotating token with
  any interactive session (the root cause of harness#3).
- Subscription OAuth only — P11 stands: never `ANTHROPIC_API_KEY` for cast
  agents.
- Secret hygiene: the token must not appear in argv, image layers, or a
  credentials file baked into the box.
- Operability: rotation should be a rare, explicit, single operation with a
  clear failure signature and remedy.
- Interactive (non-orchestration) boxes keep their existing ergonomics.

## Considered Options

1. **Dedicated long-lived setup-token, env-injected by value** — mint a
   1-year non-rotating `claude setup-token`, keep it in the macOS Keychain,
   pass it by value into the daemon, map it per-invocation into each box;
   boxes carry no credentials file.
2. **Keep snapshotting the host OAuth session** into each box (status quo).
3. **Per-box `cb login`** — each orchestration box holds its own
   interactive OAuth session.
4. **API key auth** (`ANTHROPIC_API_KEY`) for agent boxes.

## Decision Outcome

Chosen option: **1 — dedicated long-lived setup-token, never a copied
session**. The custody chain is:

- **Mint & store**: macOS Keychain, service `kentra-orch-claude-token`,
  minted ~yearly via `orch auth mint` from `claude setup-token` — a 1-year
  NON-rotating subscription credential, so no refresh race is possible.
- **Daemon ingest**: read host-side by `orch daemon start` and passed BY
  VALUE as `CLAUDE_CODE_LONG_LIVED_TOKEN` into the daemon container.
- **Per-invocation mapping**: mapped to `CLAUDE_CODE_OAUTH_TOKEN` only at
  each `claude` invocation via bare-name `docker exec -e` forwarding — the
  secret never appears in argv.
- **Boxes are credential-free**: orchestration boxes are created with
  claudebox `provisioning.env_auth: true` and carry NO credentials file.
- **Scope**: `cb login` / file-snapshot credentials remain valid for
  interactive (non-orchestration) boxes only.

Consequences: rotation is a yearly `orch auth mint` (with a 330-day
staleness warning); a daemon restart is required to pick up a new token; a
failed box auth probe means the token itself is bad and surfaces as
`needs-attention: auth` plus the `needs-human-input` GitHub label — the
remedy is mint + daemon restart + resume, never `cb login` on an env-auth
box. Option 2 is rejected as the disproven root cause; option 3 multiplies
rotating sessions and manual logins; option 4 violates P11.

## Rule

Orchestration agent boxes MUST authenticate Claude via the dedicated long-lived setup-token custody chain (Keychain `kentra-orch-claude-token` minted by `orch auth mint` → `orch daemon start` → by-value `CLAUDE_CODE_LONG_LIVED_TOKEN` → per-invocation `CLAUDE_CODE_OAUTH_TOKEN` via bare-name `docker exec -e`), MUST be created with `provisioning.env_auth: true` and carry no credentials file, and MUST NOT receive a copied/snapshotted OAuth session — `cb login` and file-snapshot credentials are for interactive boxes only.
