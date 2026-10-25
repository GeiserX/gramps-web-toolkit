"""Tests for the gwt dispatcher. Run: cd core && python3 -m pytest -q"""
import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
for sub in ("apply", "check", "fetch", "ledger", "gate", "fscache", "queue", "leads", "rank", "importxl",
            "rawstore", "myheritage"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from gwt_core import cli, version  # noqa: E402


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Dispatcher(unittest.TestCase):
    def test_no_args_lists_every_tool(self):
        code, out, _ = run([])
        self.assertEqual(code, 0)
        for name in cli.TOOLS:
            self.assertIn("  " + name + " ", out)

    def test_version(self):
        code, out, _ = run(["--version"])
        self.assertEqual((code, out.strip()), (0, "gwt " + version()))
        self.assertNotEqual(version(), "unknown")

    def test_unknown_tool_is_an_error(self):
        code, _, err = run(["nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown tool 'nope'", err)

    def test_every_tool_answers_help(self):
        for name in cli.TOOLS:
            code, out, _ = run([name, "--help"])
            self.assertEqual(code, 0, name)
            self.assertIn("usage: gwt-" + name, out.replace("\n", " "), name)

    def test_one_tool_does_not_import_the_others(self):
        for mod in [m for m in sys.modules if m.startswith("gwt_") and m != "gwt_core" and not m.startswith("gwt_core.")]:
            del sys.modules[mod]
        run(["ledger", "--help"])
        loaded = {m.split(".")[0] for m in sys.modules if m.startswith("gwt_")} - {"gwt_core"}
        self.assertEqual(loaded, {"gwt_ledger"})
        self.assertNotIn("playwright", sys.modules)


if __name__ == "__main__":
    unittest.main()
