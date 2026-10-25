"""gwt: every gramps-web-toolkit tool behind one command. `gwt <tool> ARGS` imports only that tool."""
import importlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

from .version import version

TOOLS = {  # name: (module with main(argv), one line)
    "apply": ("gwt_apply.cli", "apply a JSON Lines change list to a tree, dry-run until --apply"),
    "check": ("gwt_check.cli", "read-only checks of a whole tree, with a self-test"),
    "fetch": ("gwt_fetch.cli", "download archive images and text without a browser"),
    "import-excel": ("gwt_import_excel", "turn a research workbook into a gwt-apply change list"),
    "leads": ("gwt_leads", "list a tree's gaps as queue tasks"),
    "queue": ("gwt_queue", "a durable 'what to search next' queue"),
    "ledger": ("gwt_ledger", "a shared 'already searched' ledger"),
    "rank": ("gwt_rank", "order record-search results by fit to a person"),
    "gate": ("gwt_gate", "one request pace shared by every agent on a machine"),
    "fscache": ("gwt_fscache", "an on-disk cache for rate-limited genealogy APIs"),
    "rawstore": ("gwt_rawstore", "keep source images in an S3-compatible bucket"),
    "mh": ("gwt_mh_cli", "MyHeritage records through a signed-in browser (extra: mh)"),
    "read": ("gwt_read", "FamilySearch and PARES record search and records, slow and cached"),
}


def _module(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if name != "gwt_mh_cli" or e.name != name:
            raise
    # an editable install or a clone: gwt-mh's code is the extensionless script itself
    path = Path(__file__).resolve().parents[2] / "myheritage" / "gwt-mh"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(tool, argv):
    sys.argv[0] = "gwt-" + tool
    return _module(TOOLS[tool][0]).main(argv)


def mh():
    return run("mh", sys.argv[1:])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-V", "--version"):
        print("gwt " + version())
        return 0
    if argv and argv[0] in TOOLS:
        return run(argv[0], argv[1:])
    out = sys.stdout if not argv or argv[0] in ("-h", "--help") else sys.stderr
    if out is sys.stderr:
        print("gwt: unknown tool %r" % argv[0], file=out)
    print("usage: gwt <tool> [args...]   (gwt <tool> --help for its options)\n\ntools:", file=out)
    for name, (_, line) in TOOLS.items():
        print("  %-13s %s" % (name, line), file=out)
    return 0 if out is sys.stdout else 2
