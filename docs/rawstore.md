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
gwt-rawstore migrate <dir> [--remove] [-j 4]
```
For binaries already committed. Uploads every binary git tracks under `dir` and writes the manifests. With `--remove` it also runs `git rm --cached` on them, adds them by exact name to the folder's `.gitignore`, and stages the manifest and `.gitignore`. The files stay on disk and the history is not rewritten; commit when the diff looks right. If any upload fails, nothing is untracked. It refuses to run when tracked files are missing from the working tree (a sparse checkout), since it cannot hash what it cannot read.

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
