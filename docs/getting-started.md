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

`GWT_URL` is the address of the site. The API address works too: a trailing `/api` is dropped. An address without `http://` or `https://` stops the run with a settings error before anything is read.

### Log in with a token instead of a password

A server that signs users in only through single sign-on (OIDC) has no password to give. On any server, a token is also easier to take back than a password. Set `GWT_TOKEN` and leave out `GWT_USERNAME` and `GWT_PASSWORD`:

```sh
GWT_URL=https://gramps.example.org
GWT_TOKEN=<a sync access token>
GWT_TREE=<the tree id the login must land in>
```

To create the token, sign in to Gramps Web, open your user settings, and under **Access tokens** choose **Add sync device**. Give it a label such as `gwt`, then copy the token it shows; the server does not keep it, so it is shown only once. The token belongs to your user and carries the `sync` scope: it can read, add, edit and delete objects, which covers every `gwt-apply` op and every `gwt-check` read, and nothing else (no user or tree administration). You can revoke it from the same list at any time: from then on it can no longer be exchanged, and a login already made with it ends when it expires (15 minutes on a default server). One token per machine or per script keeps each one revocable on its own. Your Gramps Web version must show **Access tokens** in the user settings.

The tools exchange the token at `POST /api/token/sync/` for a short-lived login, and exchange it again when that login expires. When `GWT_TOKEN` is set, a username and password in the same settings are ignored.

### How the tools identify themselves

Every request to Gramps Web carries a User-Agent, as the Gramps Web maintainer asks of every API client:

```text
gramps-web-toolkit/<version> (gwt-apply; +https://github.com/GeiserX/gramps-web-toolkit)
```

`gwt-check` sends the same with `gwt-check`. A server admin can tell the toolkit apart from the web app and from other scripts in the logs. The version is `0+checkout` when a tool runs from a clone without being installed.

## First run

1. Prove the checks can fail. This needs no server:

    ```sh
    gwt check selftest
    ```

    It plants every kind of error in a made-up tree and ends with `OK: <n> planted errors, 0 problem(s)`. Any other ending means a check did not fire.

    To watch `gwt apply` refuse a duplicate before you connect a tree, run the offline demo in `examples/` of the clone (`./demo.sh`). Its 30-person tree and change lists are invented, and the [README quick start](https://github.com/GeiserX/gramps-web-toolkit#quick-start) lists its commands.

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
