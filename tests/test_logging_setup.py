import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from update_requirements.logging_setup import setup_logging


class SetupLoggingLevelSplitTests(unittest.TestCase):
    """Regressão do Problema 5 (revisão técnica): traceback não pode sumir com log_level=INFO."""

    def _close_handlers(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            handler.close()
        root.handlers.clear()

    def tearDown(self):
        self._close_handlers()

    def test_file_handler_always_captures_debug_even_when_console_level_is_info(self):
        with TemporaryDirectory() as tmp:
            setup_logging("INFO", Path(tmp))
            root = logging.getLogger()

            console_handler = next(
                h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            )
            file_handler = next(h for h in root.handlers if isinstance(h, logging.FileHandler))

            self.assertEqual(console_handler.level, logging.INFO)
            self.assertEqual(file_handler.level, logging.DEBUG)
            # o logger raiz não pode filtrar DEBUG antes dos handlers verem o registro
            self.assertLessEqual(root.level, logging.DEBUG)
            # fecha o FileHandler antes do TemporaryDirectory tentar se apagar (Windows
            # não permite remover um arquivo com handle aberto).
            self._close_handlers()

    def test_debug_message_reaches_log_file_with_info_configured(self):
        with TemporaryDirectory() as tmp:
            log_file = setup_logging("INFO", Path(tmp))
            logging.getLogger("update_requirements.test").debug("detalhe de diagnostico")
            content = log_file.read_text(encoding="utf-8")
            self._close_handlers()

            self.assertIn("detalhe de diagnostico", content)


if __name__ == "__main__":
    unittest.main()
