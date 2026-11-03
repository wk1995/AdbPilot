"""Exercise the version-PR shell path without making GitHub writes."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("bash"), "The release workflow requires Bash")
class VersionPullRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls.jsonl"
        gh = self.root / "gh"
        gh.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            with Path(os.environ["TEST_GH_CALLS"]).open("a") as log:
                log.write(json.dumps(args) + "\\n")
            if args[:2] == ["pr", "list"]:
                if os.environ["TEST_PR_STATE"] == "error":
                    sys.exit(1)
                if os.environ["TEST_PR_STATE"] == "existing":
                    print("42")
            elif args[:2] == ["pr", "view"]:
                if os.environ["TEST_PR_STATE"] != "existing":
                    sys.exit(1)
                print("42")
            elif args[:2] == ["pr", "create"]:
                print("https://github.com/example/project/pull/43")
            elif args[:2] != ["pr", "merge"]:
                raise SystemExit("Unexpected GitHub command")
            """), encoding="utf-8")
        gh.chmod(0o755)

        # Run the actual workflow fragment, including its heredoc and set -e.
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        release = workflow.split("        id: release\n", 1)[1]
        script = textwrap.dedent(
            release.split("        run: |\n", 1)[1].split("\n  build-windows:", 1)[0]
        )
        start = script.index("pr_number=")
        end = script.index("defer_build=true", start)
        self.script = (
            'set -euo pipefail\nrelease_branch="release/desktop-versions"\n'
            + script[start:end]
        )

    def run_pr_path(self, state):
        result = subprocess.run(
            ["bash", "-c", self.script], cwd=self.root, capture_output=True, text=True,
            env={**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                 "TEST_GH_CALLS": str(self.calls), "TEST_PR_STATE": state},
        )
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return result, calls

    def test_missing_version_pr_is_created_and_auto_merge_enabled(self):
        result, calls = self.run_pr_path("missing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:2] for call in calls[1:]],
                         [["pr", "create"], ["pr", "merge"]])
        create = calls[1]
        self.assertEqual(create[create.index("--base") + 1], "master")
        self.assertEqual(create[create.index("--head") + 1], "release/desktop-versions")
        self.assertEqual(create[create.index("--body") + 1],
                         "Automated version bump. Packages are built only after this pull request merges."
                         "\n\nRequired checks merge this pull request when they pass.")
        self.assertEqual(calls[2], ["pr", "merge", "43", "--merge", "--auto"])

    def test_existing_open_version_pr_is_reused(self):
        result, calls = self.run_pr_path("existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], ["pr", "merge", "42", "--merge", "--auto"])

    def test_failed_lookup_does_not_create_or_merge_a_pr(self):
        result, calls = self.run_pr_path("error")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
