import unittest

from update_requirements.dependency_check import _truncate, is_stray_check_file


class IsStrayCheckFileTests(unittest.TestCase):
    def test_matches_temp_file_in_repo_root(self):
        self.assertTrue(is_stray_check_file(".req_check_deadbeefdeadbeef.txt"))

    def test_matches_temp_file_in_subdirectory_forward_slash(self):
        self.assertTrue(is_stray_check_file("app/.req_check_deadbeefdeadbeef.txt"))

    def test_matches_temp_file_in_subdirectory_backslash(self):
        self.assertTrue(is_stray_check_file("app\\.req_check_deadbeefdeadbeef.txt"))

    def test_does_not_match_real_requirements_file(self):
        self.assertFalse(is_stray_check_file("requirements.txt"))

    def test_does_not_match_unrelated_dotfile(self):
        self.assertFalse(is_stray_check_file(".env"))

    def test_does_not_match_similarly_named_but_different_file(self):
        self.assertFalse(is_stray_check_file("req_check_notes.txt"))


class TruncateTests(unittest.TestCase):
    def test_short_text_is_returned_unchanged(self):
        self.assertEqual(_truncate("short output"), "short output")

    def test_long_text_is_capped_with_marker(self):
        text = "y" * 5000
        result = _truncate(text, limit=4000)
        self.assertLessEqual(len(result), 4000 + 100)
        self.assertIn("truncado", result)
        self.assertTrue(result.startswith("y" * 4000))


if __name__ == "__main__":
    unittest.main()
