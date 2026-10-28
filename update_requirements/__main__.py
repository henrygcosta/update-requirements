"""CLI: atualiza dependências-alvo do requirements.txt em vários repositórios Git."""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path
from typing import List

from . import dependency_check, git_ops
from .config import Config, ConfigError, RepoConfig, load_config
from .logging_setup import setup_logging
from .report import (
    STATUS_AWAITING_CONFIRMATION,
    STATUS_COMMITTED_PUSH_FAILED,
    STATUS_DIRTY_SKIPPED,
    STATUS_DRY_RUN,
    STATUS_ERROR,
    STATUS_NO_CHANGES,
    STATUS_UPDATED,
    STATUS_UPDATED_CONFLICT_CONFIRMED,
    STATUS_UPDATED_CONFLICT_DECLINED,
    Report,
    RepoResult,
)
from .requirements_file import read_text_raw, update_requirements, write_text_raw

logger = logging.getLogger("update_requirements")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="update_requirements",
        description="Atualiza dependências específicas do requirements.txt em vários repositórios Git.",
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Caminho do arquivo de configuração YAML.")
    push_group = parser.add_mutually_exclusive_group()
    push_group.add_argument("--push", dest="push", action="store_true", default=None, help="Força push após commit, sobrescrevendo o config.")
    push_group.add_argument("--no-push", dest="push", action="store_false", help="Desativa push após commit, sobrescrevendo o config.")
    parser.add_argument("--dry-run", action="store_true", help="Não escreve/commita nada; apenas mostra o que seria feito e valida conflitos.")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Não pergunta em caso de conflito: reverte a alteração e marca o repo como 'awaiting_confirmation'.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Equivalente a --log-level DEBUG.")
    parser.add_argument("--log-level", default=None, help="Sobrescreve log_level do config (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args(argv)


def _make_result(repo: RepoConfig, status: str, **kwargs) -> RepoResult:
    return RepoResult(repo=repo.name, path=str(repo.path), status=status, **kwargs)


def _cleanup_stray_check_files(repo_path: Path) -> List[str]:
    """Remove arquivos temporários de validação (`.req_check_*.txt`) deixados por uma
    execução anterior interrompida abruptamente (kill, queda de energia) antes que o
    `finally` de `dependency_check.check_dependencies` pudesse limpá-los. Sem isso, o
    arquivo órfão aparece em `git status` para sempre e trava o repositório em
    "working tree suja" em toda execução futura, exigindo remoção manual.

    Sempre seguro remover: são arquivos exclusivos desta ferramenta, nunca commitados
    (só `requirements_path` é passado para `git add`) e nunca referenciados por nada
    além da própria validação que já terminou (com sucesso ou não).
    """
    removed = []
    for path_str in git_ops.list_uncommitted_paths(repo_path):
        if dependency_check.is_stray_check_file(path_str):
            candidate = repo_path / path_str
            if candidate.is_file():
                candidate.unlink(missing_ok=True)
                removed.append(path_str)
    return removed


def process_repository(
    repo: RepoConfig, config: Config, args: argparse.Namespace, index: int = 1, total: int = 1
) -> RepoResult:
    logger.info("=== Repositório %d/%d: %s (%s) ===", index, total, repo.name, repo.path)

    if not git_ops.is_git_repository(repo.path):
        return _make_result(
            repo, STATUS_ERROR, message="Caminho não existe ou não é um repositório git válido."
        )

    stray_files = _cleanup_stray_check_files(repo.path)
    if stray_files:
        logger.warning(
            "%s: removido(s) %d arquivo(s) temporário(s) órfão(s) de uma execução anterior interrompida: %s",
            repo.name,
            len(stray_files),
            ", ".join(stray_files),
        )

    if git_ops.has_uncommitted_changes(repo.path):
        logger.warning("Repositório %s possui alterações não commitadas; pulando por segurança.", repo.name)
        return _make_result(
            repo,
            STATUS_DIRTY_SKIPPED,
            message="Working tree com alterações não commitadas antes de iniciar; repositório pulado por segurança.",
        )

    git_ops.checkout_branch(repo.path, config.branch)
    git_ops.pull(repo.path)

    req_file_path = repo.path / repo.requirements_path
    if not req_file_path.is_file():
        return _make_result(
            repo, STATUS_ERROR, message=f"{repo.requirements_path} não encontrado no repositório."
        )

    old_content = read_text_raw(req_file_path)
    new_content, changes = update_requirements(old_content, config.target_dependencies)

    unparsed_names = sorted({c.name for c in changes if c.action == "unparseable"})
    if unparsed_names:
        logger.warning(
            "%s: dependência(s)-alvo em formato não reconhecido (pin por hash, continuação de linha, "
            "espaçamento incomum etc.) NÃO foram atualizadas automaticamente: %s. Revise manualmente.",
            repo.name,
            ", ".join(unparsed_names),
        )

    if new_content == old_content:
        logger.info("Nenhuma alteração necessária em %s.", repo.name)
        message = "requirements.txt já está com as versões-alvo."
        if unparsed_names:
            message += (
                f" ATENÇÃO: {', '.join(unparsed_names)} está(ão) em formato não reconhecido "
                "e não pôde(ram) ser validada(s) — revise manualmente."
            )
        return _make_result(repo, STATUS_NO_CHANGES, unparsed_deps=unparsed_names, message=message)

    changed_names = sorted({c.name for c in changes if c.action in ("updated", "added")})
    logger.info("Alterações detectadas em %s: %s", repo.name, ", ".join(changed_names))

    if args.dry_run:
        dep_report = dependency_check.check_dependencies(repo.path, repo.requirements_path, new_content)
        summary = "\n".join(dep_report.summary_lines())
        logger.info("[dry-run] %s:\n%s", repo.name, summary)
        return _make_result(
            repo,
            STATUS_DRY_RUN,
            changed_deps=changed_names,
            unparsed_deps=unparsed_names,
            risk=dep_report.risk,
            conflict_summary=summary,
            message="Dry-run: nenhuma alteração foi persistida em disco nem no git.",
        )

    write_text_raw(req_file_path, new_content)
    try:
        dep_report = dependency_check.check_dependencies(repo.path, repo.requirements_path, new_content)
        conflict_summary = "\n".join(dep_report.summary_lines())

        if dep_report.has_conflicts:
            logger.warning("Conflitos de dependência detectados em %s:\n%s", repo.name, conflict_summary)
            print(f"\nForam encontrados conflitos de dependências no repositório '{repo.name}'.")
            print(conflict_summary)

            if args.non_interactive:
                write_text_raw(req_file_path, old_content)
                return _make_result(
                    repo,
                    STATUS_AWAITING_CONFIRMATION,
                    changed_deps=changed_names,
                    unparsed_deps=unparsed_names,
                    risk=dep_report.risk,
                    conflict_summary=conflict_summary,
                    message="Conflitos encontrados em modo não-interativo; alteração revertida, aguardando revisão manual.",
                )

            answer = input("Deseja realizar o commit mesmo assim? (y/N) ").strip().lower()
            if answer not in ("y", "yes", "s", "sim"):
                write_text_raw(req_file_path, old_content)
                logger.info("Commit recusado pelo usuário para %s; alteração revertida.", repo.name)
                return _make_result(
                    repo,
                    STATUS_UPDATED_CONFLICT_DECLINED,
                    changed_deps=changed_names,
                    unparsed_deps=unparsed_names,
                    risk=dep_report.risk,
                    conflict_summary=conflict_summary,
                    message="Usuário recusou o commit devido a conflitos; alteração revertida.",
                )
            status = STATUS_UPDATED_CONFLICT_CONFIRMED
            message = "Commitado após confirmação do usuário, apesar de conflitos detectados."
        else:
            status = STATUS_UPDATED
            message = "Atualizado e commitado sem conflitos detectados."

        git_ops.add(repo.path, repo.requirements_path)
        git_ops.commit(repo.path, config.commit_message)
    except Exception:
        # Nada foi commitado ainda neste ponto: reverter arquivo E desfazer um
        # eventual `git add` deixa o repositório exatamente como estava antes.
        write_text_raw(req_file_path, old_content)
        git_ops.reset_path(repo.path, repo.requirements_path)
        raise

    # Ponto de não-retorno: o commit já está no histórico do git. A partir daqui uma
    # falha (ex.: push) NÃO deve reverter o arquivo em disco — o HEAD já tem o novo
    # conteúdo commitado — nem fazer o relatório esquecer que o commit aconteceu.
    pushed = False
    push_enabled = config.push if args.push is None else args.push
    if push_enabled:
        try:
            git_ops.push(repo.path, config.branch)
            pushed = True
        except Exception as exc:
            logger.error("Commit realizado em %s, mas o push falhou: %s", repo.name, exc)
            return _make_result(
                repo,
                STATUS_COMMITTED_PUSH_FAILED,
                changed_deps=changed_names,
                unparsed_deps=unparsed_names,
                risk=dep_report.risk,
                conflict_summary=conflict_summary,
                committed=True,
                pushed=False,
                message=f"Commit realizado com sucesso, mas o push falhou: {exc}",
            )

    if unparsed_names:
        message += (
            f" ATENÇÃO: {', '.join(unparsed_names)} está(ão) em formato não reconhecido "
            "e não pôde(ram) ser atualizada(s) automaticamente — revise manualmente."
        )

    return _make_result(
        repo,
        status,
        changed_deps=changed_names,
        unparsed_deps=unparsed_names,
        risk=dep_report.risk,
        conflict_summary=conflict_summary,
        committed=True,
        pushed=pushed,
        message=message,
    )


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2

    log_level = "DEBUG" if args.verbose else (args.log_level or config.log_level)
    log_file = setup_logging(log_level, config.log_dir)
    logger.info("Log desta execução: %s", log_file)
    logger.info("%d repositório(s) configurado(s). Branch alvo: %s", len(config.repositories), config.branch)
    if args.dry_run:
        logger.info("Modo dry-run ativado: nenhuma alteração será persistida.")

    report = Report()
    total = len(config.repositories)
    for idx, repo in enumerate(config.repositories, start=1):
        try:
            result = process_repository(repo, config, args, index=idx, total=total)
        except Exception as exc:  # noqa: BLE001 - deve continuar processando os demais repos
            logger.error("Erro inesperado processando %s: %s", repo.name, exc)
            logger.debug(traceback.format_exc())
            result = _make_result(repo, STATUS_ERROR, message=str(exc))
        report.add(result)

    console_report = report.render_console()
    print("\n" + console_report)
    logger.info("Execução finalizada.")

    json_path, md_path = report.write_files(config.report_dir)
    logger.info("Relatório salvo em: %s e %s", json_path, md_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
