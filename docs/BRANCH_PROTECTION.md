# Branch protection and repository security

## Current status (checked 2026-11-03)

- **`main` is not protected.** There is no branch protection rule on
  `main` and no active repository ruleset, so a direct push to `main`
  currently succeeds with no CI check and no review. (Found by the
  previous audit: `main.protected = false`, no rulesets. Re-check the
  Settings > Branches page before applying the steps below — the
  state is cheap to confirm and this file records the last verified
  state, 2026-11-03.)
- **Workflow permissions are least-privilege.** `.github/workflows/ci.yml`
  declares `permissions: contents: read`, and the actions it uses are
  pinned to full commit SHAs (see the workflow file).
- **CI credentials are ephemeral.** The MySQL password in the workflow
  is for a throwaway CI-only test database with sample data, not a real
  secret, and is documented as such in the workflow.
- **Dependabot is configured** (`.github/dependabot.yml`) for pip and
  GitHub Actions, and its updates are tested by CI before merging.

## Recommended rule for `main`

This is a solo portfolio repository, so the rule should block
accidents, not require another person. Enable a branch protection
rule (or a repository ruleset — see below) with:

1. **Require a pull request before merging** — direct pushes to
   `main` are blocked; changes arrive through a PR. Do **not**
   require an approving review: there is no second maintainer, and
   requiring one would block the owner from merging their own work.
2. **Require status checks to pass before merging** — select the CI
   job (`validate` / "Lint, test, run the notebook, check MySQL
   results"). This is the important one: it makes the test suite,
   the notebook run, the CSV freshness check and the MySQL cross-check
   a precondition for anything reaching `main`.
3. **Block force pushes and branch deletion** on `main`.
4. Leave "Require linear history", "Require signed commits" and
   "Restrict who can push" **off** — they add friction without
   meaningful protection for this repository.

### Exact steps (branch protection rule)

1. Open https://github.com/omkark2404/ecommerce-funnel-analytics/settings/branches
2. Under **Branch protection rules**, click **Add branch protection rule**.
3. **Branch name pattern:** `main`.
4. Check **Require a pull request before merging** (leave "Required
   approving reviews" at 0 / unchecked).
5. Check **Require status checks to pass before merging**, click
   **Add checks**, and select the CI check for the `validate` job.
   The check only appears in the list after CI has run at least once
   on a PR against `main`.
6. Check **Block force pushes** and **Do not allow bypassing the
   above settings** (or leave bypass for the owner if you want an
   emergency escape hatch — but then the rule is only advisory).
7. Click **Create**.

### Alternative: repository ruleset

Settings > Rules > Rulesets > **New ruleset** (repository ruleset,
target `main`): add the same requirements (pull request, required
status check, block force push / deletion). Rulesets are the newer
mechanism and replace branch protection rules; use one or the other,
not both, or the two can conflict.

## Secret scanning

Under Settings > Code security and analysis, enable **Secret
scanning** (and **Push protection** if offered for this repository
type) so a committed credential is flagged. This repository has no
secrets in it — the only password-like value is the ephemeral CI
MySQL password — but scanning is free insurance. If secret scanning
is not available on the free plan for public repositories, the
manual alternative is to never commit `.env` (it is gitignored)
and to review `git diff` before pushing.

## What was deliberately not changed

- No settings were changed from here: the available GitHub
  connection cannot edit repository settings, and account-level
  security changes are out of scope. The steps above are for the
  owner to apply in about five minutes.
- No branch protection rule was enabled that would block the owner
  from maintaining the repository: the recommendation above requires
  a PR and green CI, but no second person's approval.
