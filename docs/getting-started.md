# Getting started

The toolkit is a set of command-line tools, one per folder in the repository, installed together as one Python package. Every tool runs as `gwt <tool>`.

## What you need

- Python 3.11 or newer on macOS or Linux. The package needs only `requests`.
- A Gramps Web server and a user of the tree you want to work on, for `gwt-apply` and the live reads of `gwt-check`. No other tool talks to Gramps Web.
- Node 18 or newer with Playwright, only for `gwt-spanish-poller`.
- Python Playwright and a Chrome reachable over CDP, only for `gwt-mh`.

## Install

```sh
pipx install gramps-web-toolkit            # every Python tool
pipx install 'gramps-web-toolkit[mh]'      # the same, plus Playwright for gwt mh
gwt                                        # lists the tools
gwt --version
```

PyPI gets the package with the first release. Until then, install it from GitHub: `pipx install git+https://github.com/GeiserX/gramps-web-toolkit`. Plain `pip install` into a virtualenv works too.

The package installs one command, `gwt`. `gwt <tool> ...` runs that tool and loads nothing else, and `gwt <tool> --help` prints its options. Each tool also keeps its own `gwt-<tool>` command (`gwt-apply`, `gwt-check`, ...), so scripts written for those names keep working. The PyPI package named `gwt` is an unrelated project: install `gramps-web-toolkit`, never `gwt`.

To work on the code, install the clone in editable mode: `python3 -m pip install -e .` at its root. The tools still run from their folders too (`ledger/gwt-ledger`, `check/gwt-check`, ...).

Two tools are not in the package. [gwt-spanish-poller](spanish-poller.md) needs Node 18+ and `npm install` in its folder. [ocr-bench](ocr-bench.md) needs one virtualenv per engine, built by `ocr-bench/setup.sh`. Read their pages.

## Connect to your tree

`gwt-apply` and `gwt-check` read the same settings, from the environment or from a `KEY=VALUE` file passed with `--config`. The environment wins. Keep the file out of git.

```sh
GWT_URL=https://gramps.example.org
GWT_USERNAME=researcher
GWT_PASSWORD=...
GWT_TREE=<the tree id the login must land in>
```

Both tools compare `GWT_TREE` with the tree named in the login token and stop if they differ, so a wrong account never reads or writes the wrong tree.

## First run

1. Prove the checks can fail. This needs no server:

    ```sh
    gwt check selftest
    ```

    It plants every kind of error in a made-up tree and ends with `OK: 38 planted errors, 0 problem(s)`.

2. Save the tree once, then check it offline:

    ```sh
    gwt apply --save-snapshot tree.json.gz
    gwt check check tree.json.gz
    ```

3. Write a change list, one JSON object per line (the format is in [gwt-apply](apply.md#the-change-list)), and dry-run it against the snapshot. Nothing is written:

    ```sh
    gwt apply changes.jsonl --snapshot tree.json.gz
    ```

    The dry run prints the plan: what each op would create, what it skips because it is already there, and any conflict that would stop the run.

4. When the plan is right, write it, then check the tree again:

    ```sh
    gwt apply changes.jsonl --snapshot tree.json.gz --apply
    gwt check check --since tree.json.gz
    ```

    `--since` reads the live tree, diffs it against the snapshot and checks only what is new or changed since then.

## Next

- [The research pipeline](pipeline.md) explains who researches, who verifies, who decides and who writes.
- [The autonomous research loop](agent-loop.md) wires the queue, the ledger, the gate and the cache into one loop an agent runs on its own.
- [Hard-won lessons](hard-won.md) lists the failures behind every rule here. Read it before you point an agent at a rate-limited site.
