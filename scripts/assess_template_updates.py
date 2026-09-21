#!/usr/bin/env python3
"""Assess whether the upstream projects wrapped by our Railway templates have
moved in a way that is worth propagating into the templates.

For every template listed in ``templates.yaml`` the script

1. clones the template repository (shallow) and reads the version each of its
   Dockerfiles / manifests pins for the upstream project,
2. clones the upstream repository (bare, blob-less partial clone, so it is
   cheap and needs no API token),
3. resolves the pin to an upstream commit (tag, commit SHA, PyPI version or
   container-image digest),
4. compares that commit with the upstream default branch: newer releases,
   commits ahead, commits touching template-relevant paths, commits touching
   files the template patches, security / breaking-change keywords,
5. turns the signals into a verdict with human-readable reasons, and
6. writes ``reports/<date>.md`` and ``reports/latest.md``
   and refreshes the index in ``reports/README.md``.

Only ``git`` and the Python standard library are required at runtime, plus
PyYAML for the manifest. When ``GITHUB_TOKEN`` is set, GitHub release notes are
pulled in as an enrichment; without it the CHANGELOG diff and annotated tag
messages are used instead.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - dependency error is reported plainly
    sys.exit("PyYAML is required: pip install pyyaml")

import judgments  # TypeSafe commit judgments (optional at runtime; see judgments.py)

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

GITHUB = "https://github.com"
USER_AGENT = "templates-update-assessment (+https://github.com/RockinPaul/templates)"

SECURITY_STRONG_RE = re.compile(
    r"(cve-\d{4}-\d+|ghsa-[\w-]+|\bvulnerab\w*|\bsecurity\s+(fix|patch|advisor\w*|issue|bug|hardening)|"
    r"\[security\]|\bsecurity:|\(security\)|fix\(security\)|\bexploit\w*|"
    r"\bauth(entication|orization)?\s+bypass|\bprivilege\s+escalat\w*|\bpath\s+traversal|\btraversal\b|"
    r"\b(sql|command|shell|code|header|template)\s+injection|\bxss\b|\bcsrf\b|\bssrf\b|\brce\b|"
    r"\bdenial[- ]of[- ]service|\b(secret|token|credential|password|api[- ]?key)s?\s+(leak|exposure|disclos\w*)|"
    r"\bconstant[- ]time\b|\btiming\s+attack)",
    re.IGNORECASE,
)
SECURITY_SUBJECT_RE = re.compile(r"\bsecurity\b|\binjection\b|\bsanitiz\w*|\binsecure\b|\bunauthori[sz]ed\b", re.IGNORECASE)
SECURITY_SKIP_SUBJECT_RE = re.compile(r"^(docs?|doc|chore\(docs\))\b|SECURITY\.md|\bREADME\b", re.IGNORECASE)
BOT_AUTHOR_RE = re.compile(r"dependabot|renovate|github-actions|\[bot\]|semantic-release|release-please", re.IGNORECASE)


def is_security_commit(author: str, subject: str, body: str) -> bool:
    """Heuristic: a commit worth calling out as security-related."""
    if SECURITY_SKIP_SUBJECT_RE.search(subject):
        return False
    if BOT_AUTHOR_RE.search(author):
        # dependency bumps are only interesting when the title itself names an advisory
        return bool(re.search(r"cve-\d{4}-\d+|ghsa-|security", subject, re.IGNORECASE))
    if SECURITY_STRONG_RE.search(subject) or SECURITY_SUBJECT_RE.search(subject):
        return True
    return bool(SECURITY_STRONG_RE.search(body[:1500]))


BREAKING_RE = re.compile(
    r"(BREAKING[ _-]CHANGE|\bbreaking\b|\bdeprecat\w*|\bdrop(s|ped)?\s+support|"
    r"\bremove[sd]?\s+(support|the\s+\w+\s+(flag|option|env|variable|setting))|"
    r"\brename[sd]?\s+(env|variable|setting|flag|option|config)|"
    r"\bmigrat(e|ion)s?\b|\bschema\s+change|\bnew\s+required\s+(env|variable|setting)|"
    r"\bconfig(uration)?\s+(format|change)|\bdefault\s+(port|user|path)\s+change)",
    re.IGNORECASE,
)
PRERELEASE_RE = re.compile(r"(rc|alpha|beta|dev|pre|preview|nightly|canary)", re.IGNORECASE)

DEFAULT_RELEVANT_PATHS = [
    "**/Dockerfile*",
    "**/docker-compose*",
    "**/compose*.y*ml",
    "**/.env*",
    "**/env.example",
    "**/*.env.example",
    "**/config.example.*",
    "**/entrypoint*",
    "deploy/**",
    "docker/**",
    "helm/**",
    "**/migrations/**",
    "**/alembic/**",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "**/requirements*.txt",
    "SECURITY.md",
]

VERDICT_ORDER = {
    "update-recommended": 0,
    "review": 1,
    "minor-drift": 2,
    "up-to-date": 3,
    "unknown": 4,
}
VERDICT_LABEL = {
    "update-recommended": "🔴 update recommended",
    "review": "🟠 review",
    "minor-drift": "🟡 minor drift",
    "up-to-date": "🟢 up to date",
    "unknown": "⚪ unknown",
}

MAX_LISTED_COMMITS = 10
MAX_LISTED_RELEASES = 6
MAX_LISTED_HITS = 6
NOTES_CHARS = 600

# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 600) -> str:
    proc = subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout


def git(repo: Path, *args: str, check: bool = True, timeout: int = 600) -> str:
    return run(["git", "-C", str(repo), *args], check=check, timeout=timeout)


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_head_header(url: str, header: str, headers: dict[str, str] | None = None, timeout: int = 30) -> str | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.headers.get(header)


def version_key(v: str) -> tuple:
    """Order versions like v1.2.3, 1.2.3rc1, v1.0-beta.36, 2026.9.3, RELEASE.2024-07-26T13-08-44Z.

    Numeric tokens sort numerically, alphabetic tokens sort before numbers so that
    ``1.3.0rc1`` < ``1.3.0`` and ``v1.0-beta.36`` < ``v1.0``.
    """
    s = v.strip()
    s = re.sub(r"^[vV]\.?", "", s)
    tokens = re.findall(r"\d+|[A-Za-z]+", s)
    key: list[tuple[int, Any]] = []
    for t in tokens:
        if t.isdigit():
            key.append((1, int(t)))
        else:
            key.append((0, t.lower()))
    # pad so that "1.3.0" > "1.3.0rc1": a trailing alpha token lowers the value
    key.append((1, 0))
    return tuple(key)


def is_prerelease(v: str) -> bool:
    return bool(PRERELEASE_RE.search(re.sub(r"^[vV]\.?", "", v)))


def days_between(a: dt.datetime, b: dt.datetime) -> int:
    return int((b - a).total_seconds() // 86400)


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def short(sha: str | None) -> str:
    return (sha or "")[:10]


def excerpt(text: str, limit: int = NOTES_CHARS) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\r", "", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " …"
    return text


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #


@dataclass
class CommitInfo:
    sha: str
    date: str
    subject: str
    files: list[str] = field(default_factory=list)
    # filled when TypeSafe judgments are enabled
    security: float | None = None            # probability the commit is a security fix
    impact: float | None = None              # 0 none, 1 worth knowing, 2 breaks existing deployments
    impact_confidence: float | None = None


@dataclass
class ReleaseInfo:
    tag: str
    date: str
    sha: str
    prerelease: bool
    notes: str = ""
    url: str = ""


@dataclass
class UpstreamResult:
    name: str
    repo: str
    template: str
    pin_kind: str
    pin_raw: str | None = None
    pin_source: str | None = None
    pin_sha: str | None = None
    pin_tag: str | None = None
    pin_date: str | None = None
    pin_age_days: int | None = None
    pin_mismatch: list[str] = field(default_factory=list)
    default_branch: str | None = None
    head_sha: str | None = None
    head_date: str | None = None
    commits_ahead: int = 0
    commits_behind: int = 0
    relevant_commits_total: int = 0
    relevant_commits: list[CommitInfo] = field(default_factory=list)
    patched_files: list[str] = field(default_factory=list)
    patched_file_commits_total: int = 0
    patched_file_commits: list[CommitInfo] = field(default_factory=list)
    security_hits_total: int = 0
    security_hits: list[CommitInfo] = field(default_factory=list)
    breaking_hits_total: int = 0
    breaking_hits: list[CommitInfo] = field(default_factory=list)
    security_possible_total: int = 0                      # judged 0.5-0.8: listed, not counted
    impact_breaking_total: int = 0                        # judged to break existing deployments
    impact_notable_total: int = 0                         # judged worth knowing, deployments keep working
    impact_hits: list[CommitInfo] = field(default_factory=list)
    judged_by: str | None = None                          # model id when Jev judged the commits, else None
    latest_release: str | None = None
    latest_release_date: str | None = None
    newer_releases_total: int = 0
    newer_releases: list[ReleaseInfo] = field(default_factory=list)
    pypi_latest: str | None = None
    pypi_latest_date: str | None = None
    activity_window_days: int | None = None
    changelog_excerpt: str = ""
    score: int = 0
    verdict: str = "unknown"
    reasons: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    url: str = ""


# --------------------------------------------------------------------------- #
# repositories
# --------------------------------------------------------------------------- #


class RepoCache:
    def __init__(self, root: Path, refresh: bool = True):
        self.root = root
        self.refresh = refresh
        (root / "upstreams").mkdir(parents=True, exist_ok=True)
        (root / "templates").mkdir(parents=True, exist_ok=True)

    def upstream(self, repo: str) -> Path:
        path = self.root / "upstreams" / (repo.replace("/", "__") + ".git")
        url = f"{GITHUB}/{repo}"
        if path.exists():
            if self.refresh:
                log(f"  fetching {repo}")
                git(path, "fetch", "--quiet", "--prune", "--force", "origin",
                    "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
                head = run(["git", "ls-remote", "--symref", url, "HEAD"]).split("\n")[0]
                m = re.match(r"ref: (refs/heads/\S+)\s+HEAD", head)
                if m:
                    git(path, "symbolic-ref", "HEAD", m.group(1))
        else:
            log(f"  cloning {repo} (bare, blob-less)")
            run(["git", "clone", "--quiet", "--bare", "--filter=blob:none", url, str(path)])
        return path

    def template(self, repo: str) -> Path:
        path = self.root / "templates" / repo.replace("/", "__")
        url = f"{GITHUB}/{repo}"
        if path.exists():
            shutil.rmtree(path)
        log(f"  cloning template {repo}")
        run(["git", "clone", "--quiet", "--depth", "1", url, str(path)])
        return path


def default_branch(repo: Path) -> str:
    ref = git(repo, "symbolic-ref", "--quiet", "HEAD", check=False).strip()
    return ref.replace("refs/heads/", "") or "HEAD"


def commit_date(repo: Path, sha: str) -> str:
    return git(repo, "show", "-s", "--format=%cI", sha).strip()


def list_tags(repo: Path, tag_pattern: str | None) -> list[ReleaseInfo]:
    out = git(repo, "for-each-ref", "--format=%(refname:short)%09%(creatordate:iso-strict)%09%(*objectname)%09%(objectname)", "refs/tags")
    rx = re.compile(tag_pattern) if tag_pattern else None
    tags = []
    for line in out.splitlines():
        name, date, peeled, obj = (line.split("\t") + ["", "", "", ""])[:4]
        if rx and not rx.match(name):
            continue
        tags.append(ReleaseInfo(tag=name, date=date, sha=peeled or obj, prerelease=is_prerelease(name)))
    return tags


def is_ancestor(repo: Path, maybe_ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", maybe_ancestor, descendant],
                          capture_output=True, text=True)
    return proc.returncode == 0


def rev_count(repo: Path, rng: str) -> int:
    out = git(repo, "rev-list", "--count", rng, check=False).strip()
    return int(out) if out.isdigit() else 0


EXCLUDE_PATHS: list[str] = []  # filled from the manifest defaults


def pathspecs(paths: Iterable[str], excludes: Iterable[str] | None = None) -> list[str]:
    specs = [f":(glob){p}" for p in paths]
    specs += [f":(glob,exclude){p}" for p in (EXCLUDE_PATHS if excludes is None else excludes)]
    return specs


def log_commits(repo: Path, rng: str, paths: list[str] | None = None, limit: int | None = None) -> list[CommitInfo]:
    cmd = ["log", "--no-merges", "--date=short", "--format=%H%x09%ad%x09%s", rng]
    if limit:
        cmd.insert(1, f"--max-count={limit}")
    if paths:
        cmd += ["--", *pathspecs(paths)]
    out = git(repo, *cmd, check=False)
    commits = []
    for line in out.splitlines():
        sha, date, subject = (line.split("\t", 2) + ["", ""])[:3]
        commits.append(CommitInfo(sha=sha, date=date, subject=subject))
    return commits


def count_commits(repo: Path, rng: str, paths: list[str]) -> int:
    out = git(repo, "rev-list", "--count", "--no-merges", rng, "--", *pathspecs(paths), check=False).strip()
    return int(out) if out.isdigit() else 0


def files_of(repo: Path, sha: str, paths: list[str], limit: int = 6, excludes: Iterable[str] | None = None) -> list[str]:
    out = git(repo, "show", "--format=", "--name-only", sha, "--", *pathspecs(paths, excludes), check=False)
    files = [f for f in out.splitlines() if f.strip()]
    if len(files) > limit:
        files = files[:limit] + [f"… +{len(files) - limit} more"]
    return files


def scan_commits(repo: Path, rng_args: list[str], paths: list[str] | None = None) -> list[tuple[str, str, str, str, str]]:
    """Yield (sha, date, author, subject, body) for the commits selected by rng_args."""
    cmd = ["log", "--date=short", "--format=%x1e%H%x09%ad%x09%an%x09%s%x1f%b", *rng_args]
    if paths:
        cmd += ["--", *pathspecs(paths)]
    out = git(repo, *cmd, check=False)
    records = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        head, _, body = record.partition("\x1f")
        sha, date, author, subject = (head.split("\t", 3) + ["", "", ""])[:4]
        records.append((sha, date, author, subject, body))
    return records


def keyword_hits(records: list[tuple[str, str, str, str, str]], kind: str) -> tuple[int, list[CommitInfo]]:
    hits = []
    for sha, date, author, subject, body in records:
        if kind == "security":
            ok = is_security_commit(author, subject, body)
        else:
            ok = bool(BREAKING_RE.search(subject) or BREAKING_RE.search(body[:1500]))
        if ok:
            hits.append(CommitInfo(sha=sha, date=date, subject=subject))
    return len(hits), hits[:MAX_LISTED_HITS]


def commit_files(repo: Path, rng_args: list[str], paths: list[str] | None = None) -> dict[str, list[str]]:
    """sha -> first files touched, for the commits selected by rng_args (one git call)."""
    cmd = ["log", "--format=%x1e%H", "--name-only", *rng_args]
    if paths:
        cmd += ["--", *pathspecs(paths)]
    out = git(repo, *cmd, check=False)
    files: dict[str, list[str]] = {}
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        sha, _, rest = rec.partition("\n")
        files[sha.strip()] = [l for l in rest.splitlines() if l.strip()][:judgments.MAX_FILES]
    return files


def apply_judgments(res: UpstreamResult, records: list[tuple[str, str, str, str, str]],
                    judged: dict[str, judgments.Judgment] | None) -> None:
    """Fill the security and deployer-impact fields of `res`: from Jev when judgments are available,
    otherwise from the keyword regexes."""
    if not judged:
        res.security_hits_total, res.security_hits = keyword_hits(records, "security")
        res.breaking_hits_total, res.breaking_hits = keyword_hits(records, "breaking")
        return
    infos: list[CommitInfo] = []
    for sha, date, _author, subject, _body in records:
        j = judged.get(sha)
        if j is None:
            continue
        infos.append(CommitInfo(sha=sha, date=date, subject=subject, security=j.security, impact=j.impact,
                                impact_confidence=j.impact_confidence))
        res.judged_by = res.judged_by or j.model
    sec_yes = [c for c in infos if judgments.security_bucket(c.security) == "yes"]
    sec_possible = [c for c in infos if judgments.security_bucket(c.security) == "possible"]
    res.security_hits_total = len(sec_yes)
    res.security_possible_total = len(sec_possible)
    res.security_hits = sorted(sec_yes + sec_possible, key=lambda c: -c.security)[:MAX_LISTED_HITS]
    breaking = [c for c in infos if judgments.impact_bucket(c.impact) == "breaking"]
    notable = [c for c in infos if judgments.impact_bucket(c.impact) == "notable"]
    res.impact_breaking_total = len(breaking)
    res.impact_notable_total = len(notable)
    res.impact_hits = sorted(breaking + notable, key=lambda c: -c.impact)[:MAX_LISTED_HITS]
    # the +1 "breaking" score contribution now comes from judged level-2 commits; the regex list is not shown
    res.breaking_hits_total = len(breaking)
    res.breaking_hits = []


def changelog_added_lines(repo: Path, rng: str, candidates: Iterable[str] = ("CHANGELOG.md", "CHANGELOG", "changelog.md", "HISTORY.md")) -> str:
    for name in candidates:
        exists = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{name}"], capture_output=True)
        if exists.returncode == 0:
            diff = git(repo, "diff", "--unified=0", rng, "--", name, check=False, timeout=120)
            added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
            added = [l for l in added if l.strip()]
            if added:
                return excerpt("\n".join(added[:40]), NOTES_CHARS * 2)
    return ""


def tag_message(repo: Path, tag: str) -> str:
    out = git(repo, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}", check=False)
    return excerpt(out)


# --------------------------------------------------------------------------- #
# external services
# --------------------------------------------------------------------------- #


def github_releases(repo: str, token: str | None) -> dict[str, dict[str, Any]]:
    """Return {tag_name: release} for the most recent releases, or {} on any error."""
    if not token:
        return {}
    try:
        data = http_json(
            f"https://api.github.com/repos/{repo}/releases?per_page=50",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        return {r["tag_name"]: r for r in data if isinstance(r, dict) and r.get("tag_name")}
    except Exception as exc:  # noqa: BLE001 - enrichment only
        log(f"  release notes unavailable for {repo}: {exc}")
        return {}


def pypi_latest(package: str) -> tuple[str | None, str | None]:
    try:
        data = http_json(f"https://pypi.org/pypi/{package}/json")
        version = data["info"]["version"]
        files = data.get("releases", {}).get(version, [])
        date = min((f["upload_time_iso_8601"] for f in files), default=None)
        return version, date
    except Exception as exc:  # noqa: BLE001
        log(f"  PyPI lookup failed for {package}: {exc}")
        return None, None


def registry_tags_for_digest(image: str, digest: str) -> list[str]:
    """Find tags of a container image whose manifest (index) digest matches ``digest``."""
    if image.startswith("ghcr.io/"):
        path = image[len("ghcr.io/"):]
        registry = "https://ghcr.io"
        token_url = f"https://ghcr.io/token?scope=repository:{path}:pull"
    else:
        path = image.split("/", 1)[1] if image.startswith("docker.io/") else image
        if "/" not in path:
            path = f"library/{path}"
        registry = "https://registry-1.docker.io"
        token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{path}:pull"
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])
    try:
        token = http_json(token_url)["token"]
        auth = {"Authorization": f"Bearer {token}", "Accept": accept}
        tags = http_json(f"{registry}/v2/{path}/tags/list", headers=auth).get("tags") or []
    except Exception as exc:  # noqa: BLE001
        log(f"  registry lookup failed for {image}: {exc}")
        return []
    tags = sorted(tags, key=version_key, reverse=True)[:100]
    matches = []
    for tag in tags:
        try:
            d = http_head_header(f"{registry}/v2/{path}/manifests/{tag}", "Docker-Content-Digest", headers=auth)
        except Exception:  # noqa: BLE001
            continue
        if d == digest:
            matches.append(tag)
    return matches


# --------------------------------------------------------------------------- #
# pin extraction and resolution
# --------------------------------------------------------------------------- #


def extract_pins(template_dir: Path, pins: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, str]]:
    """Return [(pin_spec, source, value)] for every pin spec that matched."""
    found = []
    for spec in pins:
        rel = spec["file"]
        path = template_dir / rel
        if not path.exists():
            found.append((spec, rel, ""))
            continue
        rx = re.compile(spec["pattern"], re.MULTILINE)
        m = rx.search(path.read_text(encoding="utf-8", errors="replace"))
        found.append((spec, rel, m.group("version") if m else ""))
    return found


def patched_files_from(template_dir: Path, glob_pattern: str | None) -> list[str]:
    if not glob_pattern:
        return []
    files: set[str] = set()
    for patch in sorted(template_dir.glob(glob_pattern)):
        for line in patch.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("+++ b/"):
                files.add(line[6:].strip())
    return sorted(files)


def tag_candidates(version: str, formats: list[str]) -> list[str]:
    bare = re.sub(r"^[vV]\.?", "", version)
    cands = []
    for fmt in formats:
        cands.append(fmt.format(version=bare, raw=version))
    for extra in (version, f"v{bare}", bare, f"v.{bare}"):
        if extra not in cands:
            cands.append(extra)
    return cands


def resolve_tag(repo: Path, version: str, formats: list[str]) -> tuple[str | None, str | None]:
    for cand in tag_candidates(version, formats):
        sha = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{cand}^{{commit}}", check=False).strip()
        if sha:
            return cand, sha
    return None, None


# --------------------------------------------------------------------------- #
# assessment
# --------------------------------------------------------------------------- #


def assess_upstream(cache: RepoCache, template: dict[str, Any], template_dir: Path, spec: dict[str, Any],
                    defaults: dict[str, Any], token: str | None, now: dt.datetime,
                    judge: judgments.Judge | None = None) -> UpstreamResult:
    repo_name = spec["repo"]
    res = UpstreamResult(name=spec["name"], repo=repo_name, template=template["name"],
                         pin_kind=spec.get("kind", "tag"), url=f"{GITHUB}/{repo_name}")
    try:
        repo = cache.upstream(repo_name)
    except Exception as exc:  # noqa: BLE001
        res.errors.append(f"clone failed: {exc}")
        return res

    res.default_branch = default_branch(repo)
    head = "HEAD"
    res.head_sha = git(repo, "rev-parse", head).strip()
    res.head_date = commit_date(repo, head)

    relevant_paths = list(defaults.get("relevant_paths", DEFAULT_RELEVANT_PATHS))
    relevant_paths += spec.get("relevant_paths", [])
    for p in spec.get("ignore_default_paths", []):
        if p in relevant_paths:
            relevant_paths.remove(p)
    tag_pattern = spec.get("tag_pattern", defaults.get("tag_pattern"))
    tag_formats = spec.get("tag_formats", ["v{version}", "{version}"])
    track_releases = spec.get("releases", True)
    lookback_days = int(spec.get("lookback_days", defaults.get("lookback_days", 90)))

    # ---- read the pin from the template repo -------------------------------
    extracted = extract_pins(template_dir, spec.get("pins", []))
    values = [(s, src, v) for s, src, v in extracted if v]
    for s, src, v in extracted:
        if not v:
            res.errors.append(f"pin pattern did not match in {src}")
    resolved: list[tuple[str, str, str | None]] = []  # (source, sha, tag)
    for s, src, v in values:
        kind = s.get("kind", res.pin_kind)
        sha = tag = None
        if kind == "commit":
            sha = git(repo, "rev-parse", "--verify", "--quiet", f"{v}^{{commit}}", check=False).strip() or None
            if not sha:
                res.errors.append(f"commit {v} from {src} not found upstream")
        elif kind == "tag":
            tag, sha = resolve_tag(repo, v, tag_formats)
            if not sha:
                res.errors.append(f"no upstream tag for {v} (from {src}); tried {', '.join(tag_candidates(v, tag_formats)[:4])}")
        elif kind == "image-digest":
            image = s["image"]
            tags = registry_tags_for_digest(image, v)
            release_tags = [t for t in tags if not re.match(tag_pattern, t) is None] if tag_pattern else tags
            chosen = (release_tags or tags)
            if chosen:
                tag, sha = resolve_tag(repo, chosen[0], tag_formats)
                res.pin_raw = f"{v[:19]}… = {image}:{chosen[0]}"
                if not sha:
                    res.errors.append(f"image tag {chosen[0]} has no matching upstream git tag")
            else:
                res.errors.append(f"digest {v[:19]}… matches no tag of {image}")
        else:
            res.errors.append(f"unknown pin kind {kind}")
        if res.pin_raw is None:
            res.pin_raw = v
        res.pin_source = res.pin_source or src
        if sha:
            resolved.append((src, sha, tag))

    if resolved:
        shas = {sha for _, sha, _ in resolved}
        if len(shas) > 1:
            res.pin_mismatch = [f"{src}: {short(sha)}{' (' + tag + ')' if tag else ''}" for src, sha, tag in resolved]
        res.pin_source, res.pin_sha, res.pin_tag = resolved[0]
        res.pin_date = commit_date(repo, res.pin_sha)
        res.pin_age_days = days_between(parse_iso(res.pin_date), now)

    # ---- PyPI ---------------------------------------------------------------
    if spec.get("pypi"):
        res.pypi_latest, res.pypi_latest_date = pypi_latest(spec["pypi"])

    # ---- releases -----------------------------------------------------------
    tags = list_tags(repo, tag_pattern) if track_releases else []
    tags.sort(key=lambda t: version_key(t.tag))
    # A tag name alone cannot decide this: upstreams ship pre-releases under
    # stable-looking tags (CaviraOSS/LongMemory tags "Beta v1.3.0" as v1.3.0).
    # The release object carries an explicit flag, so read it BEFORE the
    # decisions below - `latest` and the `newer` filter both depend on it.
    releases_api = github_releases(repo_name, token) if tags else {}
    for t in tags:
        api = releases_api.get(t.tag)
        if api is not None:
            t.prerelease = bool(api.get("prerelease")) or t.prerelease
    pinned_is_pre = bool(res.pin_tag and is_prerelease(res.pin_tag)) or \
        bool(res.pin_tag and releases_api.get(res.pin_tag, {}).get("prerelease"))
    if tags:
        stable = [t for t in tags if not t.prerelease] or tags
        latest = stable[-1]
        res.latest_release, res.latest_release_date = latest.tag, latest.date
    newer: list[ReleaseInfo] = []
    if tags and res.pin_sha:
        pin_key = version_key(res.pin_tag) if res.pin_tag else None
        pin_dt = parse_iso(res.pin_date) if res.pin_date else None
        for t in tags:
            if t.prerelease and not pinned_is_pre:
                continue
            if is_ancestor(repo, t.sha, res.pin_sha):
                continue
            if pin_key is not None and version_key(t.tag) <= pin_key:
                continue
            if pin_key is None and pin_dt is not None and t.date and parse_iso(t.date) <= pin_dt:
                continue
            newer.append(t)
    res.newer_releases_total = len(newer)
    for t in newer[-MAX_LISTED_RELEASES:][::-1]:
        api = releases_api.get(t.tag)
        if api:
            t.notes = excerpt(api.get("body") or "")
            t.url = api.get("html_url") or f"{res.url}/releases/tag/{t.tag}"
            t.prerelease = bool(api.get("prerelease")) or t.prerelease
        else:
            t.notes = tag_message(repo, t.tag)
            t.url = f"{res.url}/releases/tag/{t.tag}"
        res.newer_releases.append(t)

    # ---- commits ------------------------------------------------------------
    if res.pin_sha:
        rng = f"{res.pin_sha}..{head}"
        res.commits_ahead = rev_count(repo, rng)
        res.commits_behind = rev_count(repo, f"{head}..{res.pin_sha}")
    else:
        # no resolvable pin: look at recent activity instead
        res.activity_window_days = lookback_days
        out = git(repo, "rev-list", "--count", f"--since={lookback_days}.days", head, check=False).strip()
        res.commits_ahead = int(out) if out.isdigit() else 0

    def log_range(paths: list[str] | None = None, limit: int | None = None) -> list[CommitInfo]:
        if res.pin_sha:
            return log_commits(repo, f"{res.pin_sha}..{head}", paths, limit)
        cmd = ["log", "--no-merges", "--date=short", "--format=%H%x09%ad%x09%s", f"--since={lookback_days}.days", head]
        if limit:
            cmd.insert(1, f"--max-count={limit}")
        if paths:
            cmd += ["--", *pathspecs(paths)]
        out = git(repo, *cmd, check=False)
        return [CommitInfo(*((l.split("\t", 2) + ["", ""])[:3])) for l in out.splitlines()]

    def count_range(paths: list[str]) -> int:
        if res.pin_sha:
            return count_commits(repo, f"{res.pin_sha}..{head}", paths)
        out = git(repo, "rev-list", "--count", "--no-merges", f"--since={lookback_days}.days", head, "--", *pathspecs(paths), check=False).strip()
        return int(out) if out.isdigit() else 0

    if res.commits_ahead:
        res.relevant_commits_total = count_range(relevant_paths)
        res.relevant_commits = log_range(relevant_paths, MAX_LISTED_COMMITS)
        for c in res.relevant_commits:
            c.files = files_of(repo, c.sha, relevant_paths)

        res.patched_files = patched_files_from(template_dir, spec.get("patched_files_glob"))
        if res.patched_files:
            res.patched_file_commits_total = count_commits(repo, f"{res.pin_sha}..{head}", res.patched_files) if res.pin_sha else count_range(res.patched_files)
            res.patched_file_commits = log_range(res.patched_files, MAX_LISTED_COMMITS)
            for c in res.patched_file_commits:
                c.files = files_of(repo, c.sha, res.patched_files, excludes=[])

        if res.pin_sha:
            records = scan_commits(repo, [f"{res.pin_sha}..{head}"])
            res.changelog_excerpt = changelog_added_lines(repo, f"{res.pin_sha}..{head}")
        else:
            # without a pin only the watched paths are meaningful, not the whole repository
            records = scan_commits(repo, [f"--since={lookback_days}.days", head], relevant_paths)
        judged = None
        if judge is not None and records:
            try:
                rng_args = [f"{res.pin_sha}..{head}"] if res.pin_sha else [f"--since={lookback_days}.days", head]
                judged = judge.judge_commits(records, commit_files(repo, rng_args, None if res.pin_sha else relevant_paths))
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"commit judgments unavailable, keyword heuristics used instead: {exc}")
        apply_judgments(res, records, judged)

    score_and_verdict(res, track_releases)
    return res


def security_reason(res: UpstreamResult, since: str) -> str:
    if res.judged_by:
        extra = f" ({res.security_possible_total} more possible)" if res.security_possible_total else ""
        return f"{res.security_hits_total} commit(s) {since} judged to be security fixes (Jev ≥ {judgments.SECURITY_YES}){extra}"
    return f"{res.security_hits_total} commit(s) {since} mention security-related terms"


def breaking_reason(res: UpstreamResult) -> str:
    if res.judged_by:
        return f"{res.breaking_hits_total} commit(s) judged to break existing deployments or need a manual step"
    return f"{res.breaking_hits_total} commit(s) mention breaking changes, deprecations or migrations"


def score_and_verdict(res: UpstreamResult, track_releases: bool) -> None:
    reasons: list[str] = []
    score = 0

    if not res.pin_sha:
        if res.errors:
            reasons.append("pin could not be resolved against upstream; check the manifest pattern or the upstream tag")
        if res.activity_window_days:
            reasons.append(f"no pin: showing activity of the last {res.activity_window_days} days instead")
            if res.relevant_commits_total:
                score += min(res.relevant_commits_total, 5)
                reasons.append(f"{res.relevant_commits_total} commit(s) touched template-relevant paths")
            if res.security_hits_total:
                score += 4
                reasons.append(security_reason(res, "in the window"))
        res.score = score
        res.verdict = "unknown" if not res.activity_window_days else ("review" if score >= 3 else "minor-drift" if score else "up-to-date")
        res.reasons = reasons
        return

    if res.pin_mismatch:
        reasons.append("the template pins disagree with each other: " + "; ".join(res.pin_mismatch))
        score += 2

    if res.commits_behind:
        reasons.append(f"pinned commit is not on the default branch ({res.commits_behind} commit(s) not in {res.default_branch})")

    n_rel = res.newer_releases_total
    if n_rel:
        stable = [r for r in res.newer_releases if not r.prerelease]
        pts = min(2 * n_rel, 6)
        score += pts
        newest = res.latest_release or res.newer_releases[0].tag
        reasons.append(f"{n_rel} newer release(s); newest {newest}" + (f" ({res.latest_release_date[:10]})" if res.latest_release_date else ""))
        if any(not r.prerelease for r in res.newer_releases) and version_key(newest)[:1] != version_key(res.pin_tag or "")[:1]:
            reasons.append("major version component changed; expect migration work")
            score += 1
    if res.pypi_latest and res.pin_raw and version_key(res.pypi_latest) > version_key(re.sub(r"^[vV]", "", res.pin_raw.split()[0])):
        reasons.append(f"PyPI publishes {res.pypi_latest}" + (f" ({res.pypi_latest_date[:10]})" if res.pypi_latest_date else ""))
        if not n_rel:
            score += 2

    if res.security_hits_total:
        score += 4
        reasons.append(security_reason(res, "since the pin"))

    if res.patched_file_commits_total:
        score += 3
        reasons.append(f"{res.patched_file_commits_total} commit(s) touched files the template patches; the patch may need rebasing")

    if res.relevant_commits_total:
        score += min(res.relevant_commits_total, 5)
        reasons.append(f"{res.relevant_commits_total} commit(s) touched template-relevant paths (Dockerfiles, compose, env, config, migrations, …)")

    if res.breaking_hits_total:
        score += 1
        reasons.append(breaking_reason(res))

    if res.commits_ahead:
        score += min(res.commits_ahead // 25, 3)
        reasons.append(f"default branch is {res.commits_ahead} commit(s) ahead of the pin")

    if res.pin_age_days is not None and res.pin_age_days > 90 and (res.commits_ahead or n_rel):
        score += 2
        reasons.append(f"pinned commit is {res.pin_age_days} days old")

    if not res.commits_ahead and not n_rel:
        verdict = "up-to-date"
        reasons.append("pin matches the upstream default branch head")
    elif track_releases and not n_rel and res.latest_release and res.pin_tag and \
            version_key(res.latest_release) <= version_key(res.pin_tag):
        # pinned at the newest release; only unreleased work on the default branch
        verdict = "review" if (res.security_hits_total or res.patched_file_commits_total) else "minor-drift"
        reasons.insert(0, "pinned at the newest release; the commits below are unreleased work on the default branch")
    elif score >= 8:
        verdict = "update-recommended"
    elif score >= 3:
        verdict = "review"
    elif score >= 1:
        verdict = "minor-drift"
    else:
        verdict = "up-to-date"

    if res.security_hits_total and n_rel and verdict != "update-recommended":
        verdict = "update-recommended"
        reasons.insert(0, "security-related commits are already part of a newer release")

    res.score = score
    res.verdict = verdict
    res.reasons = reasons


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def fmt_commit(res: UpstreamResult, c: CommitInfo, with_files: bool = True, tag: str | None = None) -> str:
    line = f"- [`{c.sha[:8]}`]({res.url}/commit/{c.sha}) {c.date} {md_escape(c.subject)[:110]}"
    if tag:
        line += f" *({tag})*"
    if with_files and c.files:
        line += "  \n  " + ", ".join(f"`{f}`" for f in c.files)
    return line


def render_upstream(res: UpstreamResult) -> str:
    out = []
    pin_desc = "not resolved"
    if res.pin_sha:
        pin_desc = f"`{res.pin_raw}` → "
        if res.pin_tag:
            pin_desc += f"[{res.pin_tag}]({res.url}/releases/tag/{res.pin_tag}) "
        pin_desc += f"[`{short(res.pin_sha)}`]({res.url}/commit/{res.pin_sha})"
        if res.pin_date:
            pin_desc += f" from {res.pin_date[:10]}"
        if res.pin_age_days is not None:
            pin_desc += f" ({res.pin_age_days} days old)"
    elif res.pin_raw:
        pin_desc = f"`{res.pin_raw}` (could not be resolved)"
    out.append(f"#### [{res.repo}]({res.url}) — {res.name}\n")
    out.append(f"**Verdict:** {VERDICT_LABEL[res.verdict]} (score {res.score})  ")
    out.append(f"**Pin:** {pin_desc}" + (f", read from `{res.pin_source}`" if res.pin_source else "") + "  ")
    if res.head_sha:
        out.append(f"**Upstream `{res.default_branch}`:** [`{short(res.head_sha)}`]({res.url}/commit/{res.head_sha}) from {res.head_date[:10]}"
                   + (f", {res.commits_ahead} commit(s) ahead of the pin" if res.pin_sha else "") + "  ")
    if res.latest_release:
        out.append(f"**Newest release:** [{res.latest_release}]({res.url}/releases/tag/{res.latest_release}) ({res.latest_release_date[:10]})"
                   + (f"; PyPI {res.pypi_latest}" if res.pypi_latest else "") + "  ")
    elif res.pypi_latest:
        out.append(f"**PyPI latest:** {res.pypi_latest}  ")
    out.append("")
    if res.reasons:
        out.append("Why:")
        out.extend(f"- {r}" for r in res.reasons)
        out.append("")
    if res.errors:
        out.append("Problems:")
        out.extend(f"- ⚠️ {e}" for e in res.errors)
        out.append("")
    if res.newer_releases:
        out.append(f"Newer releases ({res.newer_releases_total}" + (f", showing {len(res.newer_releases)}" if res.newer_releases_total > len(res.newer_releases) else "") + "):")
        for r in res.newer_releases:
            line = f"- [{r.tag}]({r.url}) {r.date[:10]}" + (" (pre-release)" if r.prerelease else "")
            if r.notes:
                line += "\n" + textwrap.indent(r.notes, "  > ", lambda _: True)
            out.append(line)
        out.append("")
    if res.security_hits and res.judged_by:
        hdr = f"Security fixes ({res.security_hits_total} judged ≥ {judgments.SECURITY_YES}"
        if res.security_possible_total:
            hdr += f", {res.security_possible_total} more possible"
        out.append(hdr + "):")
        out.extend(fmt_commit(res, c, with_files=False, tag=f"security {c.security:.2f}") for c in res.security_hits)
        out.append("")
    elif res.security_hits:
        out.append(f"Security-related commit messages ({res.security_hits_total}):")
        out.extend(fmt_commit(res, c, with_files=False) for c in res.security_hits)
        out.append("")
    if res.impact_hits:
        shown = f", showing {len(res.impact_hits)}" if res.impact_breaking_total + res.impact_notable_total > len(res.impact_hits) else ""
        out.append(f"Deployer impact ({res.impact_breaking_total} judged to break existing deployments, "
                   f"{res.impact_notable_total} worth knowing{shown}):")
        out.extend(fmt_commit(res, c, with_files=False, tag=f"impact {c.impact:.2f}, confidence {c.impact_confidence:.2f}")
                   for c in res.impact_hits)
        out.append("")
    if res.patched_file_commits:
        out.append(f"Commits touching files the template patches ({res.patched_file_commits_total}) — `{'`, `'.join(res.patched_files)}`:")
        out.extend(fmt_commit(res, c) for c in res.patched_file_commits)
        out.append("")
    if res.relevant_commits:
        out.append(f"Commits touching template-relevant paths ({res.relevant_commits_total}" + (f", showing {len(res.relevant_commits)}" if res.relevant_commits_total > len(res.relevant_commits) else "") + "):")
        out.extend(fmt_commit(res, c) for c in res.relevant_commits)
        out.append("")
    if res.breaking_hits:
        out.append(f"Breaking-change / migration mentions ({res.breaking_hits_total}):")
        out.extend(fmt_commit(res, c, with_files=False) for c in res.breaking_hits)
        out.append("")
    if res.changelog_excerpt and not any(r.notes for r in res.newer_releases):
        out.append("CHANGELOG lines added since the pin:")
        out.append(textwrap.indent(res.changelog_excerpt, "> ", lambda _: True))
        out.append("")
    return "\n".join(out)


def template_verdict(results: list[UpstreamResult]) -> str:
    return min((r.verdict for r in results), key=lambda v: VERDICT_ORDER[v]) if results else "unknown"


def render_report(date: str, manifest: dict[str, Any], results: dict[str, list[UpstreamResult]],
                  previous: dict[str, Any] | None, template_meta: dict[str, dict[str, Any]]) -> str:
    templates = manifest["templates"]
    out = [f"# Template update assessment — {date}\n"]
    judged_models = sorted({r.judged_by for rs in results.values() for r in rs if r.judged_by})
    out.append(f"Generated by `scripts/assess_template_updates.py` from [`templates.yaml`](../templates.yaml). "
               f"{len(templates)} templates, {sum(len(v) for v in results.values())} upstream projects. "
               "Verdicts compare the version each template pins with the upstream default branch and releases; "
               "see [scripts/README.md](../scripts/README.md) for the scoring rules."
               + (f" Security and deployer-impact judgments by TypeSafe `{'`, `'.join(judged_models)}`." if judged_models
                  else " Security and breaking-change signals come from keyword heuristics (no `TYPESAFE_API_KEY`).") + "\n")

    counts: dict[str, int] = {}
    for t in templates:
        v = template_verdict(results[t["name"]])
        counts[v] = counts.get(v, 0) + 1
    out.append("| Verdict | Templates |\n|---|---|")
    for v in VERDICT_ORDER:
        if counts.get(v):
            out.append(f"| {VERDICT_LABEL[v]} | {counts[v]} |")
    out.append("")

    out.append("## Summary\n")
    out.append("| Template | Upstream | Pinned | Newest release | Newer releases | Commits ahead | Relevant | Security | Verdict |")
    out.append("|---|---|---|---|---:|---:|---:|---:|---|")
    ordered = sorted(templates, key=lambda t: (VERDICT_ORDER[template_verdict(results[t["name"]])], t["name"].lower()))
    for t in ordered:
        for i, r in enumerate(results[t["name"]]):
            tname = f"[{md_escape(t['name'])}]({GITHUB}/{t['template_repo']})" if i == 0 else ""
            pinned = (r.pin_tag or (short(r.pin_sha) if r.pin_sha else (r.pin_raw or "—")))
            if r.pin_raw and r.pin_tag is None and r.pin_sha and len(r.pin_raw) > 12:
                pinned = short(r.pin_sha)
            latest = r.latest_release or (r.pypi_latest and f"PyPI {r.pypi_latest}") or "—"
            if not r.pin_sha and r.pin_raw:
                pinned = f"⚠️ {pinned}"
            ahead = str(r.commits_ahead) if r.commits_ahead else "—"
            if r.activity_window_days and r.commits_ahead:
                ahead = f"{r.commits_ahead} in {r.activity_window_days}d"
            out.append(f"| {tname} | [{r.repo}]({r.url}) ({md_escape(r.name)}) | `{md_escape(pinned)}` | {latest} | "
                       f"{r.newer_releases_total or '—'} | {ahead} | {r.relevant_commits_total or '—'} | "
                       f"{r.security_hits_total or '—'} | {VERDICT_LABEL[r.verdict]} |")
    out.append("")

    # ---- delta vs previous report -----------------------------------------
    if previous:
        prev_map = {(u["template"], u["repo"], u["name"]): u for t in previous.get("templates", []) for u in t.get("upstreams", [])}
        changes = []
        for t in templates:
            for r in results[t["name"]]:
                p = prev_map.get((r.template, r.repo, r.name))
                if not p:
                    changes.append(f"- **{md_escape(t['name'])}** / {r.repo}: newly tracked")
                    continue
                bits = []
                if p.get("verdict") != r.verdict:
                    bits.append(f"verdict {VERDICT_LABEL.get(p.get('verdict'), p.get('verdict'))} → {VERDICT_LABEL[r.verdict]}")
                if p.get("pin_sha") != r.pin_sha:
                    bits.append(f"template pin moved {p.get('pin_tag') or short(p.get('pin_sha'))} → {r.pin_tag or short(r.pin_sha)}")
                if p.get("latest_release") != r.latest_release and r.latest_release:
                    bits.append(f"new upstream release {r.latest_release}")
                elif (r.commits_ahead or 0) > (p.get("commits_ahead") or 0) and r.pin_sha == p.get("pin_sha"):
                    bits.append(f"+{r.commits_ahead - (p.get('commits_ahead') or 0)} upstream commit(s)")
                # counts are only comparable when both reports used the same method (regex vs Jev model)
                if (r.security_hits_total or 0) > (p.get("security_hits_total") or 0) and p.get("judged_by") == r.judged_by:
                    bits.append("new security-related commit(s)")
                if bits:
                    changes.append(f"- **{md_escape(t['name'])}** / {r.repo}: " + "; ".join(bits))
        out.append(f"## Changes since the previous report ({previous.get('date', '?')})\n")
        out.extend(changes or ["- Nothing changed."])
        out.append("")

    out.append("## Details\n")
    for t in ordered:
        rs = results[t["name"]]
        meta = template_meta.get(t["name"], {})
        out.append(f"### {t['name']} — {VERDICT_LABEL[template_verdict(rs)]}\n")
        line = f"Template repo [{t['template_repo']}]({GITHUB}/{t['template_repo']})"
        if meta.get("last_commit_date"):
            line += f" (last commit {meta['last_commit_date'][:10]})"
        if t.get("deploy_url"):
            line += f" · [Railway listing]({t['deploy_url']})"
        out.append(line + "\n")
        if t.get("notes"):
            out.append(f"> {t['notes']}\n")
        for r in rs:
            out.append(render_upstream(r))
    return "\n".join(out).rstrip() + "\n"


def render_index(report_dir: Path) -> str:
    dated = sorted((p for p in report_dir.glob("*.md") if re.match(r"\d{4}-\d{2}-\d{2}\.md$", p.name)), reverse=True)
    out = ["# Template update assessment reports\n",
           "Weekly reports written by `scripts/assess_template_updates.py` (GitHub Actions, Fridays). "
           "[latest.md](latest.md) always mirrors the newest report. Each run diffs against the newest **dated** "
           "report older than itself, so the dated files are the state that matters.\n",
           "| Date | Update recommended | Review | Minor drift | Up to date | Unknown |", "|---|---:|---:|---:|---:|---:|"]
    for p in dated:
        js = report_dir / (p.stem + ".json")
        counts = {}
        if js.exists():
            try:
                data = json.loads(js.read_text())
                for t in data.get("templates", []):
                    counts[t["verdict"]] = counts.get(t["verdict"], 0) + 1
            except Exception:  # noqa: BLE001
                pass
        out.append(f"| [{p.stem}]({p.name}) | " + " | ".join(str(counts.get(v, 0)) for v in VERDICT_ORDER) + " |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="templates.yaml")
    ap.add_argument("--report-dir", default="reports")
    ap.add_argument("--cache-dir", default=".cache", help="where upstream and template clones are kept between runs")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="report date (YYYY-MM-DD)")
    ap.add_argument("--only", action="append", default=[], help="assess only templates whose name contains this (repeatable)")
    ap.add_argument("--no-refresh", action="store_true", help="do not fetch upstreams that are already cached")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"), help="also append the summary table to this file")
    ap.add_argument("--no-judgments", action="store_true",
                    help="use the keyword heuristics even when TYPESAFE_API_KEY is set (same as TEMPLATES_JUDGMENTS=off)")
    args = ap.parse_args()

    manifest = yaml.safe_load(Path(args.manifest).read_text(encoding="utf-8"))
    defaults = manifest.get("defaults", {})
    EXCLUDE_PATHS[:] = list(defaults.get("exclude_paths", []))
    templates = manifest["templates"]
    if args.only:
        templates = [t for t in templates if any(o.lower() in t["name"].lower() for o in args.only)]
    manifest = {**manifest, "templates": templates}

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    now = dt.datetime.now(dt.timezone.utc)
    cache = RepoCache(Path(args.cache_dir), refresh=not args.no_refresh)
    judge: judgments.Judge | None = None
    if judgments.enabled() and not args.no_judgments:
        judge = judgments.Judge(os.environ["TYPESAFE_API_KEY"], Path(args.cache_dir) / "judgments.json")
        log(f"commit judgments: TypeSafe {judge.model} (cache {args.cache_dir}/judgments.json)")
    else:
        log("commit judgments: off, keyword heuristics in use (export TYPESAFE_API_KEY to enable)")
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    # the newest dated report older than this run (a re-run on the same day must not diff against itself)
    previous = None
    older = sorted(p for p in report_dir.glob("*.json") if re.match(r"\d{4}-\d{2}-\d{2}\.json$", p.name) and p.stem < args.date)
    if older:
        try:
            previous = json.loads(older[-1].read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log(f"could not read previous report {older[-1]}: {exc}")

    results: dict[str, list[UpstreamResult]] = {}
    template_meta: dict[str, dict[str, Any]] = {}
    for t in templates:
        log(f"== {t['name']}")
        try:
            tdir = cache.template(t["template_repo"])
            template_meta[t["name"]] = {
                "last_commit_date": git(tdir, "show", "-s", "--format=%cI", "HEAD").strip(),
                "head_sha": git(tdir, "rev-parse", "HEAD").strip(),
            }
        except Exception as exc:  # noqa: BLE001
            log(f"  template clone failed: {exc}")
            results[t["name"]] = [UpstreamResult(name=u["name"], repo=u["repo"], template=t["name"], pin_kind=u.get("kind", "tag"),
                                                 errors=[f"template clone failed: {exc}"], url=f"{GITHUB}/{u['repo']}")
                                  for u in t["upstreams"]]
            continue
        results[t["name"]] = []
        for u in t["upstreams"]:
            try:
                res = assess_upstream(cache, t, tdir, u, defaults, token, now, judge)
            except Exception as exc:  # noqa: BLE001
                log(f"  assessment failed for {u['repo']}: {exc}")
                res = UpstreamResult(name=u["name"], repo=u["repo"], template=t["name"], pin_kind=u.get("kind", "tag"),
                                     errors=[f"assessment failed: {exc}"], url=f"{GITHUB}/{u['repo']}")
            results[t["name"]].append(res)
            log(f"  {u['repo']}: {res.verdict} (score {res.score})")

    if judge is not None:
        u = judge.usage
        log(f"TypeSafe usage: {u['requests']} request(s), {u['input_tokens']} input tokens (~${u['input_tokens'] * 0.042 / 1e6:.3f})")
    report = render_report(args.date, manifest, results, previous, template_meta)
    data = {
        "date": args.date,
        "generated_at": now.isoformat(timespec="seconds"),
        "templates": [
            {
                "name": t["name"],
                "template_repo": t["template_repo"],
                "deploy_url": t.get("deploy_url"),
                "template_head": template_meta.get(t["name"], {}).get("head_sha"),
                "verdict": template_verdict(results[t["name"]]),
                "upstreams": [asdict(r) for r in results[t["name"]]],
            }
            for t in templates
        ],
    }

    (report_dir / f"{args.date}.md").write_text(report, encoding="utf-8")
    (report_dir / f"{args.date}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (report_dir / "latest.md").write_text(report, encoding="utf-8")
    (report_dir / "README.md").write_text(render_index(report_dir), encoding="utf-8")

    if args.summary:
        summary = report.split("## Details")[0]
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")

    log(f"wrote {report_dir / (args.date + '.md')} and {report_dir / 'latest.md'}")
    print(report.split("## Changes since")[0].split("## Details")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
