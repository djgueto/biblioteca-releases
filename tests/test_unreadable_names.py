import unittest
from pathlib import PureWindowsPath

from core.library.unreadable_names import is_unreadable_name


class UnreadableNamesTest(unittest.TestCase):
    def check(self, name):
        return is_unreadable_name(PureWindowsPath(r"\\nas\SERIES") / name)

    def test_samba_mangled_and_trailing_dot_or_space(self):
        for name in ("MKZ9R6~H", "ABC_12~1.MKV", "Muertos S.L.", "Serie "):
            self.assertTrue(self.check(name), name)

    def test_normal_names(self):
        for name in ("Muertos S.L", "Mr. Throwback", "MILF de noruega", "Serie 1x01.mkv", "PROGRA~1 extra"):
            self.assertFalse(self.check(name), name)


if __name__ == "__main__":
    unittest.main()
