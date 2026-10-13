"""Tests for which release the assessment reports as "newest".

Run from the repository root:  python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assess_template_updates as atu  # noqa: E402


def tags(*names: str, pre: set[str] = frozenset()) -> list[atu.ReleaseInfo]:
    out = [atu.ReleaseInfo(tag=n, date="2026-10-01", sha=f"{i:040x}",
                           prerelease=atu.is_prerelease(n) or n in pre)
           for i, n in enumerate(names)]
    out.sort(key=lambda t: atu.version_key(t.tag))
    return out


class NewestReleaseTest(unittest.TestCase):
    def test_prerelease_pin_follows_the_prerelease_line(self):
        # YaoApp/yao on 2026-10-09: the last stable tag is from 2023, the pin is v1.0.0-rc22.
        yao = tags("v0.10.2", "v0.10.3", "v1.0.0-rc22", "v1.0.0-rc25", "v1.0.0-rc26")
        self.assertEqual(atu.newest_release(yao, pinned_is_pre=True).tag, "v1.0.0-rc26")

    def test_prerelease_pin_sees_the_final_release(self):
        yao = tags("v0.10.3", "v1.0.0-rc26", "v1.0.0")
        self.assertEqual(atu.newest_release(yao, pinned_is_pre=True).tag, "v1.0.0")

    def test_stable_pin_ignores_prereleases(self):
        marmot = tags("v0.10.0", "v0.11.0", "v0.12.0-preview1")
        self.assertEqual(atu.newest_release(marmot, pinned_is_pre=False).tag, "v0.11.0")

    def test_stable_pin_respects_the_release_api_flag(self):
        # CaviraOSS/LongMemory tags a beta as a stable-looking v1.3.0.
        lm = tags("v1.2.3", "v1.3.0", pre={"v1.3.0"})
        self.assertEqual(atu.newest_release(lm, pinned_is_pre=False).tag, "v1.2.3")

    def test_only_prereleases_upstream(self):
        nn = tags("v1.0-beta.36", "v1.0-beta.38")
        self.assertEqual(atu.newest_release(nn, pinned_is_pre=False).tag, "v1.0-beta.38")


if __name__ == "__main__":
    unittest.main()
