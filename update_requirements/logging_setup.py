"""Configuração centralizada de logging (console + arquivo por execução)."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def setup_logging(log_level: str, log_dir: Path) -> Path:
    """Configura o logger raiz e retorna o caminho do arquivo de log desta execução.

    O arquivo de log sempre recebe tudo (DEBUG+), independentemente de `log_level` —
    só o console é filtrado pelo nível configurado. Isso garante que o traceback
    completo de um erro inesperado (logado em DEBUG por `__main__.py`) nunca some
    silenciosamente só porque o console está em INFO (nível padrão).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"run_{timestamp}.log"

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return log_file
