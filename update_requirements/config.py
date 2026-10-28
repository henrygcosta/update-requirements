"""Configuração centralizada da ferramenta, carregada de um arquivo YAML."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Erro de configuração (arquivo ausente, malformado ou incompleto)."""


@dataclass
class RepoConfig:
    path: Path
    requirements_path: str
    name: str


@dataclass
class Config:
    branch: str
    commit_message: str
    push: bool
    log_level: str
    report_dir: Path
    log_dir: Path
    target_dependencies: Dict[str, str]
    repositories: List[RepoConfig]


def _parse_target_dependencies(raw: List[str]) -> Dict[str, str]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'target_dependencies' deve ser uma lista não vazia de 'nome==versao'.")

    targets: Dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, str) or "==" not in entry:
            raise ConfigError(f"Entrada inválida em target_dependencies (esperado 'nome==versao'): {entry!r}")
        name, _, version = entry.partition("==")
        name, version = name.strip(), version.strip()
        if not name or not version:
            raise ConfigError(f"Entrada inválida em target_dependencies: {entry!r}")
        targets[name] = version
    return targets


def _parse_repositories(raw: List, default_requirements_path: str) -> List[RepoConfig]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'repositories' deve ser uma lista não vazia.")

    repositories: List[RepoConfig] = []
    seen_paths = set()
    for entry in raw:
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict) or "path" not in entry:
            raise ConfigError(f"Entrada de repositório inválida (precisa de 'path'): {entry!r}")

        repo_path = Path(entry["path"]).expanduser().resolve()
        if repo_path in seen_paths:
            raise ConfigError(f"Repositório duplicado na configuração: {repo_path}")
        seen_paths.add(repo_path)

        repositories.append(
            RepoConfig(
                path=repo_path,
                requirements_path=str(entry.get("requirements_path", default_requirements_path)),
                name=str(entry.get("name", repo_path.name)),
            )
        )
    return repositories


def load_config(config_path: Path) -> Config:
    """Carrega e valida o arquivo de configuração YAML.

    Caminhos de repositório inválidos ou que não existem NÃO abortam o carregamento
    do config: eles falham durante o processamento daquele repositório específico
    (o orquestrador continua com os demais), consistente com o requisito de
    resiliência do fluxo.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(f"Arquivo de configuração não encontrado: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError("Conteúdo do arquivo de configuração deve ser um mapeamento YAML.")

    default_requirements_path = str(raw.get("requirements_path", "requirements.txt"))
    log_level = str(raw.get("log_level", "INFO")).upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigError(f"log_level inválido: {log_level!r} (opções: {sorted(_VALID_LOG_LEVELS)})")

    return Config(
        branch=str(raw.get("branch", "dev")),
        commit_message=str(raw.get("commit_message", "update libs")),
        push=bool(raw.get("push", False)),
        log_level=log_level,
        report_dir=Path(raw.get("report_dir", "reports")),
        log_dir=Path(raw.get("log_dir", "logs")),
        target_dependencies=_parse_target_dependencies(raw.get("target_dependencies")),
        repositories=_parse_repositories(raw.get("repositories"), default_requirements_path),
    )
