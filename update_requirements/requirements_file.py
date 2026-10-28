"""Atualização seletiva de requirements.txt preservando formatação original."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


def read_text_raw(path: Path) -> str:
    """Lê o arquivo sem tradução de newline (evita LF/CRLF sendo trocados no Windows)."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text_raw(path: Path, content: str) -> None:
    """Escreve o arquivo sem tradução de newline, preservando exatamente `content`."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


_REQ_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\[[^\]]*\])?"
    r"(?P<space>[ \t]*)"
    r"(?P<specifier>\S*)"
    r"(?P<trail>[ \t]*)$"
)

# Reconhece só o nome do pacote no início da linha, sem exigir que o resto bata no
# formato estrito acima. Usado apenas para detectar (sem editar) ocorrências de uma
# dependência-alvo em formatos que _REQ_LINE_RE não cobre (pin por hash, continuação
# de linha, espaço dentro do specifier etc.), para não tratá-las como "ausentes" e
# duplicar a entrada no final do arquivo.
_NAME_ONLY_RE = re.compile(r"^[ \t]*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize_name(name: str) -> str:
    """Normaliza nome de pacote conforme PEP 503 para comparação case/hífen-insensitive."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Change:
    name: str
    old_line: str
    new_line: str
    action: str  # "updated", "added" ou "unparseable"


def _split_line(body: str) -> Tuple[str, str, str]:
    """Separa a linha (sem o terminador) em (parte-do-requisito, marker, comentário)."""
    comment_idx = body.find("#")
    if comment_idx != -1:
        rest, comment_part = body[:comment_idx], body[comment_idx:]
    else:
        rest, comment_part = body, ""

    marker_idx = rest.find(";")
    if marker_idx != -1:
        req_part, marker_part = rest[:marker_idx], rest[marker_idx:]
    else:
        req_part, marker_part = rest, ""

    return req_part, marker_part, comment_part


def _split_ending(raw_line: str) -> Tuple[str, str]:
    for eol in ("\r\n", "\n", "\r"):
        if raw_line.endswith(eol):
            return raw_line[: -len(eol)], eol
    return raw_line, ""


def _dominant_newline(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    return "\n"


def update_requirements(content: str, targets: Dict[str, str]) -> Tuple[str, List[Change]]:
    """Atualiza somente as dependências em `targets` ({nome: versao}) dentro de `content`.

    Todo o restante do arquivo (comentários, linhas em branco, ordem, formatação,
    outras dependências) é preservado byte-a-byte. Dependências de `targets` que não
    existirem no arquivo são anexadas ao final. É idempotente: reaplicar sobre um
    conteúdo já atualizado não produz nenhuma mudança.
    """
    normalized_targets = {normalize_name(name): (name, version) for name, version in targets.items()}
    matched: set = set()
    unparseable: set = set()
    changes: List[Change] = []

    new_lines: List[str] = []
    for raw_line in content.splitlines(keepends=True):
        body, ending = _split_ending(raw_line)
        req_part, marker_part, comment_part = _split_line(body)

        match = _REQ_LINE_RE.match(req_part)
        if not match:
            loose_match = _NAME_ONLY_RE.match(req_part)
            if loose_match:
                loose_norm = normalize_name(loose_match.group("name"))
                if loose_norm in normalized_targets and loose_norm not in unparseable:
                    unparseable.add(loose_norm)
                    original_name, _ = normalized_targets[loose_norm]
                    changes.append(
                        Change(name=original_name, old_line=raw_line, new_line=raw_line, action="unparseable")
                    )
            new_lines.append(raw_line)
            continue

        norm = normalize_name(match.group("name"))
        if norm not in normalized_targets:
            new_lines.append(raw_line)
            continue

        original_name, new_version = normalized_targets[norm]
        new_specifier = f"=={new_version}"

        if match.group("specifier") == new_specifier:
            matched.add(norm)
            new_lines.append(raw_line)
            continue

        new_req_part = (
            match.group("indent")
            + match.group("name")
            + (match.group("extras") or "")
            + match.group("space")
            + new_specifier
            + match.group("trail")
        )
        new_line = new_req_part + marker_part + comment_part + ending
        matched.add(norm)
        changes.append(Change(name=original_name, old_line=raw_line, new_line=new_line, action="updated"))
        new_lines.append(new_line)

    missing = [norm for norm in normalized_targets if norm not in matched and norm not in unparseable]
    if missing:
        newline = _dominant_newline(content)
        if new_lines and not new_lines[-1].endswith(("\n", "\r")):
            new_lines[-1] = new_lines[-1] + newline
        for norm in missing:
            original_name, new_version = normalized_targets[norm]
            new_line = f"{original_name}=={new_version}{newline}"
            changes.append(Change(name=original_name, old_line="", new_line=new_line, action="added"))
            new_lines.append(new_line)

    return "".join(new_lines), changes
