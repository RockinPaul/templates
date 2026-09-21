# scripts

## `assess_template_updates.py`

Weekly check of whether the upstream projects wrapped by the Railway templates in
[README.md](../README.md) have moved in a way that is worth propagating into the
templates. It runs from
[`.github/workflows/weekly-template-assessment.yml`](../.github/workflows/weekly-template-assessment.yml)
every Friday at 07:00 UTC (and on demand via *Run workflow*), and commits its
output to [`reports/`](../reports/):

| File | Purpose |
|---|---|
| `reports/YYYY-MM-DD.md` | the report for that run |
| `reports/YYYY-MM-DD.json` | the same data, machine-readable |
| `reports/latest.md` | a copy of the newest run, for a stable link; the next run diffs against the newest **dated** report older than itself |
| `reports/README.md` | index of all reports with verdict counts |

### What it does, per template

1. Shallow-clones the **template** repository and reads the upstream version it
   pins (Dockerfile `FROM` tags, `ARG` versions, `pip install x==y`, image
   digests), as described in [`templates.yaml`](../templates.yaml). The pin is
   read live, so bumping a template needs no manifest change.
2. Makes a bare, blob-less partial clone of the **upstream** repository (cheap,
   no API token needed) and resolves the pin to a commit: a tag, a commit SHA,
   or, for digest-pinned images, the registry tag whose manifest digest matches.
3. Compares the pinned commit with the upstream default branch:
   - releases (tags matching `tag_pattern`) that are newer than the pin and not
     ancestors of it; pre-releases only count when the pin itself is one,
   - commits ahead of the pin,
   - commits touching *template-relevant* paths (Dockerfiles, compose files,
     env examples, entrypoints, deploy/helm directories, migrations, dependency
     manifests, plus per-upstream extras such as `cognee-mcp/**`), with test
     code excluded,
   - commits touching files the template **patches** (rebase risk; Fabric),
   - security fixes and deployer-impacting changes among the commits since the
     pin. With `TYPESAFE_API_KEY` set these are **judged by TypeSafe Jev**
     (see below); otherwise commit messages are matched against keyword
     regexes (security advisories, CVEs, injection, traversal, …; breaking
     changes, deprecations, migrations), which fire on website copy and miss
     fixes whose message names no keyword,
   - PyPI's current version for pip-installed upstreams.
4. Enriches newer releases with GitHub release notes when `GITHUB_TOKEN` is
   available, otherwise with annotated-tag messages and the CHANGELOG lines added
   since the pin.
5. Scores the signals and emits a verdict with plain-language reasons.

### Verdicts

| Verdict | Meaning |
|---|---|
| 🔴 update recommended | score ≥ 8, or security-related commits that are already part of a newer release |
| 🟠 review | score 3–7; or pinned at the newest release while unreleased security fixes or patched-file changes sit on the default branch |
| 🟡 minor drift | score 1–2; or pinned at the newest release with only unrelated unreleased work upstream |
| 🟢 up to date | the pin is the default branch head and there is no newer release |
| ⚪ unknown | the pin could not be resolved (pattern did not match, tag missing); the report says why |

Score contributions: newer releases +2 each (max 6, +1 more if the major version
component moved); security-related commits +4; commits touching patched files
+3; template-relevant commits +1 each (max 5); breaking-change mentions +1;
commits ahead +1 per 25 (max 3); pin older than 90 days +2; disagreeing pins
inside one template +2. A PyPI version newer than the pin adds +2 when no git
release reflects it. Upstreams without a resolvable pin (Notesnook's monograph
image) are assessed on the activity of the last 90 days in their watched paths
instead.

The heuristics are deliberately conservative about *what* to look at, not about
*whether* to update: a red verdict means "open the template repo and look",
not "bump blindly" (see the guide's §1 on why a version bump is not proof of
compatibility).

### Commit judgments (TypeSafe)

[`judgments.py`](judgments.py) asks TypeSafe's System One model (`jev-1.13.0`,
pinned) two questions about every commit since the pin, eight commits per
request, standard library only:

| Question | Type | Used as |
|---|---|---|
| Does the commit fix or harden against a security weakness? | probability | ≥ 0.8 counts as a security fix (+4 as before); 0.5–0.8 is listed as "possible" but not counted |
| What does the change mean for someone running the software from a container with their own configuration? | 3-level score | ≥ 1.5 "breaks existing deployments / manual step" (+1, replaces the breaking-mention regex); 0.9–1.5 "worth knowing"; both listed under *Deployer impact* |

Measured on the 21 Sep 2026 corpus (6,022 commits): the security regex had
about 50% precision and caught 26 of the 87 commits Jev rated ≥ 0.8; the
breaking-change regex was operator-relevant for about 30% of its hits. The
whole corpus cost $0.18 and 87 seconds. Judgments are cached by sha in
`<cache-dir>/judgments.json`, so a weekly run pays only for new commits.
Score contributions are unchanged; only the inputs are.

Enable by exporting `TYPESAFE_API_KEY` (a GitHub Actions secret of the same
name in CI). Force the heuristics with `--no-judgments` or
`TEMPLATES_JUDGMENTS=off`. If the API fails mid-run, the affected upstream
falls back to the regexes and says so under *Problems*. Tests:
`python3 -m unittest discover -s scripts -p 'test_*.py'` (add
`TEMPLATES_LIVE_TESTS=1` for one real request).

### Running locally

```bash
pip install pyyaml
python scripts/assess_template_updates.py                 # full run, writes reports/
python scripts/assess_template_updates.py --only fabric   # one template
python scripts/assess_template_updates.py --no-refresh    # reuse cached clones in .cache/
GITHUB_TOKEN=ghp_… python scripts/assess_template_updates.py   # with release notes
TYPESAFE_API_KEY=… python scripts/assess_template_updates.py    # with Jev commit judgments
```

Clones live in `.cache/` (git-ignored) and are reused between runs. A full cold
run takes two to three minutes.

### Adding or changing a template

Edit [`templates.yaml`](../templates.yaml). Each upstream needs the GitHub
`repo`, at least one `pins` entry pointing at a file in the template repo with a
regex containing a `(?P<version>…)` group, and `tag_formats` describing how that
version maps to an upstream tag (`v{version}`, `{version}`). Optional keys are
documented at the top of the manifest. Run the script with `--only <name>` to
check that the pin resolves before committing.
