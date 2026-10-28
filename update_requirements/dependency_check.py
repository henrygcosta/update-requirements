"""Validação de conflitos de dependência com ferramentas reais do ecossistema pip.

Nenhuma regra de compatibilidade é codificada manualmente: o conflito é detectado
deixando o resolver do próprio pip (`pip install`) e `pip check` / `pipdeptree`
rodarem contra um ambiente virtual efêmero criado só para essa validação.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import uuid
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .requirements_file import write_text_raw

logger = logging.getLogger(__name__)

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

_INSTALL_TIMEOUT = 900
_CHECK_TIMEOUT = 120
_MAX_OUTPUT_LENGTH = 4000

# Convenção de nome do arquivo temporário usado para validar o requirements.txt
# atualizado sem tocar no arquivo real. Exposta aqui (e não em git_ops.py) porque é
# esta função que decide o nome; `__main__.py` usa `is_stray_check_file` para limpar
# sobras de uma execução anterior interrompida abruptamente (kill -9, queda de energia)
# antes que elas travem o repositório em "working tree suja" para sempre.
TEMP_FILE_PREFIX = ".req_check_"
TEMP_FILE_SUFFIX = ".txt"


def is_stray_check_file(path_str: str) -> bool:
    name = path_str.replace("\\", "/").rsplit("/", 1)[-1]
    return name.startswith(TEMP_FILE_PREFIX) and name.endswith(TEMP_FILE_SUFFIX)


def _truncate(text: str, limit: int = _MAX_OUTPUT_LENGTH) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n... (truncado, {omitted} caractere(s) omitido(s))"


@dataclass
class DependencyReport:
    risk: str
    resolver_ok: bool
    resolver_output: str
    pip_check_ok: Optional[bool]
    pip_check_output: str
    pipdeptree_ok: Optional[bool]
    pipdeptree_output: str

    @property
    def has_conflicts(self) -> bool:
        return self.risk in (RISK_MEDIUM, RISK_HIGH)

    def summary_lines(self) -> List[str]:
        lines = [f"Risco: {self.risk}"]
        if not self.resolver_ok:
            lines.append("- pip não conseguiu resolver as dependências (conflito de versão ou pacote indisponível):")
            lines.append(_indent(self.resolver_output))
            return lines
        if self.pip_check_ok is False:
            lines.append("- `pip check` encontrou dependências instaladas incompatíveis/quebradas:")
            lines.append(_indent(self.pip_check_output))
        if self.pipdeptree_ok is False:
            lines.append("- `pipdeptree` encontrou conflitos de versão ou ciclos na árvore de dependências:")
            lines.append(_indent(self.pipdeptree_output))
        if self.risk == RISK_LOW:
            lines.append("- Nenhum conflito encontrado.")
        return lines


def _indent(text: str, prefix: str = "    ") -> str:
    text = text.strip()
    if not text:
        return prefix + "(sem saída)"
    return "\n".join(prefix + line for line in text.splitlines())


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(command: List[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    logger.debug("run: %s (cwd=%s)", " ".join(command), cwd)
    try:
        return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command, returncode=1, stdout=exc.stdout or "", stderr=f"Timeout após {timeout}s executando: {' '.join(command)}"
        )


def check_dependencies(repo_path: Path, requirements_relative_path: str, updated_content: str) -> DependencyReport:
    """Cria um venv efêmero e valida `updated_content` como se fosse o requirements.txt do repo.

    O arquivo temporário é escrito ao lado do requirements.txt real (mesmo diretório)
    para que referências relativas (`-r outro.txt`, `-c constraints.txt`) continuem
    resolvendo corretamente; `-e .` é resolvido contra a raiz do repo (cwd do pip).
    O arquivo é removido ao final, o requirements.txt real nunca é tocado por esta função.
    """
    req_dir = (repo_path / requirements_relative_path).parent
    tmp_req_path = req_dir / f"{TEMP_FILE_PREFIX}{uuid.uuid4().hex}{TEMP_FILE_SUFFIX}"
    venv_root = Path(tempfile.mkdtemp(prefix="req-check-venv-"))

    try:
        write_text_raw(tmp_req_path, updated_content)

        logger.info("Criando ambiente virtual efêmero para validação de conflitos...")
        venv.EnvBuilder(with_pip=True, clear=True).create(str(venv_root))
        python_path = _venv_python(venv_root)

        upgrade_pip = _run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], repo_path, _CHECK_TIMEOUT)
        if upgrade_pip.returncode != 0:
            logger.warning("Falha ao atualizar pip no venv efêmero: %s", upgrade_pip.stderr.strip())

        logger.info("Instalando requirements.txt atualizado no ambiente de validação (pode levar alguns minutos)...")
        install_result = _run(
            [str(python_path), "-m", "pip", "install", "-r", str(tmp_req_path)],
            repo_path,
            _INSTALL_TIMEOUT,
        )
        resolver_ok = install_result.returncode == 0
        resolver_output = _truncate((install_result.stdout + "\n" + install_result.stderr).strip())

        if not resolver_ok:
            return DependencyReport(
                risk=RISK_HIGH,
                resolver_ok=False,
                resolver_output=resolver_output,
                pip_check_ok=None,
                pip_check_output="",
                pipdeptree_ok=None,
                pipdeptree_output="",
            )

        logger.info("Instalação concluída, rodando pip check e pipdeptree...")
        check_result = _run([str(python_path), "-m", "pip", "check"], repo_path, _CHECK_TIMEOUT)
        pip_check_ok = check_result.returncode == 0
        pip_check_output = _truncate((check_result.stdout + "\n" + check_result.stderr).strip())

        pipdeptree_install = _run(
            [str(python_path), "-m", "pip", "install", "pipdeptree"], repo_path, _CHECK_TIMEOUT
        )
        if pipdeptree_install.returncode != 0:
            pipdeptree_ok = None
            pipdeptree_output = f"Não foi possível instalar pipdeptree: {pipdeptree_install.stderr.strip()}"
        else:
            deptree_result = _run(
                [str(python_path), "-m", "pipdeptree", "--warn", "fail"], repo_path, _CHECK_TIMEOUT
            )
            pipdeptree_ok = deptree_result.returncode == 0
            pipdeptree_output = _truncate((deptree_result.stdout + "\n" + deptree_result.stderr).strip())

        risk = RISK_MEDIUM if (pip_check_ok is False or pipdeptree_ok is False) else RISK_LOW

        return DependencyReport(
            risk=risk,
            resolver_ok=True,
            resolver_output=resolver_output,
            pip_check_ok=pip_check_ok,
            pip_check_output=pip_check_output,
            pipdeptree_ok=pipdeptree_ok,
            pipdeptree_output=pipdeptree_output,
        )
    finally:
        tmp_req_path.unlink(missing_ok=True)
        shutil.rmtree(venv_root, ignore_errors=True)
