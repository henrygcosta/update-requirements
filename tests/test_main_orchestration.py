import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from update_requirements import dependency_check, git_ops
from update_requirements.__main__ import process_repository
from update_requirements.config import Config, RepoConfig
from update_requirements.dependency_check import RISK_LOW, DependencyReport
from update_requirements.report import STATUS_COMMITTED_PUSH_FAILED
from update_requirements.requirements_file import read_text_raw


def _make_config(push: bool) -> Config:
    return Config(
        branch="dev",
        commit_message="update libs",
        push=push,
        log_level="INFO",
        report_dir=Path("reports"),
        log_dir=Path("logs"),
        target_dependencies={"requests": "2.34.2"},
        repositories=[],
    )


def _args(**overrides) -> argparse.Namespace:
    ns = argparse.Namespace(dry_run=False, non_interactive=False, push=None)
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _clean_dependency_report() -> DependencyReport:
    return DependencyReport(
        risk=RISK_LOW,
        resolver_ok=True,
        resolver_output="",
        pip_check_ok=True,
        pip_check_output="",
        pipdeptree_ok=True,
        pipdeptree_output="",
    )


class ProcessRepositoryCommitRollbackTests(unittest.TestCase):
    """Regressão do Problema 2 (revisão técnica): rollback pré-commit vs. pós-commit."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.repo_path = Path(self._tmp.name)
        with open(self.repo_path / "requirements.txt", "w", encoding="utf-8", newline="") as fh:
            fh.write("requests==2.20.0\n")
        self.repo = RepoConfig(path=self.repo_path, requirements_path="requirements.txt", name="fake-repo")

    def tearDown(self):
        self._tmp.cleanup()

    @mock.patch.object(git_ops, "list_uncommitted_paths", return_value=[])
    @mock.patch.object(git_ops, "push", side_effect=git_ops.GitCommandError(["git", "push"], 1, "", "rejected"))
    @mock.patch.object(git_ops, "commit")
    @mock.patch.object(git_ops, "add")
    @mock.patch.object(dependency_check, "check_dependencies")
    @mock.patch.object(git_ops, "pull")
    @mock.patch.object(git_ops, "checkout_branch")
    @mock.patch.object(git_ops, "has_uncommitted_changes", return_value=False)
    @mock.patch.object(git_ops, "is_git_repository", return_value=True)
    def test_push_failure_after_commit_does_not_revert_file_and_reports_commit(
        self, _is_git, _dirty, _checkout, _pull, mock_check, mock_add, mock_commit, mock_push, _list_paths
    ):
        mock_check.return_value = _clean_dependency_report()
        config = _make_config(push=True)

        result = process_repository(self.repo, config, _args())

        self.assertEqual(result.status, STATUS_COMMITTED_PUSH_FAILED)
        self.assertTrue(result.committed)
        self.assertFalse(result.pushed)
        mock_commit.assert_called_once()
        mock_push.assert_called_once()

        # O commit já tinha acontecido no histórico do git quando o push falhou: o
        # requirements.txt em disco deve continuar com a versão nova (commitada),
        # NÃO revertido para a antiga — reverter aqui deixaria HEAD e working tree
        # inconsistentes entre si.
        on_disk = read_text_raw(self.repo_path / "requirements.txt")
        self.assertIn("requests==2.34.2", on_disk)

    @mock.patch.object(git_ops, "list_uncommitted_paths", return_value=[])
    @mock.patch.object(git_ops, "reset_path")
    @mock.patch.object(
        git_ops, "commit", side_effect=git_ops.GitCommandError(["git", "commit"], 1, "", "hook rejected")
    )
    @mock.patch.object(git_ops, "add")
    @mock.patch.object(dependency_check, "check_dependencies")
    @mock.patch.object(git_ops, "pull")
    @mock.patch.object(git_ops, "checkout_branch")
    @mock.patch.object(git_ops, "has_uncommitted_changes", return_value=False)
    @mock.patch.object(git_ops, "is_git_repository", return_value=True)
    def test_commit_failure_reverts_file_and_unstages(
        self, _is_git, _dirty, _checkout, _pull, mock_check, mock_add, mock_commit, mock_reset, _list_paths
    ):
        mock_check.return_value = _clean_dependency_report()
        config = _make_config(push=False)

        with self.assertRaises(git_ops.GitCommandError):
            process_repository(self.repo, config, _args())

        # Nada foi commitado: reverter o arquivo E desfazer o `git add` deixam o
        # repositório exatamente como estava antes da execução.
        mock_reset.assert_called_once_with(self.repo_path, "requirements.txt")
        on_disk = read_text_raw(self.repo_path / "requirements.txt")
        self.assertEqual(on_disk, "requests==2.20.0\n")

    @mock.patch.object(git_ops, "list_uncommitted_paths", return_value=[])
    @mock.patch.object(git_ops, "push")
    @mock.patch.object(git_ops, "commit")
    @mock.patch.object(git_ops, "add")
    @mock.patch.object(dependency_check, "check_dependencies")
    @mock.patch.object(git_ops, "pull")
    @mock.patch.object(git_ops, "checkout_branch")
    @mock.patch.object(git_ops, "has_uncommitted_changes", return_value=False)
    @mock.patch.object(git_ops, "is_git_repository", return_value=True)
    def test_successful_commit_without_push_is_reported_correctly(
        self, _is_git, _dirty, _checkout, _pull, mock_check, mock_add, mock_commit, mock_push, _list_paths
    ):
        mock_check.return_value = _clean_dependency_report()
        config = _make_config(push=False)

        result = process_repository(self.repo, config, _args())

        self.assertTrue(result.committed)
        self.assertFalse(result.pushed)
        mock_push.assert_not_called()
        on_disk = read_text_raw(self.repo_path / "requirements.txt")
        self.assertIn("requests==2.34.2", on_disk)


class StrayCheckFileCleanupTests(unittest.TestCase):
    """Regressão do Problema 7 (revisão técnica): temp file órfão não deve travar o repo."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.repo_path = Path(self._tmp.name)
        with open(self.repo_path / "requirements.txt", "w", encoding="utf-8", newline="") as fh:
            fh.write("requests==2.20.0\n")
        self.repo = RepoConfig(path=self.repo_path, requirements_path="requirements.txt", name="fake-repo")
        self.stray_file = self.repo_path / ".req_check_deadbeefdeadbeefdeadbeef.txt"
        self.stray_file.write_text("requests==2.34.2\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    @mock.patch.object(git_ops, "push")
    @mock.patch.object(git_ops, "commit")
    @mock.patch.object(git_ops, "add")
    @mock.patch.object(dependency_check, "check_dependencies")
    @mock.patch.object(git_ops, "pull")
    @mock.patch.object(git_ops, "checkout_branch")
    @mock.patch.object(git_ops, "is_git_repository", return_value=True)
    def test_stray_temp_file_is_removed_and_repo_is_not_skipped_as_dirty(
        self, _is_git, _checkout, _pull, mock_check, mock_add, mock_commit, mock_push
    ):
        # git_ops.has_uncommitted_changes/list_uncommitted_paths NÃO são mockados aqui:
        # o diretório é um repo real (git init) para exercitar o `git status` de verdade
        # contra o arquivo órfão físico criado em setUp.
        import subprocess

        subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "add", "requirements.txt"], cwd=self.repo_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "initial"],
            cwd=self.repo_path,
            check=True,
        )

        mock_check.return_value = _clean_dependency_report()
        config = _make_config(push=False)

        result = process_repository(self.repo, config, _args())

        self.assertFalse(self.stray_file.exists())
        self.assertTrue(result.committed)
        self.assertNotEqual(result.status, "dirty_skipped")

    @mock.patch.object(git_ops, "has_uncommitted_changes", return_value=True)
    @mock.patch.object(
        git_ops,
        "list_uncommitted_paths",
        return_value=[".req_check_deadbeefdeadbeefdeadbeef.txt", "some_real_change.py"],
    )
    @mock.patch.object(git_ops, "is_git_repository", return_value=True)
    def test_real_uncommitted_change_alongside_stray_file_still_skips_dirty(
        self, _is_git, mock_list_paths, mock_dirty
    ):
        config = _make_config(push=False)

        result = process_repository(self.repo, config, _args())

        # O arquivo órfão é removido de qualquer forma (sempre seguro), mas a mudança
        # real do usuário continua bloqueando o processamento deste repositório.
        self.assertFalse(self.stray_file.exists())
        self.assertEqual(result.status, "dirty_skipped")


if __name__ == "__main__":
    unittest.main()
