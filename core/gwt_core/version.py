"""The toolkit's version: the installed distribution's, or the checkout's pyproject.toml when run from a clone."""
import tomllib
from importlib import metadata
from pathlib import Path

DIST = "gramps-web-toolkit"


def version():
    try:
        return metadata.version(DIST)
    except metadata.PackageNotFoundError:
        pass
    try:
        with open(Path(__file__).resolve().parents[2] / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"] + "+checkout"
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"
