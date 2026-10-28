"""Wrappers finos sobre o binário git via subprocess."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_LENGTH = 4000


def _truncate(text: str, limit: int = _MAX_ERROR_MESSAGE_LENGTH) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n... (truncado, {omitted} caractere(s) omitido(s))"


class GitCommandError(RuntimeError):
    def __init__(self, command: List[str], returncode: int, stdout: str, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = _truncate(stderr.strip() or stdout.strip())
        super().__init__(f"Comando git falhou ({returncode}): {' '.join(command)}\n{detail}")


def _run(repo_path: Path, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    command = ["git", *args]
    logger.debug("git: %s (cwd=%s)", " ".join(args), repo_path)
    result = subprocess.run(command, cwd=str(repo_path), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise GitCommandError(command, result.returncode, result.stdout, result.stderr)
    return result


def is_git_repository(repo_path: Path) -> bool:
    if not repo_path.is_dir():
        return False
    result = _run(repo_path, ["rev-parse", "--is-inside-work-tree"], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def has_uncommitted_changes(repo_path: Path) -> bool:
    result = _run(repo_path, ["status", "--porcelain"])
    return bool(result.stdout.strip())


def list_uncommitted_paths(repo_path: Path) -> List[str]:
    """Retorna os caminhos (relativos à raiz do repo) de `git status --porcelain`.

    Usado para diferenciar alterações reais do usuário de sobras de uma execução
    anterior desta ferramenta (arquivos temporários de validação), sem precisar
    interpretar os dois primeiros caracteres de status de cada linha.
    """
    result = _run(repo_path, ["status", "--porcelain"])
    paths = []
    for line in result.stdout.splitlines():
        if len(line) <= 3:
            continue
        path = line[3:].strip()
        if " -> " in path:  # renomeio: "orig -> novo"
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def checkout_branch(repo_path: Path, branch: str) -> None:
    _run(repo_path, ["checkout", branch])


def pull(repo_path: Path) -> str:
    """Atualiza a branch via fast-forward only; falha (em vez de mergear) se divergiu."""
    result = _run(repo_path, ["pull", "--ff-only"])
    return result.stdout.strip()


def add(repo_path: Path, relative_path: str) -> None:
    _run(repo_path, ["add", "--", relative_path])


def reset_path(repo_path: Path, relative_path: str) -> None:
    """Remove `relative_path` da staging area (desfaz um `add` se o commit seguinte falhar).

    Usa check=False deliberadamente: é chamado a partir de um handler de rollback que já
    está tratando outra exceção, e uma falha secundária aqui não deve mascará-la.
    """
    _run(repo_path, ["reset", "--", relative_path], check=False)


def commit(repo_path: Path, message: str) -> str:
    result = _run(repo_path, ["commit", "-m", message])
    return result.stdout.strip()


def push(repo_path: Path, branch: str) -> str:
    result = _run(repo_path, ["push", "origin", branch])
    return (result.stdout + result.stderr).strip()
