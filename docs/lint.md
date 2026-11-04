# gwt-lint

`gwt-lint` checks a change list against a snapshot of the tree before [gwt-apply](apply.md) runs it. `gwt-apply` checks how an op is written; `gwt-lint` checks your research rules: which people a list may touch, which people it must leave alone, what may be written about people who may still be alive, and whether another list is working on the same objects.

Every rule ships its own broken copy of a list and the code it must fire on it, so `gwt-lint --prove` shows that each rule can still go red. You never write a red control by hand.

It reads files only: the list, a snapshot, and optionally a config file and folders of other lists. It never contacts a server.

```sh
gwt-lint changes.jsonl --snapshot tree.json.gz --root I0001 --protect I0009 --weak-tag weak --rules scope,protect,living,weak
gwt-lint changes.jsonl --snapshot tree.json.gz --config lint.toml --json
gwt-lint --prove changes.jsonl --snapshot tree.json.gz --config lint.toml
gwt-lint --list-rules
```

With no `--rules`, every rule runs, and a rule whose setting is missing (overlap without `--open`, say) makes the run exit 2. Keep the settings in a `lint.toml`, or name the rules you mean.

The snapshot is a `gwt-check dump` or a `gwt-apply --save-snapshot` file, plain or gzipped. Take it right before you lint: a list judged against an old tree is judged wrongly.

The list may be JSON Lines, a JSON array or `{"ops": [...]}`, the shapes `gwt-apply` reads. Before any rule runs, `gwt-lint` runs `gwt-apply`'s own check of how each op is written, so a list `gwt-apply` would refuse exits 2 instead of linting clean.

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | every rule looked and found nothing |
| 1 | at least one finding |
| 2 | a rule had nothing to check (a missing setting, a root the snapshot does not have), the list, snapshot or config cannot be read, or `gwt-apply` would refuse the list |

2 wins over 1. A run where one rule did not look is not a clean run, whatever the other rules found, so a missing `--root` can never pass as "no scope findings".

## Output

```text
scope: 1 finding
  scope-out          op 'e-hugo-res' line 1: I7 (Hugo Vega) is not a blood relative of I1 or the spouse of one
protect: nothing to check: no protected people: give --protect or [protect] ids
living: ok
exit 2: a rule had nothing to check (1 finding from 3 rules)
```

`-v` adds each rule's notes, such as the blood path of every person in scope (`I11 (Mario Ruiz): I1 > child I6 > spouse I11`). `--json` prints one object: `exit`, and per rule its `status` (`ok`, `findings` or `nothing`), the `reason` for `nothing`, its `info` notes and its `findings`, each with `rule`, `code`, `key`, `line`, `message` and `fix`. When the run cannot start (an unreadable input, a bad setting), `--json` prints `{"list", "exit": 2, "error"}`.

## Settings

Options on the command line win over `lint.toml`. Folders in `lint.toml` are read relative to the file. A setting that takes a list of names also takes one name as a plain string (`roots = "I0001"`); a setting of the wrong type exits 2. All names and IDs below are invented.

```toml
rules = ["scope", "protect", "living", "overlap", "weak", "count"]  # default: all of them

[scope]
roots = ["I0001"]                    # --root

[protect]
ids = ["I0009", "I0010"]             # --protect
locales = ["en", "es"]               # life-event words from these locale tables; default: every locale

[living]
years = 110                          # possibly living: no death and born less than this many years ago

[overlap]
open = "lists/open"                  # --open: lists not applied yet
applied = "lists/applied"            # --applied: lists already applied

[weak]
tag = ["weak", "hipotesis"]          # --weak-tag; the first is the one a fix suggests
machine_sources = ["S0042"]          # sources whose citations come from a machine index

[count]
words = ["tree", "árbol"]            # default: the word for "tree" in every gwt_core locale
```

`--rules scope,living` runs only those rules. `--records DIR` names a folder of saved records for rules that compare a list with them: the [evidence rules](lint-evidence.md) (quote, principal, role, placement) read it, and with no `--rules` they run too. A list that cites a record or quotes one then needs saved records (exit 2 without them); one that does neither needs none.

## Rules

Each rule judges an op on the tree as the list leaves it: the people, families and events the list adds (as `key:<key>`) and the links it adds count, so a child the list links into the root's family is in scope. What the list removes does not count, so an op is judged on the tree before the removal.

*New text* is every free-text field an op writes: note and transcription text, event descriptions, citation pages, media descriptions, source titles, authors and publication details, attribute values, and the reasons ops give (`namesake_reason`, `reason`, ...). Names, IDs, types and paths are not.

### scope

Every person and family an op touches must be a blood relative of a root, or the spouse of one. Blood is the roots' ancestors and every descendant of those ancestors, through parent links of type Birth, Adopted, Unknown or none. A Stepchild, Foster or Sponsored link is never a blood line, and neither is a marriage: a spouse's brother is out. A family is in scope when one of its parents is a blood relative. An event touches the people and families that list it; a child_ref its child and family; notes, citations, media, places and sources touch nobody. `namesake_of` and `new_couple_of` name the people and families a new object is *not*, so they touch nobody either (here and in overlap).

| Code | When |
|---|---|
| `scope-out` | the op touches a person or family outside that set |
| `scope-unknown` | the op names a person, family or event that neither the snapshot nor the list has |

Nothing to check: no root, or a root the snapshot does not have.

### protect

People who asked to stay out of the tree (the tree's owners, say) get no new data. No update or merge of them, no new or moved event on them or the families they head, no date, place or other change to their events (an `update_event` that sets only what the event already has is no change: `gwt-apply` skips it, and dates compare by what they mean, as `gwt-apply` compares them), no new parent or child link, no note, citation, media, attribute or association attached to them, and no new text that names them (by Gramps ID, or first name followed by a surname word) within eight words of a life-event word. The life-event words are the `events` section of the [locale tables](rules.md): born, baptised, married, died, buried and their forms in each language.

| Code | When |
|---|---|
| `protect-event` | an event added to or moved onto a protected person or their family |
| `protect-date` | the date of a protected person's event changed |
| `protect-place` | the place of a protected person's event changed |
| `protect-link` | a parent or child link to a protected person |
| `protect-attach` | a note, citation, media, attribute or association on a protected person |
| `protect-update` | an `update_person` or `update_family` on a protected person or their family, or a merge where one side is a protected person, their family or their event |
| `protect-text` | new text names a protected person near a life-event word |
| `protect-unknown` | a protected ID is not in the snapshot, so nothing protects that person |

Tags are not data about the person, so `add_tag` and `remove_tag` pass. Text that names a protected person without a full name or ID ("her daughter was born...") is not caught.

Nothing to check: no protected IDs.

### living

Who may be living comes from `possibly_living()` in the [shared rules](rules.md): no death recorded and born less than `years` ago, an end of a DNA association, or a living bound from relatives' dates. A person the list adds counts with the events the list gives them. An event is judged when the list adds it, updates it, or moves it (`move_event`) onto someone who may be living; a moved event brings the date, place and type it has in the snapshot.

| Code | When |
|---|---|
| `living-date` | an event of someone who may be living gets a date with a day; a year, or a year and month, passes |
| `living-place` | an event of someone who may be living gets a place |
| `living-quote` | a quotation of four words or more (`«…»`, `“…”`, `"…"`; a shorter quoted span reads as a title or a name), a `Transcript` note or a transcript attached to someone who may be living, or an `update_note` putting one in a note they carry |
| `living-marriage` | a marriage event of someone who may be living |
| `living-id` | new text holds an identity number (DNI, NIE, NIR, codice fiscale, SSN with a valid check). The message shows its first three characters only. Leave it out; if it must stay, mark the note private |

Nothing to check: a snapshot with no people.

### overlap

Two lists written at the same time against one tree should not change the same objects, and a list should not repeat an update that is already in the tree.

| Code | When |
|---|---|
| `overlap-open` | the op touches a person, family, event, note, citation or media object that a list in the open folder also touches. Places, sources and repositories are shared freely and do not count |
| `overlap-applied` | an `update_*`, `set_attribute`, `add_tag` or `remove_tag` op is the same as one in the applied folder, everything but `key`, `ruling` and `tag` equal |

The open folder may hold the list being linted; it is skipped. Every `*.jsonl` directly in the folder is read.

Nothing to check: neither `--open` nor `--applied`, or a folder that does not exist.

### weak

An op that makes a weak claim must carry the weak tag:

- an `add_person` with `allow_namesake`;
- an `add_citation` with `confidence` 0 or 1 (`1.0` and `true` count, as `gwt-apply` stores them as 1), or of a source in `machine_sources`;
- an `update_citation` that lowers `confidence` to 0 or 1.

A tag counts as weak when its name, with case and accents folded, holds one of the weak tags, the same test [gwt-report](report.md) uses, so `r7-weak` or `Hipótesis` count for `weak` and `hipotesis`. The op's own `tag` counts on ops that make an object, an `add_tag` of the weak tag on the same object in the list counts, and so does the tag already on the object in the snapshot. A citation whose every target is a child_ref marked `[probable]` (by `link_child` with `probable`, an `add_note` starting with `[probable]` in the list, or a note already on the link) needs no tag: a child_ref cannot carry tags, and the mark is how a doubtful link is shown. A `link_child` with `probable` carries its own mark.

| Code | When |
|---|---|
| `weak-untagged` | a weak claim without the weak tag |
| `weak-inert-tag` | an op that makes no object has a `tag`; `gwt-apply` only tags what an op makes, so that tag lands nowhere. Use an `add_tag` op. A `merge_*` op makes the merge note `gwt-apply` writes on the winner, and its tag lands there |

Nothing to check: no weak tag.

### count

| Code | When |
|---|---|
| `count-in-text` | new text has a number within eight words of the word "tree" in any of the locales (or a `[count] words` entry): «el árbol tiene 263 personas con este apellido». Such a count is wrong as soon as another list lands. The tree word must be a whole word (`Baumann` or `Treeside` is no tree, and neither is the plural `trees`), and the number must stand alone, so the digits of `12-03-1850` or of an ID are no count. A number right before a comma names a record, it counts nothing: «acta 5, imágenes ya en el árbol». A four-digit number from 1000 to 2199 is read as a year. The finding quotes the words that fired |

## Proving the rules

`gwt-lint --prove LIST --snapshot DUMP --config lint.toml` runs each rule twice, under your own settings: on the list, and on the rule's own mutant of it (the list with one bad op added, such as a stranger for scope or a dated event on the first protected person). A rule passes only when it could look and its expected code fires more often on the mutant than on the list. So a rule that has nothing to check under your settings, fires anyway, fires another code, or cannot fire on its mutant fails, and the run exits 1:

```text
proved scope: scope-out fired on its mutant (15 on the list, 16 on the mutant)
FAILED protect: nothing to check under these settings: no protected people: give --protect or [protect] ids
FAILED overlap: no mutant can be built from this tree and these settings
exit 1: a rule did not catch its own mutant
```

Nothing is filled in for you: overlap's mutant touches an object one of *your* open lists touches, so an empty or missing open folder fails `--prove`. Run `--prove` with the same settings as the lint run it vouches for, in the same step.

CI runs `gwt-lint --prove` on [examples/changes.jsonl](https://github.com/GeiserX/gramps-web-toolkit/blob/main/examples/changes.jsonl) with the invented settings in `lint/fixtures/example/lint.toml`, and a test makes each rule blind in turn and checks that `--prove` then fails.

## Adding a rule

A rule is a class in a module listed in `RULE_MODULES` in `lint/gwt_lint/framework.py`, with a `name`, a `codes` table (code: one line) and two methods:

```python
from gwt_lint.framework import Mutant, NothingToCheck, Rule

class NoEmptyNotes(Rule):
    name = "empty-note"
    codes = {"empty-note": "a note with no text"}

    def check(self, ctx):
        # ctx.ops (each op has _line), ctx.tree (the snapshot), ctx.view (the tree with the list laid over it),
        # ctx.section("empty-note") (its settings); raise NothingToCheck(reason) when it cannot look
        return [self.finding("empty-note", op, "the note is empty") for op in ctx.ops
                if op["op"] == "add_note" and not op.get("text", "").strip()]

    def mutate(self, ctx):
        bad = {"op": "add_note", "key": "lint-prove-empty", "text": " ", "attach": [], "_line": len(ctx.ops) + 1}
        return Mutant(ctx.ops + [bad], "empty-note")
```

`check` returns findings, plus plain strings for notes `-v` prints. Its mutant must fire its code on any list, and a test should show the rule's clean list, a list with each code, and the case where it has nothing to check.
