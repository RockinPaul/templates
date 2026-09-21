"""Tests for the TypeSafe commit judgments and their use in the assessment.

Run from the repository root:  python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import judgments  # noqa: E402
import assess_template_updates as atu  # noqa: E402


def record(i: int, subject: str = "fix: something", body: str = "", author: str = "dev") -> tuple[str, str, str, str, str]:
    return (f"{i:040x}", "2026-09-01", author, subject, body)


def fake_answers(state: dict, questions: dict, security: float = 0.1, impact: float = 0.0) -> dict:
    answers = {}
    for key, q in questions.items():
        if q["type"] == "noul":
            answers[key] = {"type": "noul", "noul": security}
        else:
            answers[key] = {"type": "score", "score": impact, "confidence": 0.9,
                            "legend": {"0": "a", "1": "b", "2": "c"}, "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0}}
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 1}}


class EnabledTest(unittest.TestCase):
    def test_enabled_needs_key_and_not_switched_off(self):
        self.assertFalse(judgments.enabled({}))
        self.assertTrue(judgments.enabled({"TYPESAFE_API_KEY": "k"}))
        self.assertFalse(judgments.enabled({"TYPESAFE_API_KEY": "k", "TEMPLATES_JUDGMENTS": "off"}))


class QuestionTest(unittest.TestCase):
    def test_two_questions_per_commit_referencing_its_index(self):
        q = judgments.build_questions(3)
        self.assertEqual(len(q), 6)
        self.assertEqual(q["sec_2"]["type"], "noul")
        self.assertEqual(q["ops_2"]["type"], "score")
        self.assertIn("`commits[2]`", q["sec_2"]["instructions"])
        self.assertIn("`commits[2]`", q["ops_2"]["instructions"])
        self.assertEqual(len(q["ops_2"]["criteria"]), 3)

    def test_state_trims_body_and_files(self):
        recs = [record(1, body="x" * 2000)]
        state = judgments.commit_state(recs, {recs[0][0]: [f"f{i}" for i in range(20)]})
        self.assertEqual(len(state["commits"]), 1)
        self.assertLessEqual(len(state["commits"][0]["body"]), 400)
        self.assertEqual(len(state["commits"][0]["files"]), 8)
        self.assertEqual(state["commits"][0]["subject"], "fix: something")


class BucketTest(unittest.TestCase):
    def test_security_buckets(self):
        self.assertEqual(judgments.security_bucket(0.95), "yes")
        self.assertEqual(judgments.security_bucket(0.8), "yes")
        self.assertEqual(judgments.security_bucket(0.6), "possible")
        self.assertEqual(judgments.security_bucket(0.2), "no")

    def test_impact_buckets(self):
        self.assertEqual(judgments.impact_bucket(1.7), "breaking")
        self.assertEqual(judgments.impact_bucket(1.5), "breaking")
        self.assertEqual(judgments.impact_bucket(1.0), "notable")
        self.assertEqual(judgments.impact_bucket(0.3), "none")


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "judgments.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_batches_of_eight_keyed_by_sha(self):
        calls = []

        def ask(state, questions):
            calls.append(len(state["commits"]))
            return fake_answers(state, questions, security=0.9, impact=1.6)

        judge = judgments.Judge(api_key="k", cache_path=self.cache, ask=ask, workers=1)
        recs = [record(i) for i in range(20)]
        out = judge.judge_commits(recs)
        self.assertEqual(sorted(calls), [4, 8, 8])
        self.assertEqual(set(out), {r[0] for r in recs})
        j = out[recs[0][0]]
        self.assertEqual(j.security, 0.9)
        self.assertEqual(j.impact, 1.6)
        self.assertEqual(j.impact_confidence, 0.9)
        self.assertEqual(j.model, "jev-test")

    def test_cache_skips_known_commits_and_is_written(self):
        recs = [record(i) for i in range(3)]
        cached = {"jev-test": {recs[0][0]: {"security": 0.42, "impact": 0.1, "impact_confidence": 0.5}}}
        self.cache.write_text(json.dumps(cached))
        asked = []

        def ask(state, questions):
            asked.extend(c["subject"] for c in state["commits"])
            return fake_answers(state, questions)

        judge = judgments.Judge(api_key="k", cache_path=self.cache, ask=ask, workers=1, model="jev-test")
        out = judge.judge_commits(recs)
        self.assertEqual(len(asked), 2)
        self.assertEqual(out[recs[0][0]].security, 0.42)
        on_disk = json.loads(self.cache.read_text())
        self.assertEqual(set(on_disk["jev-test"]), {r[0] for r in recs})

    def test_no_records_makes_no_request(self):
        def ask(state, questions):
            raise AssertionError("must not be called")

        judge = judgments.Judge(api_key="k", cache_path=self.cache, ask=ask)
        self.assertEqual(judge.judge_commits([]), {})


class AssessIntegrationTest(unittest.TestCase):
    def judged(self):
        recs = [record(1, "fix(auth): no default password"), record(2, "docs: typo"), record(3, "feat: rename DATA_DIR"),
                record(4, "chore: bump"), record(5, "fix: maybe sec")]
        J = judgments.Judgment
        js = {recs[0][0]: J(0.95, 0.2, 0.9, "m"), recs[1][0]: J(0.02, 0.0, 0.9, "m"), recs[2][0]: J(0.1, 1.7, 0.6, "m"),
              recs[3][0]: J(0.1, 1.0, 0.7, "m"), recs[4][0]: J(0.6, 0.0, 0.9, "m")}
        return recs, js

    def test_judged_hits_counts_and_orders(self):
        recs, js = self.judged()
        res = atu.UpstreamResult(name="n", repo="o/r", template="t", pin_kind="tag")
        atu.apply_judgments(res, recs, js)
        self.assertEqual(res.security_hits_total, 1)
        self.assertEqual(res.security_possible_total, 1)
        self.assertEqual([c.subject for c in res.security_hits], ["fix(auth): no default password", "fix: maybe sec"])
        self.assertEqual(res.security_hits[0].security, 0.95)
        self.assertEqual(res.impact_breaking_total, 1)
        self.assertEqual(res.impact_notable_total, 1)
        self.assertEqual([c.subject for c in res.impact_hits], ["feat: rename DATA_DIR", "chore: bump"])
        self.assertEqual(res.impact_hits[0].impact, 1.7)
        self.assertEqual(res.breaking_hits_total, 1)
        self.assertEqual(res.judged_by, "m")

    def test_regex_path_still_used_without_judgments(self):
        recs = [record(1, "fix: prevent SQL injection"), record(2, "docs: typo")]
        res = atu.UpstreamResult(name="n", repo="o/r", template="t", pin_kind="tag")
        atu.apply_judgments(res, recs, None)
        self.assertEqual(res.security_hits_total, 1)
        self.assertIsNone(res.judged_by)

    def test_render_shows_deployer_impact_and_hides_regex_mentions(self):
        recs, js = self.judged()
        res = atu.UpstreamResult(name="n", repo="o/r", template="t", pin_kind="tag", pin_sha="a" * 40, head_sha="b" * 40,
                                 head_date="2026-09-20", default_branch="main", commits_ahead=5)
        atu.apply_judgments(res, recs, js)
        atu.score_and_verdict(res, True)
        md = atu.render_upstream(res)
        self.assertIn("Deployer impact", md)
        self.assertIn("feat: rename DATA_DIR", md)
        self.assertIn("1.7", md)
        self.assertIn("Security fixes", md)
        self.assertIn("0.95", md)
        self.assertNotIn("Breaking-change / migration mentions", md)
        self.assertTrue(any("break existing deployments" in r for r in res.reasons), res.reasons)

    def test_delta_does_not_call_method_change_new_security_commits(self):
        recs, js = self.judged()
        res = atu.UpstreamResult(name="n", repo="o/r", template="T", pin_kind="tag", pin_sha="a" * 40, head_sha="b" * 40,
                                 head_date="2026-09-20", default_branch="main", commits_ahead=5, latest_release="v1",
                                 latest_release_date="2026-09-01")
        atu.apply_judgments(res, recs, js)
        atu.score_and_verdict(res, True)
        manifest = {"templates": [{"name": "T", "template_repo": "o/t", "upstreams": [{"name": "n", "repo": "o/r"}]}]}
        previous = {"date": "2026-09-14", "templates": [{"name": "T", "upstreams": [
            {"template": "T", "repo": "o/r", "name": "n", "verdict": res.verdict, "pin_sha": res.pin_sha, "latest_release": "v1",
             "commits_ahead": 5, "security_hits_total": 0, "judged_by": None}]}]}
        md = atu.render_report("2026-09-21", manifest, {"T": [res]}, previous, {})
        self.assertNotIn("new security-related commit(s)", md)
        self.assertIn("Nothing changed", md)
        # same method, higher count: still reported
        previous["templates"][0]["upstreams"][0]["judged_by"] = "m"
        md = atu.render_report("2026-09-21", manifest, {"T": [res]}, previous, {})
        self.assertIn("new security-related commit(s)", md)


@unittest.skipUnless(os.environ.get("TYPESAFE_API_KEY") and os.environ.get("TEMPLATES_LIVE_TESTS"), "live API test (set TEMPLATES_LIVE_TESTS=1)")
class LiveTest(unittest.TestCase):
    def test_one_real_request(self):
        judge = judgments.Judge(api_key=os.environ["TYPESAFE_API_KEY"], cache_path=None)
        # checks the wire format and the direction of the answers, not the exact calibration
        sec = ("deadbeef" * 5, "2026-09-01", "dev", "caddyhttp: fix url_pattern authorization bypass via encoded-slash traversal", "")
        doc = ("cafebabe" * 5, "2026-09-01", "dev", "docs: fix a typo in the README", "")
        out = judge.judge_commits([sec, doc])
        self.assertGreater(out[sec[0]].security, 0.5)
        self.assertLess(out[doc[0]].security, 0.5)
        self.assertTrue(all(0 <= j.impact <= 2 for j in out.values()))
        self.assertTrue(out[sec[0]].model.startswith("jev-"))
        self.assertEqual(judge.usage["requests"], 1)


if __name__ == "__main__":
    unittest.main()
