# GitHub integration for transfr

Everything the `investigate-issue` skill needs in order to talk to GitHub, plus
how to get it working from a clean machine. The skill drives GitHub entirely
through the **`gh` CLI** (never raw tokens in the shell), so this doc is about
getting `gh` authenticated and understanding what it's allowed to do.

Repo: **`scherven/transfr`** — public, issues enabled. `origin` already points at
`https://github.com/scherven/transfr.git`, so `gh` picks the repo up
automatically when run from inside the working tree.

---

## 1. Why `gh` (and not tokens or the API directly)

- `gh` stores credentials in the OS keychain and refreshes them; we never paste a
  personal access token onto a command line where it can leak into shell history,
  process listings, or a transcript.
- It resolves the repo from the git remote, so commands don't need `--repo`
  spelled out every time (the skill passes it anyway, to be explicit and safe
  inside worktrees).
- Claude is **not permitted to enter credentials, passwords, or tokens** on your
  behalf. Authentication is always something *you* run in your own terminal. The
  steps below are for you, the human, to execute.

## 2. Check current state first

```bash
gh auth status
```

Three outcomes:

- **Logged in, correct account, valid token** → you're done, skip to §5.
- **`The token in ... is invalid`** → re-authenticate, §3. (This is the state the
  repo was in when the skill was written: an account `Innoviox` with a dead
  token.)
- **Logged in as the wrong account** (an account without push access to
  `scherven/transfr`) → §4.

You can read a public repo's issues even while unauthenticated, but **opening a
PR, pushing a branch, or commenting requires a valid login** with push access.
`scherven/transfr` is your own repo, so log in as `scherven` (or any account
added as a collaborator).

## 3. Log in (or re-authenticate a dead token)

Run this yourself — it's interactive and involves a browser:

```bash
gh auth login
```

Answer the prompts:

- **Account** → `GitHub.com`
- **Protocol** → `HTTPS` (matches the existing `origin` remote; avoids needing an
  SSH key)
- **Authenticate Git with your GitHub credentials?** → **Yes** (so `git push` uses
  the same login — required for the PR flow)
- **How would you like to authenticate?** → *Login with a web browser* is easiest;
  paste the one-time code it shows into the page that opens.

When it asks about **scopes**, make sure the token includes **`repo`** (full
control of private and public repos — needed to push branches and open PRs) and
**`read:org`** if the repo ever moves under an org. The browser flow requests
sensible defaults; the token-paste flow does not, so prefer the browser flow.

If a stale account is wedging things, clear it first:

```bash
gh auth logout -h github.com -u Innoviox   # forget the dead 'Innoviox' login
gh auth login                              # then log in fresh
```

## 4. Multiple accounts on one machine

`gh` can hold several logins and switch between them:

```bash
gh auth status          # lists every account, marks the active one
gh auth switch          # interactively pick which account is active
gh auth switch --user scherven
```

The **active** account is the one every `gh` command uses. Before running the
skill, make sure the active account has push access to `scherven/transfr` —
otherwise investigation and reading work, but the PR/comment step will 403.

## 5. Verify it end to end

```bash
gh auth status                              # active account, valid token, has 'repo' scope
gh repo view scherven/transfr --json nameWithOwner,viewerPermission
```

`viewerPermission` should be `ADMIN`, `MAINTAIN`, or `WRITE`. If it's `READ`, the
active account can't open PRs on this repo — switch accounts (§4) or get added as
a collaborator. A quick read probe:

```bash
gh issue list --repo scherven/transfr --limit 5
```

## 6. Commands the skill relies on

Read-only (safe, run freely):

```bash
# Full issue payload as JSON — the skill parses this
gh issue view <N> --repo scherven/transfr \
  --json number,title,body,labels,state,author,comments,url,createdAt

gh issue list --repo scherven/transfr --state open --limit 30 \
  --json number,title,labels,updatedAt

# Cross-reference: related PRs or code search
gh search issues --repo scherven/transfr "<keywords>"
gh pr list --repo scherven/transfr --state all --limit 20
```

Write / outward-facing (the skill NEVER runs these without your explicit
go-ahead in chat — see the safety section of `SKILL.md`):

```bash
# Post a comment (issue triage / questions back to the reporter)
gh issue comment <N> --repo scherven/transfr --body-file <draft.md>

# Open a PR from an already-pushed branch
git push -u origin <branch>
gh pr create --repo scherven/transfr \
  --base main --head <branch> \
  --title "<title>" --body-file <pr-body.md>
```

`--body-file` is preferred over `--body` so multi-line Markdown (and anything
that looks like shell metacharacters) is passed verbatim, never re-interpreted by
the shell.

## 7. Auth model notes

- **HTTPS + `gh`-managed git credentials** is the path this repo uses. `git push`
  then authenticates through `gh`'s stored token — no SSH key required.
- If you prefer **SSH**, choose SSH in `gh auth login`, and change the remote:
  `git remote set-url origin git@github.com:scherven/transfr.git`. Everything else
  works the same.
- **Token scopes:** `repo` is the one that matters for pushing and PR creation.
  Fine-grained tokens also work but must grant *Contents: write*, *Pull requests:
  write*, and *Issues: write* on `scherven/transfr`.
- **Never** put a token in an env var in a committed file, or in a URL. `gh` keeps
  it in the keychain for a reason.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `The token ... is invalid` | expired/revoked token | `gh auth login` (§3) |
| `HTTP 403` on `pr create` / `issue comment` | active account lacks push access | `gh auth switch` to an account with WRITE (§4/§5) |
| `could not determine base repo` | run from outside the git tree | `cd` into the repo, or pass `--repo scherven/transfr` |
| `gh: command not found` | CLI not installed | `brew install gh` (macOS) |
| push prompts for username/password | git not wired to `gh` | re-run `gh auth login`, answer **Yes** to "Authenticate Git" |
