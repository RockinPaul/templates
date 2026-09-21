"""Commit judgments from TypeSafe's System One model (Jev).

Two questions are asked about every commit since a template's pin, eight
commits per request:

* ``sec_i`` (Noul): does the commit fix or harden against a security weakness?
* ``ops_i`` (Score, three levels): what does the change mean for someone who
  runs the software from a container with their own configuration?

Only the standard library is used. Judgments are cached by commit sha (per
model) so a weekly run only pays for new commits. Enable by exporting
``TYPESAFE_API_KEY``; force off with ``TEMPLATES_JUDGMENTS=off`` or the
script's ``--no-judgments`` flag.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # pinned: thresholds below were measured against this version
BATCH = 8
BODY_CHARS = 400
MAX_FILES = 8

SECURITY_YES = 0.8       # counted as a security fix
SECURITY_POSSIBLE = 0.5  # listed as "possible", not counted
IMPACT_BREAKING = 1.5    # level 2: existing deployments break / manual step
IMPACT_NOTABLE = 0.9     # level 1: worth knowing, deployments keep working

SECURITY_INSTRUCTIONS = (
    "Does commit `commits[{i}]` fix, mitigate, or harden the shipped software against a security weakness? "
    "This includes fixing an injection, an authorization or authentication bypass, path traversal, SSRF, denial of service, "
    "secret or credential exposure, or an unsafe default, and upgrading a dependency because of a known vulnerability or advisory "
    "(CVE, GHSA, audit finding)."
)
SECURITY_CRITERIA = {
    "true": "The code change itself closes or reduces an exploitable weakness in the software users run, or bumps a dependency "
            "specifically to pick up a vulnerability fix.",
    "false": "The commit only mentions security-flavoured words without changing the software's security: documentation, website or "
             "marketing copy, CI or test changes, refactors, new features, routine dependency bumps that name no advisory, or words such as "
             "traversal, sanitize, token, secret, guard, bound used in a non-security sense (tree traversal, string normalisation, "
             "feature work on an auth UI).",
}
IMPACT_INSTRUCTIONS = (
    "Consider someone who runs this software from a container image with their own configuration (environment variables, mounted "
    "volumes, ports, a reverse proxy) and does not modify its source code. How does commit `commits[{i}]` affect them when they "
    "upgrade to a build that includes it?"
)
IMPACT_LEVELS = [
    "No effect on how the software is deployed or configured: internal code, tests, documentation, CI, refactors, or features that "
    "work unchanged with the existing configuration.",
    "Something a deployer may want to know about, but existing deployments keep working unchanged: a new optional setting or "
    "environment variable, a changed default that can still be overridden, a new dependency or base image, a Dockerfile or compose "
    "file change, or a database migration that runs automatically on start.",
    "Existing deployments break or need a manual step after upgrading: a required new setting; a removed or renamed environment "
    "variable, config file, path, or port without a fallback; a data or schema migration that must be run by hand; a changed storage "
    "layout or volume path; or dropped support for a platform, runtime, or database version.",
]

Record = tuple[str, str, str, str, str]  # (sha, date, author, subject, body), as scan_commits yields


@dataclass
class Judgment:
    security: float
    impact: float
    impact_confidence: float
    model: str


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("TYPESAFE_API_KEY")) and env.get("TEMPLATES_JUDGMENTS", "on").lower() != "off"


def security_bucket(p: float) -> str:
    return "yes" if p >= SECURITY_YES else "possible" if p >= SECURITY_POSSIBLE else "no"


def impact_bucket(score: float) -> str:
    return "breaking" if score >= IMPACT_BREAKING else "notable" if score >= IMPACT_NOTABLE else "none"


def build_questions(n: int) -> dict[str, dict[str, Any]]:
    q: dict[str, dict[str, Any]] = {}
    for i in range(n):
        q[f"sec_{i}"] = {"type": "noul", "instructions": SECURITY_INSTRUCTIONS.format(i=i), "criteria": SECURITY_CRITERIA}
        q[f"ops_{i}"] = {"type": "score", "instructions": IMPACT_INSTRUCTIONS.format(i=i), "criteria": IMPACT_LEVELS}
    return q


def commit_state(records: Iterable[Record], files: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    files = files or {}
    return {"commits": [
        {"author": author, "subject": subject, "body": (body or "").strip()[:BODY_CHARS], "files": list(files.get(sha, []))[:MAX_FILES]}
        for sha, _date, author, subject, body in records
    ]}


class Judge:
    """Asks Jev about commits, in batches, with a sha-keyed JSON cache."""

    def __init__(self, api_key: str, cache_path: Path | None, model: str = MODEL,
                 ask: Callable[[dict, dict], dict] | None = None, workers: int = 8):
        self.api_key = api_key
        self.cache_path = cache_path
        self.model = model
        self._ask = ask or self._http_ask
        self.workers = workers
        self.usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
        self._cache: dict[str, dict[str, dict[str, float]]] = {}
        if cache_path and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}

    # ---- transport ---------------------------------------------------------
    def _http_ask(self, state: dict, questions: dict, retries: int = 6) -> dict:
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        req = urllib.request.Request(URL, data=body, headers={"Authorization": f"Bearer {self.api_key}",
                                                              "Content-Type": "application/json"})
        delay = 1.0
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.load(r)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 529) and attempt < retries - 1:
                    retry_after = exc.headers.get("retry-after")
                    time.sleep(float(retry_after) if retry_after else delay)
                    delay = min(delay * 2, 20)
                    continue
                raise RuntimeError(f"TypeSafe HTTP {exc.code}: {exc.read()[:300]!r}") from exc
            except (urllib.error.URLError, TimeoutError):
                if attempt < retries - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, 20)
                    continue
                raise
        raise RuntimeError("unreachable")

    # ---- judgments ---------------------------------------------------------
    def judge_commits(self, records: list[Record], files: Mapping[str, list[str]] | None = None) -> dict[str, Judgment]:
        known = self._cache.setdefault(self.model, {})
        out: dict[str, Judgment] = {}
        todo: list[Record] = []
        seen: set[str] = set()
        for rec in records:
            sha = rec[0]
            if sha in seen:
                continue
            seen.add(sha)
            if sha in known:
                k = known[sha]
                out[sha] = Judgment(k["security"], k["impact"], k["impact_confidence"], self.model)
            else:
                todo.append(rec)
        if not todo:
            return out

        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

        def run(batch: list[Record]) -> tuple[list[Record], dict]:
            return batch, self._ask(commit_state(batch, files), build_questions(len(batch)))

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            for batch, resp in pool.map(run, batches):
                usage = resp.get("usage") or {}
                self.usage["input_tokens"] += int(usage.get("input_tokens", 0))
                self.usage["output_tokens"] += int(usage.get("output_tokens", 0))
                self.usage["requests"] += 1
                model = resp.get("model") or self.model
                answers = resp["answers"]
                for i, rec in enumerate(batch):
                    sec = float(answers[f"sec_{i}"]["noul"])
                    ops = answers[f"ops_{i}"]
                    j = Judgment(sec, float(ops["score"]), float(ops.get("confidence", 0.0)), model)
                    out[rec[0]] = j
                    known[rec[0]] = {"security": j.security, "impact": j.impact, "impact_confidence": j.impact_confidence}
        self._save()
        return out

    def _save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache), encoding="utf-8")
        tmp.replace(self.cache_path)
