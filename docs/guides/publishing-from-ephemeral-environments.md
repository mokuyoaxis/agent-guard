# Publishing to GitHub from ephemeral environments

A field guide for pushing a repository from a sandbox, container, or cloud
agent workspace - written from a live session where every step was exercised.
Your computer is broken; your project still ships.

## Overview

```
sandbox workspace ──▶ deploy key (repo-scoped, revocable)
      │                   +
      ├─ bundle backup    fingerprint-pinned known_hosts
      ▼                   +
GitHub ◀────────────── GIT_SSH_COMMAND (IPv4 forced)
```

## 1. Use a deploy key, not an account key

A private key living in an ephemeral environment is a consumable, not an
asset. Scope the blast radius:

- **Deploy key** binds to ONE repository. Leaked = one repo exposed,
  revocable independently in the repo's Settings → Deploy keys.
- An account-level SSH key exposes EVERY repository you own.

Generate inside the workspace boundary:

```bash
mkdir -p .deploy-keys && chmod 700 .deploy-keys
ssh-keygen -t ed25519 -f .deploy-keys/id_ed25519 -N "" \
  -C "user/repo deploy key (sandbox)"
chmod 600 .deploy-keys/id_ed25519
```

No passphrase keeps non-interactive pushes working; compensating controls
are the tiny scope and instant revocation. (A passphrase would require
interactive pushes or ssh-agent - pick deliberately.)

## 2. Pin the host fingerprint - never trust-on-first-use

Fetch GitHub's official fingerprints from their meta endpoint and compare
against the live handshake BEFORE writing known_hosts:

```bash
curl -s https://api.github.com/meta | python3 -c \
  "import json,sys;print(json.load(sys.stdin)['ssh_key_fingerprints']['ed25519'])"
ssh-keyscan -t ed25519 github.com 2>/dev/null | ssh-keygen -lf -
# both must print: SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
ssh-keyscan -t ed25519 github.com 2>/dev/null > .deploy-keys/known_hosts
chmod 600 .deploy-keys/known_hosts
```

## 3. Push through GIT_SSH_COMMAND (no ~/.ssh needed)

Everything stays inside the workspace boundary - no writes outside it:

```bash
export GIT_SSH_COMMAND='ssh -4 -i .deploy-keys/id_ed25519 \
  -o UserKnownHostsFile=.deploy-keys/known_hosts -o IdentitiesOnly=yes'
git remote add origin git@github.com:user/repo.git
git push -u origin main
```

Field notes from a real session:

- **Force IPv4 (`-4`).** Dual-stack environments occasionally fail host-key
  verification over IPv6 while IPv4 works; the flake costs minutes.
- If a push fails transiently ("repository exists" style errors), simply
  retry - the second attempt carried what the first dropped.
- `ssh -T git@github.com` always exits 1 even on success; read the
  greeting. Deploy keys greet as `Hi user/repo!`.

## 4. On the GitHub side (do these yourself - account operations)

1. Create the repository EMPTY: no README, no .gitignore, no license -
   anything initialized will diverge from your local history.
2. Repo → Settings → Deploy keys → Add deploy key → paste the `.pub`
   content → **check "Allow write access"** (default is read-only).
3. Settings → Emails → enable both privacy checkboxes so a misconfigured
   future commit cannot leak your real address.

## 5. Back up before rewriting anything

Before identity rewrites or force operations, snapshot all refs:

```bash
git bundle create ../backup.bundle --all
```

Restore later with `git clone backup.bundle restored/`. Keep the bundle
outside the repository directory.

## 6. Identity hygiene

Use GitHub's noreply address (`ID+username@users.noreply.github.com`) in
commits - it becomes permanently public in every commit. Rewriting identity
before first push is safe; after push it means force-push coordination.

---

*This document exists because agent-guard itself blocked its own maintainer
twice while being published from a sandbox - once correctly (the boundary),
once due to a parser bug (F5/F6). Both are why the guide prefers adapting
operations to fit boundaries over weakening them.*
