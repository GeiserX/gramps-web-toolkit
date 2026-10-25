# Getting started

The toolkit is a set of separate command-line tools, one per folder. You install only the ones you use. Most need nothing but Python's standard library.

## What you need

- Python 3.11 or newer for `gwt-apply`, `gwt-check`, `gwt-fetch` and `gwt-mh`. The other Python tools run on 3.8 or newer.
- A Gramps Web server and a user of the tree you want to work on, for `gwt-apply` and the live reads of `gwt-check`. No other tool talks to Gramps Web.
- Node 18 or newer with Playwright, only for `gwt-spanish-poller`.
- Python Playwright and a Chrome reachable over CDP, only for `gwt-mh`.

## Install

```sh
git clone https://github.com/GeiserX/gramps-web-toolkit && cd gramps-web-toolkit
python3 -m pip install -e apply      # gwt-apply, needs requests
python3 -m pip install -e check      # gwt-check, standard library only
python3 -m pip install -e fetch      # gwt-fetch, needs requests
```

The other tools run from their folders in the clone; symlink them onto your `PATH` if you like. A few need more than Python:

| Tool | Run it as | Needs |
|---|---|---|
| [gwt-import-excel](import-excel.md) | `importxl/gwt-import-excel` | standard library |
| [gwt-leads](leads.md) | `leads/gwt-leads` | standard library |
| [gwt-queue](queue.md) | `queue/gwt-queue` | standard library |
| [gwt-ledger](ledger.md) | `ledger/gwt-ledger` | standard library |
| [gwt-rank](rank.md) | `rank/gwt-rank` | standard library |
| [gwt-gate](gate.md) | `gate/gwt-gate` | standard library, macOS or Linux |
| [gwt-fscache](fscache.md) | `fscache/gwt-fscache` | standard library |
| [gwt-rawstore](rawstore.md) | `rawstore/gwt-rawstore` | standard library and an S3-compatible bucket |
| [gwt-mh](myheritage.md) | `myheritage/gwt-mh` | `pip install playwright` and a Chrome over CDP |
| [gwt-spanish-poller](spanish-poller.md) | `spanish-poller/gwt-spanish-poller` | Node 18+, `npm install` in its folder |
| [ocr-bench](ocr-bench.md) | `ocr-bench/run.sh` | one virtualenv per engine, built by `ocr-bench/setup.sh` |

Every `gwt-` Python tool prints its options with `--help`. For `gwt-spanish-poller` and `ocr-bench`, read their pages.

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
    gwt-check selftest
    ```

    It plants every kind of error in a made-up tree and ends with `OK: 38 planted errors, 0 problem(s)`.

2. Save the tree once, then check it offline:

    ```sh
    gwt-apply --save-snapshot tree.json.gz
    gwt-check check tree.json.gz
    ```

3. Write a change list, one JSON object per line (the format is in [gwt-apply](apply.md#the-change-list)), and dry-run it against the snapshot. Nothing is written:

    ```sh
    gwt-apply changes.jsonl --snapshot tree.json.gz
    ```

    The dry run prints the plan: what each op would create, what it skips because it is already there, and any conflict that would stop the run.

4. When the plan is right, write it, then check the tree again:

    ```sh
    gwt-apply changes.jsonl --snapshot tree.json.gz --apply
    gwt-check check --since tree.json.gz
    ```

    `--since` reads the live tree, diffs it against the snapshot and checks only what is new or changed since then.

## Next

- [The research pipeline](pipeline.md) explains who researches, who verifies, who decides and who writes.
- [The autonomous research loop](agent-loop.md) wires the queue, the ledger, the gate and the cache into one loop an agent runs on its own.
- [Hard-won lessons](hard-won.md) lists the failures behind every rule here. Read it before you point an agent at a rate-limited site.
