"""gwt-apply: a deterministic change-list applier for Gramps Web."""

__version__ = "0.1.0"


def version() -> str:
    """The installed version: the whole toolkit's package first, then this tool's own, else a checkout."""
    from importlib.metadata import PackageNotFoundError, version as dist_version

    for dist in ("gramps-web-toolkit", "gwt-apply"):
        try:
            return dist_version(dist)
        except PackageNotFoundError:
            pass
    return "0+checkout"
