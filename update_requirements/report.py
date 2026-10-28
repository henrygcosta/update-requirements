"""Modelagem e apresentação do relatório final da execução."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

STATUS_UPDATED = "updated"
STATUS_UPDATED_CONFLICT_CONFIRMED = "updated_conflict_confirmed"
STATUS_UPDATED_CONFLICT_DECLINED = "updated_conflict_declined"
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATUS_NO_CHANGES = "no_changes"
STATUS_DIRTY_SKIPPED = "dirty_skipped"
STATUS_ERROR = "unexpected_error"
STATUS_DRY_RUN = "dry_run"
STATUS_COMMITTED_PUSH_FAILED = "committed_push_failed"


@dataclass
class RepoResult:
    repo: str
    path: str
    status: str
    changed_deps: List[str] = field(default_factory=list)
    unparsed_deps: List[str] = field(default_factory=list)
    risk: Optional[str] = None
    conflict_summary: Optional[str] = None
    committed: bool = False
    pushed: bool = False
    message: str = ""


class Report:
    def __init__(self) -> None:
        self.results: List[RepoResult] = []

    def add(self, result: RepoResult) -> None:
        self.results.append(result)

    def _by_status(self, *statuses: str) -> List[RepoResult]:
        return [r for r in self.results if r.status in statuses]

    def render_console(self) -> str:
        lines = ["=" * 78, "RELATÓRIO FINAL", "=" * 78]

        buckets = [
            ("Atualizados e commitados", self._by_status(STATUS_UPDATED, STATUS_UPDATED_CONFLICT_CONFIRMED)),
            ("Sem alterações", self._by_status(STATUS_NO_CHANGES)),
            (
                "Com conflitos de dependência",
                self._by_status(
                    STATUS_UPDATED_CONFLICT_CONFIRMED,
                    STATUS_UPDATED_CONFLICT_DECLINED,
                    STATUS_AWAITING_CONFIRMATION,
                ),
            ),
            ("Aguardando confirmação do usuário", self._by_status(STATUS_AWAITING_CONFIRMATION)),
            ("Commitados, mas push falhou", self._by_status(STATUS_COMMITTED_PUSH_FAILED)),
            ("Pulados (working tree suja)", self._by_status(STATUS_DIRTY_SKIPPED)),
            ("Erro inesperado", self._by_status(STATUS_ERROR)),
            ("Dry-run (nenhuma alteração persistida)", self._by_status(STATUS_DRY_RUN)),
        ]
        for title, items in buckets:
            lines.append(f"\n{title}: {len(items)}")
            for r in items:
                extra = f" [{r.risk}]" if r.risk else ""
                lines.append(f"  - {r.repo}{extra}: {r.message}")

        with_unparsed = [r for r in self.results if r.unparsed_deps]
        if with_unparsed:
            lines.append(f"\nDependências-alvo em formato não reconhecido (revisar manualmente): {len(with_unparsed)}")
            for r in with_unparsed:
                lines.append(f"  - {r.repo}: {', '.join(r.unparsed_deps)}")

        commits = [r for r in self.results if r.committed]
        pushes = [r for r in self.results if r.pushed]
        lines.append(f"\nCommits realizados: {len(commits)}")
        for r in commits:
            lines.append(f"  - {r.repo}")
        lines.append(f"Pushes realizados: {len(pushes)}")
        for r in pushes:
            lines.append(f"  - {r.repo}")

        lines.append("=" * 78)
        return "\n".join(lines)

    def write_files(self, report_dir: Path) -> Tuple[Path, Path]:
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = report_dir / f"run_{timestamp}.json"
        md_path = report_dir / f"run_{timestamp}.md"

        json_path.write_text(
            json.dumps([asdict(r) for r in self.results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        md_path.write_text(self._render_markdown(timestamp), encoding="utf-8")
        return json_path, md_path

    def _render_markdown(self, timestamp: str) -> str:
        lines = [f"# Relatório de atualização de requirements.txt — {timestamp}", ""]
        lines.append("| Repositório | Status | Risco | Commit | Push | Deps alteradas | Não reconhecidas | Mensagem |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in self.results:
            deps = ", ".join(r.changed_deps) or "-"
            unparsed = ", ".join(r.unparsed_deps) or "-"
            message = r.message.replace("\n", " ").replace("|", "\\|")
            lines.append(
                f"| {r.repo} | {r.status} | {r.risk or '-'} | {'sim' if r.committed else 'não'} | "
                f"{'sim' if r.pushed else 'não'} | {deps} | {unparsed} | {message} |"
            )
        return "\n".join(lines)
