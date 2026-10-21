# gwt-check

`gwt-check` reads a whole Gramps Web tree and reports what is broken in it: orphans, dangling links, wrong birth and death pointers, repeated references, likely duplicates, and what a run changed compared with an earlier copy. It never writes to the tree.

It runs the [independent check](pipeline.md#the-independent-check) after an apply in one command. Before it, every round's checker wrote throwaway scripts, and no two rounds checked quite the same things.

## Install and run

```sh
cd check
python3 -m pip install -e .        # Python 3.11+, standard library only
gwt-check selftest                 # proves every check can fail (no tree needed)
gwt-check check                    # reads the live tree, runs every check
gwt-check check dump.json.gz       # the same on a saved dump
gwt-check dump dump.json.gz        # save the live tree (.gz compresses)
```

Without installing, run `check/gwt-check` from a clone.

Settings come from the environment, or from a `KEY=VALUE` file passed with `--config`, the same as [`gwt-apply`](apply.md): `GWT_URL`, `GWT_USERNAME`, `GWT_PASSWORD`, and `GWT_TREE`, the tree id the login must land in. If `GWT_TREE` is set and the token's `tree` claim differs, it stops. One login serves the whole run, and the password never reaches the output.

It exits `0` with no new findings and `1` with new findings. It exits `2` on bad input, which covers a file that is not a dump, missing settings, a failed login and a dump that came back short.

## Options of `check`

| Option | What it does |
|---|---|
| `TREE` | A dump file. Without it, the live tree is read |
| `--since OLD` | An earlier dump, or a `.gramps` backup. Adds the diff, and objects new since `OLD` become the scope |
| `--tag NAME` | Objects carrying this tag become the scope. `gwt-apply --tag` puts the tag only on the objects a run creates, so old objects the run changed are not in it: use `--since` to see those. With `--tag`, the diff counts only tagged objects as changed or new |
| `--root ID` | Gramps ID of the Ahnentafel root. With `--since`, a number that is lost or now holds another person is a finding |
| `--known FILE` | Findings to accept: an earlier `--json` report, or a list of `{"check", "key"}` |
| `--baseline OLD` | An earlier dump: the findings it already had count as known, so the exit code reflects only what is new since then. The same as running `check OLD --json` and passing that report to `--known`, in one command. The usual call is `--since OLD --baseline OLD`. It runs the checks twice, so a check takes about twice as long |
| `--json FILE` | The full report as JSON (`-` prints it instead of the text) |
| `--save-dump FILE` | Keeps the live dump, to use as `--since` next time |
| `--limit N` | Findings printed per check (default 30); the JSON has all of them |
| `--pagesize`, `--workers` | Objects per request (default 1000) and requests in flight (default 4) |

**Scope.** The structural checks always run on the whole tree. The fuzzy duplicate checks (`dup-couple-names`, `dup-person-birth`, `dup-person-relative`, `dup-person-fuzzy`, `dup-vital-event`) report a pair only when at least one side is in scope, so a run is not blamed for duplicates that were already there. Without `--since` or `--tag`, everything is in scope.

**Reasons.** Rows of a known file may carry a `reason`, and the report prints it (`known N0099: kept on purpose`). `gwt-check mark-known REPORT.json --check NAME --key KEY [--key KEY ...] --reason "..." --out known.json` adds findings of a report to a known file with the reason; it refuses a key the report does not have and a blank reason, and adding one twice changes nothing.

**Parent and child.** When one of a same-name pair is a parent of the other in the tree, a shared relative no longer makes them a duplicate (`dup-person-relative`, and the shared-relative branch of `dup-person-fuzzy`): a father and a son with one name share relatives by name across three generations. A shared birth date still does: `dup-person-birth` and "birth within 2 years" report the pair, because a person entered twice and then linked as their own child is a real duplicate.

**Known findings.** Every finding has a `check` and a `key` built from Gramps IDs, for example `repeated-ref` / `F0012 citation_list C0034`. The key stays the same from run to run while the problem does. Pass the first run's report as `--known` and the exit code then reflects only new problems.

## The checks

| Check | Fires on |
|---|---|
| `orphan-event` | An event that no person or family lists |
| `orphan-citation`, `orphan-note`, `orphan-media` | A citation, note or media object nothing refers to |
| `dangling-ref` | A handle that points at nothing, in any list or sub-reference: `child_ref`, `event_ref`, names, attributes, `media_ref`, `placeref`, `reporef`, tags, a citation's source, a family's father or mother |
| `place-not-place` | A `place` field that holds a handle of another type, or of nothing |
| `citation-no-source` | A citation with an empty source |
| `birth-death-pointer` | A birth or death pointer out of range, or pointing at an event that is not a `Primary` birth or death; or a `Primary` birth or death with the pointer at -1 |
| `repeated-ref` | The same reference twice in one list: a child in a family, a family in `family_list` or `parent_family_list`, a citation in a `citation_list` |
| `family-backlink` | A person and a family that disagree about who is in it |
| `empty-family` | A family with no father, mother or child |
| `dup-couple` | Two families with the same father and mother |
| `dup-couple-names` | Two families whose couples are different people with the same two names |
| `dup-person-birth` | Two people with the same name and a birth or baptism in the same year (day and month must not contradict) |
| `dup-person-relative` | Two people with the same name and a parent, spouse or child of the same name, unless their birth years differ |
| `dup-person-fuzzy` | Two people whose names differ only by an extra given name or surname (every word of the shorter given name is in the longer one, and the same for the surnames), born within 2 years of each other, or with a spouse or child of the same name, or a parent of the same name who is a different person. Siblings share their parents, so the same parent does not count |
| `dup-vital-event` | One person (role `Primary`) or family (role `Family`) with two Birth, Death, Baptism, Burial or Marriage events |
| `private` | Any object or sub-reference marked private |
| `place-no-coords` | A place that an event uses, without latitude or longitude |
| `surname-marker` | A surname containing `[`, such as `Vidal [¿?]` |
| `missing-object` | With `--since`: an object of the earlier tree that is gone |
| `ahnentafel-lost`, `ahnentafel-changed` | With `--since` and `--root`: a number that disappeared, or holds another person |

Names are compared lowercased, without accents, with b/v and i/y folded and the particle "de" dropped. `dup-person-fuzzy` also drops "del", "la", "las", "los" and "y", and matches "Pedro Lasa" with "Pedro José Lasa Ibarra". For a pair to count, at least one side must be in scope, or belong to a family that is. That covers an old person the run gave a new spouse or new parents. A different spelling of the same word ("Lasa" against "Lassa") is still not matched. A run that allowed namesakes still needs its homonyms read one by one.

The report also gives, without failing: events without a place (in the whole tree and in scope), the objects changed and new since `--since`, the last changed object (it should be the last item applied), and the Ahnentafel counts.

## Self-test

`gwt-check selftest` builds an invented three-generation tree, checks that it comes out clean (the positive control), then plants 32 errors, one at a time and then all together, and checks that each comes out as its finding. The test suite also silences each check in turn and requires the self-test to fail, so a check without a planted error cannot slip in.

## Speed

On a tree of 13,035 people, 3,874 families, 10,831 events, 9,120 citations, 5,589 media and 18,294 notes (about 60,000 objects), read over the internet from one Gramps Web server:

| Reading | Requests | Time |
|---|---|---|
| 100 per page, 4 in flight | 620 | 349 s |
| 2000 per page, 4 in flight | 38 | 126 s |
| 1000 per page, 4 in flight (default) | 69 | 109 s |
| 1000 per page, 8 in flight | 69 | 64 s |

Each request costs about 1.3 s before the first object, so small pages waste most of the time. The checks themselves take under a second, and reading a saved dump about one. Large pages are safe without `profile=all`. It was `profile=all` that answered 500 on a whole-tree listing.

If the tree changes while it is being read, pages shift, and a type comes back short or with repeats. `gwt-check` then stops with exit code 2 instead of reporting false orphans. Run it when no apply is writing.

## A `.gramps` backup as the earlier tree

`--since` reads a Gramps XML backup (`POST /api/exporters/gramps/file`) as well as a dump. From the backup it takes handles, IDs, change times and family links, which is all the diff and the Ahnentafel need. The checks themselves need a JSON dump, so a backup can only be the earlier tree.
