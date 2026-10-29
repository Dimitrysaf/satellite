"""meson.build lists the top-level modules one by one; a missing one breaks the installed app."""

import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PackagingTests(unittest.TestCase):
    def test_every_module_is_installed(self):
        with open(os.path.join(ROOT, "meson.build"), encoding="utf-8") as stream:
            meson = stream.read()
        for pattern in ("satellite/*.py", "satellite/plugins/*.py"):
            for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
                relative = os.path.relpath(path, ROOT)
                with self.subTest(module=relative):
                    self.assertIn("'{}'".format(relative), meson)


if __name__ == "__main__":
    unittest.main()
