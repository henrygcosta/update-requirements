import subprocess
import unittest
from pathlib import Path
from unittest import mock

from update_requirements import git_ops


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr="")


class ListUncommittedPathsTests(unittest.TestCase):
    @mock.patch("update_requirements.git_ops.subprocess.run")
    def test_parses_plain_status_lines(self, mock_run):
        mock_run.return_value = _completed(
            " M requirements.txt\n?? .req_check_abc123.txt\n?? some_real_change.py\n"
        )
        paths = git_ops.list_uncommitted_paths(Path("/fake/repo"))
        self.assertEqual(paths, ["requirements.txt", ".req_check_abc123.txt", "some_real_change.py"])

    @mock.patch("update_requirements.git_ops.subprocess.run")
    def test_parses_rename_lines_keeping_new_path(self, mock_run):
        mock_run.return_value = _completed('R  old_name.txt -> new_name.txt\n')
        paths = git_ops.list_uncommitted_paths(Path("/fake/repo"))
        self.assertEqual(paths, ["new_name.txt"])

    @mock.patch("update_requirements.git_ops.subprocess.run")
    def test_empty_status_returns_empty_list(self, mock_run):
        mock_run.return_value = _completed("")
        self.assertEqual(git_ops.list_uncommitted_paths(Path("/fake/repo")), [])


class GitCommandErrorTruncationTests(unittest.TestCase):
    def test_short_message_is_not_truncated(self):
        err = git_ops.GitCommandError(["git", "pull"], 1, "", "fatal: no tracking information")
        self.assertIn("fatal: no tracking information", str(err))
        self.assertNotIn("truncado", str(err))

    def test_long_message_is_truncated_but_raw_fields_kept_intact(self):
        huge_stderr = "x" * 10_000
        err = git_ops.GitCommandError(["git", "pull"], 1, "", huge_stderr)

        self.assertLess(len(str(err)), len(huge_stderr))
        self.assertIn("truncado", str(err))
        # os campos brutos do objeto continuam completos, só a mensagem é limitada.
        self.assertEqual(err.stderr, huge_stderr)


if __name__ == "__main__":
    unittest.main()
