"""The toolkit's version: the installed distribution's, or one read from git when run from a clone.

A build takes its version from the git tag through hatch-vcs: `v0.2.0` builds 0.2.0, and a commit after
a tag builds a dev version that names the commit. A clone with nothing installed reads the same shape here.
"""
import subprocess
from importlib import metadata
from pathlib import Path

DIST = "gramps-web-toolkit"
ROOT = Path(__file__).resolve().parents[2]


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _from_git():
    try:
        tag, count, node = _git("describe", "--tags", "--long", "--match", "v[0-9]*").rsplit("-", 2)
    except subprocess.CalledProcessError:  # no release tag yet
        return "0.1.dev%s+g%s" % (_git("rev-list", "--count", "HEAD"), _git("rev-parse", "--short", "HEAD"))
    if count == "0":
        return tag[1:]
    parts = tag[1:].split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return "%s.dev%s+%s" % (".".join(parts), count, node)


def version():
    try:
        return metadata.version(DIST)
    except metadata.PackageNotFoundError:
        pass
    try:
        return _from_git()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return "unknown"
