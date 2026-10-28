import tempfile
import unittest
from pathlib import Path

from update_requirements.requirements_file import read_text_raw, update_requirements, write_text_raw

TARGETS = {
    "google-api-core": "2.33.0",
    "googleapis-common-protos": "1.75.0",
    "proto-plus": "1.28.2",
    "google-cloud-bigquery-storage": "2.39.0",
    "google-cloud-secret-manager": "2.30.0",
    "requests": "2.34.2",
    "grpcio": "1.83.0",
    "protobuf": "7.36.0rc1",
}


class UpdateRequirementsTests(unittest.TestCase):
    def test_simple_version_replace(self):
        content = "google-api-core==2.10.0\nrequests==2.20.0\nflask==2.0.0\n"
        targets = {"google-api-core": "2.33.0", "requests": "2.34.2"}
        new_content, changes = update_requirements(content, targets)
        self.assertEqual(
            new_content,
            "google-api-core==2.33.0\nrequests==2.34.2\nflask==2.0.0\n",
        )
        self.assertEqual({c.name for c in changes}, {"google-api-core", "requests"})

    def test_preserves_comments_blank_lines_and_order(self):
        content = (
            "# core deps\n"
            "\n"
            "flask==2.0.0\n"
            "google-api-core==2.10.0  # pinned for compat\n"
            "\n"
            "# transitive\n"
            "requests>=2.10,<3.0\n"
        )
        targets = {"google-api-core": "2.33.0", "requests": "2.34.2"}
        new_content, _ = update_requirements(content, targets)
        lines = new_content.splitlines(keepends=True)
        self.assertEqual(lines[0], "# core deps\n")
        self.assertEqual(lines[1], "\n")
        self.assertEqual(lines[2], "flask==2.0.0\n")
        self.assertEqual(lines[3], "google-api-core==2.33.0  # pinned for compat\n")
        self.assertEqual(lines[4], "\n")
        self.assertEqual(lines[5], "# transitive\n")
        self.assertEqual(lines[6], "requests==2.34.2\n")

    def test_preserves_extras_and_markers(self):
        content = "google-api-core[grpc]>=2.0.0; python_version>='3.8'  # note\n"
        targets = {"google-api-core": "2.33.0"}
        new_content, changes = update_requirements(content, targets)
        self.assertEqual(
            new_content,
            "google-api-core[grpc]==2.33.0; python_version>='3.8'  # note\n",
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "updated")

    def test_case_and_separator_insensitive_matching(self):
        content = "Google_API.Core==2.10.0\n"
        targets = {"google-api-core": "2.33.0"}
        new_content, _ = update_requirements(content, targets)
        self.assertEqual(new_content, "Google_API.Core==2.33.0\n")

    def test_missing_dependency_is_appended(self):
        content = "flask==2.0.0\n"
        new_content, changes = update_requirements(content, {"requests": "2.34.2"})
        self.assertEqual(new_content, "flask==2.0.0\nrequests==2.34.2\n")
        self.assertEqual(changes[0].action, "added")

    def test_missing_dependency_appended_even_without_trailing_newline(self):
        content = "flask==2.0.0"
        new_content, _ = update_requirements(content, {"requests": "2.34.2"})
        self.assertEqual(new_content, "flask==2.0.0\nrequests==2.34.2\n")

    def test_appended_dependency_respects_crlf(self):
        content = "flask==2.0.0\r\n"
        new_content, _ = update_requirements(content, {"requests": "2.34.2"})
        self.assertEqual(new_content, "flask==2.0.0\r\nrequests==2.34.2\r\n")

    def test_empty_file_appends_all_targets(self):
        new_content, changes = update_requirements("", {"requests": "2.34.2", "flask": "2.0.0"})
        self.assertEqual(new_content, "requests==2.34.2\nflask==2.0.0\n")
        self.assertEqual(len(changes), 2)

    def test_idempotent_second_run_produces_no_changes(self):
        content = "google-api-core==2.10.0\nrequests==2.20.0\n"
        first_pass, _ = update_requirements(content, TARGETS)
        second_pass, changes = update_requirements(first_pass, TARGETS)
        self.assertEqual(first_pass, second_pass)
        self.assertEqual(changes, [])

    def test_option_lines_and_editable_installs_untouched(self):
        content = "-r base.txt\n--index-url https://example.com/simple\n-e .\nrequests==2.20.0\n"
        new_content, _ = update_requirements(content, TARGETS)
        self.assertIn("-r base.txt\n", new_content)
        self.assertIn("--index-url https://example.com/simple\n", new_content)
        self.assertIn("-e .\n", new_content)
        self.assertIn("requests==2.34.2\n", new_content)

    def test_prerelease_version_pin(self):
        content = "protobuf==6.30.0\n"
        targets = {"protobuf": "7.36.0rc1"}
        new_content, _ = update_requirements(content, targets)
        self.assertEqual(new_content, "protobuf==7.36.0rc1\n")

    def test_does_not_reorder_or_duplicate(self):
        content = "zlib-utils==1.0.0\nrequests==2.1.0\naardvark==0.1.0\n"
        targets = {"requests": "2.34.2"}
        new_content, _ = update_requirements(content, targets)
        self.assertEqual(
            new_content,
            "zlib-utils==1.0.0\nrequests==2.34.2\naardvark==0.1.0\n",
        )

    def test_full_target_list_updates_and_appends_missing(self):
        content = "google-api-core==2.10.0\nrequests==2.20.0\nflask==2.0.0\n"
        new_content, changes = update_requirements(content, TARGETS)
        for name, version in TARGETS.items():
            self.assertIn(f"{name}=={version}", new_content)
        self.assertIn("flask==2.0.0\n", new_content)
        updated = {c.name for c in changes if c.action == "updated"}
        added = {c.name for c in changes if c.action == "added"}
        self.assertEqual(updated, {"google-api-core", "requests"})
        self.assertEqual(added, set(TARGETS) - updated)

    def test_read_write_raw_preserve_crlf_byte_for_byte(self):
        original = "flask==2.0.0\r\nrequests==2.20.0\r\n# comment\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            with open(path, "wb") as fh:
                fh.write(original.encode("utf-8"))

            read_back = read_text_raw(path)
            self.assertEqual(read_back, original)

            write_text_raw(path, read_back)
            with open(path, "rb") as fh:
                raw_bytes = fh.read()
            self.assertEqual(raw_bytes, original.encode("utf-8"))

    def test_read_write_raw_do_not_convert_lf_to_crlf(self):
        original = "flask==2.0.0\nrequests==2.20.0\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            with open(path, "wb") as fh:
                fh.write(original.encode("utf-8"))

            content = read_text_raw(path)
            write_text_raw(path, content)
            with open(path, "rb") as fh:
                raw_bytes = fh.read()
            self.assertEqual(raw_bytes, original.encode("utf-8"))

    # Regressão do Problema 1 (revisão técnica): uma linha de dependência-alvo em
    # formato que o regex estrito não reconhece (hash pin, espaçamento incomum etc.)
    # não pode ser tratada como "ausente" — isso duplicava a entrada no final do arquivo.

    def test_hash_pinned_line_is_not_touched_and_not_duplicated(self):
        content = "flask==2.0.0\nrequests==2.20.0 --hash=sha256:deadbeef\n"
        targets = {"requests": "2.34.2"}
        new_content, changes = update_requirements(content, targets)

        self.assertEqual(new_content, content)  # nada foi tocado nem anexado
        self.assertEqual(new_content.count("requests"), 1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "unparseable")
        self.assertEqual(changes[0].name, "requests")

    def test_unparseable_line_reported_without_blocking_other_updates(self):
        content = "flask==2.0.0\nrequests==2.20.0 --hash=sha256:deadbeef\ngoogle-api-core==2.10.0\n"
        targets = {"requests": "2.34.2", "google-api-core": "2.33.0"}
        new_content, changes = update_requirements(content, targets)

        self.assertEqual(new_content.count("requests"), 1)
        self.assertIn("requests==2.20.0 --hash=sha256:deadbeef\n", new_content)
        self.assertIn("google-api-core==2.33.0\n", new_content)

        actions = {c.name: c.action for c in changes}
        self.assertEqual(actions["requests"], "unparseable")
        self.assertEqual(actions["google-api-core"], "updated")

    def test_specifier_with_internal_space_is_unparseable_not_duplicated(self):
        content = "requests >= 1.0\n"
        targets = {"requests": "2.34.2"}
        new_content, changes = update_requirements(content, targets)

        self.assertEqual(new_content, content)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "unparseable")

    def test_unparseable_is_reported_even_when_no_other_change_happens(self):
        content = "requests==2.20.0 --hash=sha256:deadbeef\n"
        targets = {"requests": "2.34.2"}
        new_content, changes = update_requirements(content, targets)

        self.assertEqual(new_content, content)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "unparseable")


if __name__ == "__main__":
    unittest.main()
