# ADR-0001: Protected `main` with the status check `ci`

- Status: accepted
- Date: 2026-09-19
- Requirement: UC NF8 (commit hygiene), NF9 (merge gate), SPEC AK19; task T-2026-CU-9130-003

## Context

Many people or tools can work on z-finance at the same time. Each one works on a separate branch.
A change must pass the automated gates and a review before it goes into `main`.

The gates run two times, and this is intentional. The pre-commit hooks give fast feedback on the local computer.
The CI job `ci` (`.github/workflows/ci.yml`) runs the same gates. A contributor cannot skip that
job with `--no-verify` or `SKIP=`.

## Decision

The protection rules of `main` are these:

- **Status check:** `ci` must pass. It runs pre-commit on all files and gitleaks over the full
  history. It also runs the pull-request title gate, the docs gate, pytest, the frontend build and
  the i18n check. A branch must contain the last commit of `main` before the merge (`strict`).
- **Review:** the maintainer reviews each pull request before the merge.
- **Linear history:** a merge is a squash or a rebase. The squash subject is the pull-request
  title. CI checks that title against the conventional-commit rule.
- **No bypass:** `enforce_admins` is on. Force pushes and branch deletion are off. Nobody uses
  `--admin` merges.
- **No approval rule in GitHub:** the repository has one maintainer. GitHub does not let an author
  approve a pull request of their own.

## Apply the protection

Do this one time, after the first green `ci` run on `main`. GitHub shows a status check only after
it reports one time. You must use `gh` as a repository admin. Use your repository name for
`<owner>/<repo>`.

```sh
gh api --method PUT repos/<owner>/<repo>/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": { "strict": true, "contexts": ["ci"] },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
EOF
```

A squash merge uses the pull-request title as its subject:

```sh
gh api --method PATCH repos/<owner>/<repo> \
  -F allow_squash_merge=true -F allow_rebase_merge=true -F allow_merge_commit=false \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY
```

Check the result:

```sh
gh api repos/<owner>/<repo>/branches/main/protection \
  --jq '{checks: .required_status_checks.contexts, linear: .required_linear_history.enabled, admins: .enforce_admins.enabled}'
# expected: {"admins":true,"checks":["ci"],"linear":true}
```

## Consequences

- A pull request goes into `main` only when `ci` is green on its last commit and the maintainer
  reviewed it.
- A branch that does not contain the last commit of `main` must get it first. This runs `ci` again.
- GitHub refuses a force push to `main`. Thus nobody can change the history of `main`.
