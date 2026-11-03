# gwt-rawstore

A research repo that commits every scanned page grows to gigabytes of packs. Clones get slow, and a partial clone fetches blobs one at a time and can hang the machine. `gwt-rawstore` keeps the bytes in an S3-compatible bucket (Garage, MinIO, AWS) and keeps only a small text manifest in git.

Standard library only, Python 3.9 or newer. Requests are signed with AWS SigV4 and use path-style addressing (`<endpoint>/<bucket>/<key>`), which Garage and MinIO need.

## What it stores

- **Key:** `<prefix><sha[:2]>/<sha256>/<original name>`. The same file uploaded twice is the same object, and the key alone proves what the content should hash to.
- **Manifest:** `rawstore.tsv` in the file's own folder, columns `sha256`, `size`, `key`, `path` (path relative to that folder). It is sorted and rewritten atomically, so it diffs cleanly. It does not touch any other manifest you keep in the folder.
- **Integrity:** the upload carries the sha256 as `x-amz-content-sha256`, so the server refuses a body that arrives damaged. The object also carries it as `x-amz-meta-sha256`. A file is recorded in the manifest, and removed locally with `--remove`, only after a `HEAD` confirms the object and its size.

## Configuration

Environment first, then the file in `$GWT_RAWSTORE_CONFIG` (default `~/.config/gwt-rawstore/config`, `KEY=value` lines). Keep that file mode `600` and out of git.

| Variable | Meaning |
|---|---|
| `GWT_RAWSTORE_ENDPOINT` | `http://host:port` of the S3 API |
| `GWT_RAWSTORE_BUCKET` | bucket name |
| `GWT_RAWSTORE_REGION` | region the server expects (Garage: its `s3_region`, often `garage`); default `us-east-1` |
| `GWT_RAWSTORE_ACCESS_KEY`, `GWT_RAWSTORE_SECRET_KEY` | a key allowed on that bucket only |
| `GWT_RAWSTORE_PREFIX` | optional key prefix, e.g. `repo-name/` |
| `GWT_RAWSTORE_CACHE` | download cache, default `~/.cache/gwt-rawstore` |
| `GWT_RAWSTORE_MAX_KB` | `check-staged` limit, default `256` |
| `GWT_RAWSTORE_ALLOW` | `check-staged` allowed globs, `:`-separated (also read from `.rawstore-allow` at the repo root, one glob per line) |

For Garage, a dedicated bucket and key:

```sh
garage bucket create my-raw
garage key create my-raw            # prints the key id and secret once
garage bucket allow --read --write my-raw --key <key id>
```

## Commands

```sh
gwt-rawstore put <files or dirs> [--remove] [--all] [-j 4]
```
Uploads files and records them. A directory is walked for binary extensions (images, PDF, archives, audio, video); `--all` takes every file. `-j`/`--jobs` sets how many uploads run at once (default 4). An object already in the bucket with the right size is not uploaded again. `--remove` deletes each local file once the store confirms it. A transient error (network, 5xx, 429, a file that changed while it was read) is retried, three attempts per file. A file that still fails gets a `FAIL <path>: <error>` line, and the run ends with `put: N of M file(s) not stored: <paths>` on stderr and stdout and exit status 1; nothing about a failed file goes into the manifest.

```sh
gwt-rawstore get <path|sha> [...] [-o TARGET]
```
Resolves a path through the `rawstore.tsv` beside it, or a sha256 (8 or more hex characters) through a bucket listing. Downloads into the cache, checks the sha256, and prints the cached path; `-o`/`--output` copies it to a file, or to a directory when several refs are given. A corrupt cache entry is fetched again. A download that does not hash right is deleted and the command fails.

```sh
gwt-rawstore verify [dirs] [--deep]
```
Every row of every `rawstore.tsv` under the given directories: the key carries the sha256, the object exists, the size and the stored sha256 match. `--deep` also downloads and hashes every object. Exit status 1 when any row is bad.

```sh
gwt-rawstore migrate <dir> [--remove | --evict] [-j 4]
```
For binaries already committed. Uploads every binary git tracks under `dir` and writes the manifests. With `--remove` it also runs `git rm --cached` on them, adds one pattern per extension (`*.jpg`, `*.pdf`) to `dir/.gitignore`, which covers every subfolder, and stages the manifests and `.gitignore`. The files stay on disk. `--evict` does the same and then deletes the local copies. Every upload is confirmed by a `HEAD` that checks the size and the sha256 before anything is untracked or deleted. The history is not rewritten in either case; commit when the diff looks right. If any upload fails, nothing is untracked.

It refuses to run when tracked files are missing from the working tree (a sparse checkout), since it cannot hash what it cannot read. Run it on a full clone, for example on a server, then pull the commit on the laptop.

### Images that live in the bucket

To keep a folder of source images (say `raw/`) out of git for good:

1. Download new ones with `gwt-fetch ... --out raw/<folder> --rawstore`: each file goes to the bucket and is deleted locally once confirmed, and `gwt-fetch` reads `rawstore.tsv` so it never downloads it again ([gwt-fetch](fetch.md)).
2. Move the ones already committed with `gwt-rawstore migrate raw --evict` on a full clone, then commit.
3. Pull that commit on the laptop. Git deletes the untracked images from its working tree and keeps the `rawstore.tsv` and `manifest.tsv` files, which `gwt-fetch --rawstore` reads to skip what is in the bucket. `gwt-rawstore get <path|sha256>` brings back one image when you need it.

After `--remove` or `--evict`, git ignores every file under `dir` with a migrated extension, including scans saved there later without `--rawstore`: they never show in `git status` and are in no bucket. Store them with `gwt-rawstore put --remove <dir>`; it is safe to repeat, since files already in the bucket come back as `have`. Migrate prints the same reminder.

```sh
gwt-rawstore check-staged [--max-kb N]
```
A pre-commit check. It refuses staged files that git sees as binary, or that have a binary extension, when they are over the limit and match no allowed glob, and tells the committer to run `gwt-rawstore put`. `rawstore/pre-commit` is a ready hook script. With lefthook:

```yaml
pre-commit:
  commands:
    rawstore:
      run: gwt-rawstore check-staged
```

## Tests

```sh
python3 -m unittest rawstore/test_gwt_rawstore.py
```

The tests run against an in-process S3 stub that, like Garage, refuses a body that does not match its `x-amz-content-sha256`, plus the signed example from the AWS SigV4 documentation, so the signer is checked against a known answer.

## Repo health: `doctor`

```sh
gwt-rawstore doctor [--repo PATH] [--json] [--max-packs 500] [--max-tmp-gb 1] [--max-binary-gb 1]
```

A read-only check of how a research repo uses the disk, for repos that many agents read and commit to. It needs no store configuration. Each finding says what to do; where that is a command, the `fix:` line is the command alone, paths quoted, ready to paste. Exit status 0 when nothing is wrong, 1 when something is, 2 when it cannot run (no such folder, not a git work tree, no git).

| Check | A finding when | Fix it prints |
|---|---|---|
| `packs` | more than `--max-packs` packs | `git repack --geometric=2 -d && git multi-pack-index write`; the message says to run it in the foreground, never while a backup runs, and in a partial clone on git older than 2.53 to upgrade git first |
| `multi-pack-index` | more than 50 packs outside the multi-pack-index (none written, or written before they arrived; the count comes from its header) | `git multi-pack-index write` |
| `lazy-fetch` | more than 100 promisor packs written in the last 24 hours: something is lazy fetching one object at a time | `export GIT_NO_LAZY_FETCH=1`, in every agent's environment |
| `binaries` | the binaries git tracks (by extension, sized from the work tree) add up to more than `--max-binary-gb` | `gwt-rawstore migrate <folder> --remove` for the three biggest folders, then commit the `rawstore.tsv` files |
| `scratch` | `tmp/`, `temp/`, `scratch/` or `.tmp/` at the top of the work tree holds more than `--max-tmp-gb` | none to paste: the message says to move scratch out of the repo |
| `cache` | a `.cache` folder at the top or one level down, or a folder named by `GWT_FSCACHE_DIR`, `GWT_GATE_DIR`, `GWT_MH_LOCK_DIR` or any `GWT_*_CACHE` inside the work tree, holds more than `--max-tmp-gb` (`GWT_LEDGER_DIR` is meant to be committed and is not checked) | none to paste: the message says to keep caches under `~/.cache` |
| `time-machine` | macOS only: `.git`, a scratch or cache folder, or a big binary folder over `--max-tmp-gb` that `tmutil isexcluded` says is backed up | `tmutil addexclusion <path>`; for `.git`, only once the remote has every commit |

It also prints as information `gc.auto` and `maintenance.auto`, and, in a quiet partial clone, whether `GIT_NO_LAZY_FETCH` is set in the shell running doctor; neither is a finding. A relative cache variable is looked up from the current folder and from the top of the repo. On Linux the Time Machine check is skipped with a note.

It never fetches. It runs only `git version`, `rev-parse`, `count-objects -v`, `config --get`, `config --get-regexp`, `ls-files` and `tmutil isexcluded`, never a command that reads an object, and starts every git with `GIT_NO_LAZY_FETCH=1` as well. A binary outside a sparse checkout is skipped, not read from the object store. Why these checks: [hard-won lessons 7, 8 and 55 to 57](hard-won.md#git-and-disk).

```sh
python3 -m unittest rawstore/test_doctor.py
```

The tests build small repos in a temp folder: many tiny packs, a tracked image, a big `tmp/` file, a `.cache` folder, and a `blob:none` partial clone over `file://`. One test records every process doctor starts and checks that no pack appears; its control step then reads a blob outside the sparse checkout and shows that a lazy fetch in that clone does add one.
