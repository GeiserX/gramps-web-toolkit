# Changelog

Every release of gramps-web-toolkit, newest first. The format follows https://keepachangelog.com/en/1.1.0/ and the versions follow https://semver.org/. A release is a `v*` tag; PyPI gets the package from it.

Every release has a **Change lists** heading. It names each change that makes a change list that worked before behave differently: an op that is now refused, a field that is now required, a different skip or duplicate rule, a different default. Read it before applying an old list with a new version. "None" is a valid entry; a missing heading is not, and the release workflow stops on it.

## [Unreleased]

### Fixed

- `gwt-corpus update` indexes `.tsv`, `.csv` and `.eml` files now; it used to pass over them in silence, so a search said «not downloaded yet» for tables and saved mail already on disk. A TSV or CSV is one row per line, so a hit points at its row (`people.tsv p.2`). A mail is its decoded headers and its text parts, each in its own charset; attachments that are not text stay out, and the HTML copy of a plain part is not read twice. A TSV with a `person_key` column (a gwt-ledger or gwt-queue file) is left out, because a person would find the query asked for them.
- `gwt-corpus update` reads a list of hits under a JSON key `links` now, one row per hit with the hit's own `h`, `href` or `url`, so each hit points at its record. Every value under `links` used to be dropped, so a saved search answer of that shape was indexed with no text. A GEDCOM X `links` object (hrefs) is still left out.

## [1.1.0] - 2026-11-03

### Change lists

- `gwt-apply`: an `add_person` whose key already names a person in the tree with a different first name, surname, or given gender or Gramps ID is a conflict now, not a skip, so evidence that follows it is never hung on someone else. Updates in the same list and a rerun proven by the list's own apply log still pass. The conflict names each differing field: make the op match the tree, or give it a new key.

### Added

- An agent skill, `skills/gramps-web-toolkit/SKILL.md` in the Agent Skills format. It teaches an AI agent the safe order of steps for changing a tree (snapshot, change list, lint, dry run, `gwt run`, verify) and the traps, and points to `--help` and the docs for the rest. Install it with `npx skills add GeiserX/gramps-web-toolkit --skill gramps-web-toolkit` or `gh skill install`. A test in `skills/` fails when the skill names a tool, subcommand or option the tools do not have, or links a docs page or heading that does not exist.

### Changed

- `gwt-report digest --lang es|fr` translates the digest's own text now: title, side headings, item labels, confidence, `[weak]` and the cut note, not only the relationship names. Event types, sources, places and descriptions stay as the tree stores them. English output is unchanged.
- `gwt-apply`: an apply that finds the tree locked says how many seconds are left on the holder's lease, and to pass `--break-lock` only after checking on the holder's host that it stopped, since a PID checked on another machine proves nothing.

### Fixed

- `gwt-apply LIST --apply --save-snapshot FILE` exits 2 now. It used to save the snapshot, skip the list and exit 0, so the caller read a write that never happened. Save the snapshot, then apply in a second command.
- `gwt report digest` no longer prints a web address twice on one line when the item's text already shows it, as a citation page that holds the record's address does. Only whole addresses count, so a shorter address that starts the same way is still listed. In `--format html` an address inside the text is now a link where it stands.

## [1.0.0] - 2026-11-03

### Change lists

- `gwt-apply`: `detach`, `remove_tag` and `unlink_child` of something the target does not have are a conflict now, not a skip, so a mistyped ID no longer passes a dry run. Add `expect: "absent"` to an op that may find it already gone; `--verify` still reads it as landed. A rerun of the same list after a crash skips the removals an earlier `--apply` of it logged as done. The skip line of an `update_source` or `update_family` that changes nothing now reads `already has those values`. `attach_existing` is unchanged unless it says `expect: "absent"`.
- `gwt-apply` dates: `julian ...` and `french ...` dates (`julian 1700-02-11`, `french an XII-01-01`) and `text: ...` text-only dates are read now; they used to be refused. Two dates with the same numbers in different calendars, or two text-only dates with different texts, are now different dates when an op compares them (the event reuse of `add_event`, `merge_event`, `update_*` skips), where they used to count as the same. Gregorian dates compare as before.

### Added

- `gwt-rawstore doctor [--repo PATH] [--json]` checks how a research repo uses the disk and prints the fix for each finding: too many packs (`--max-packs`, default 500), more than 50 packs outside the multi-pack-index, more than 100 promisor packs written in a day (lazy fetching), tracked binaries over `--max-binary-gb` (default 1), scratch or cache folders inside the work tree over `--max-tmp-gb` (default 1), and on macOS big folders Time Machine still backs up. It is read-only and never fetches. Exit 0 clean, 1 findings, 2 cannot run. Hard-won lessons 55 to 57 explain the partial-clone rules behind it.
- `gwt-leads --archives builtin:es,fr` (country codes, or `builtin:all`) reads a catalogue of about 200 archives and record portals in 79 countries that ships with the tool, one TSV per country under `leads/archives/` (`xx` holds the sources that span countries). A gap's record gets every row whose place names match a level of its place, whose record types hold it and whose years and `coverage_places` include it: the first as `where`, the rest as `also` (deepest level first, online sources first within a level, and a region row only for places in its own country), and the rows that match the place but cannot hold this record under `outside` with why, so a search there is not counted as a negative. The queue note lists up to three. Catalogue urls take `{given}`, `{surname}`, `{code}`, `{year_from}` and `{year_to}` besides `{place}` and `{year}`, every value URL-encoded. `gwt-leads archives probe [--country CC]` checks the urls live (robots.txt first, two seconds between requests) and dates the ones that answer; it is for maintainers and never runs in CI. `--archives FILE` reads your own file exactly as before.
- `gwt-apply` ops for what lists used to write by hand through the API: `update_media` (description, date, path, private, and the file itself against its current MD5), `delete` (only an object nothing points at; `--revert` makes it again), `merge_place`, `merge_source` and `merge_citation` (the server's merge), `reorder_refs`, `add_alternate_name` and `remove_alternate_name` (people and places), `set_external_id` (FamilySearch in `_FSFTID`). `private` in every `update_*`; `call_name`, `nickname`, `title`, `suffix` in `update_person`; `father` and `mother` in `update_family` for a family that lacks one; `source` with `expect_source` in `update_citation`; a region (`rect`) on `add_media` and `attach_existing`; `add_media` refuses text, HTML, JSON and mail files unless `force`. `ops_table()` lists every op with its fields.
- `gwt-geocode`, a new tool that puts coordinates on the places a tree's events use, so the Gramps Web map is not empty. `gwt-geocode --snapshot DUMP --out changes.jsonl` reads a `gwt-check dump` or `gwt-apply` snapshot offline and writes `update_place` ops with `lat` and `long` for `gwt-apply`, plus a TSV report (match, provider, confidence, distance). Places no event uses never get a point. Each place is looked up with its hierarchy through CartoCiudad (Spain), the BAN (France), a GeoNames dump you downloaded (any country) and Nominatim (anywhere, one request a second, cached on disk). A church, building or cemetery must lie within 5 km of its town (a street within 2 km), else it gets the town's point and the report says why; a church never matches a namesake that only shares its words; an `--overrides` file always wins; a point already in the tree is kept unless `--recheck` asks again, and even then a church's own point is never replaced by its town's.
- `gwt-read <source> control` runs the source's canary searches (public, neutral queries with a known minimum of rows) without the cache, and exits 3 with a `void` line when one reads less. `search --controls before|after|both` runs them around one search, read without the cache: a short canary before means no search, after means the run is void, and the files and cached answers the search left are removed. Each source declares the marks of a page with rows and the site's own "nothing found" words; `gwt_reader.classify()` calls any other page `unknown`, a STOP. Sources now come from one registry (`gwt_reader.SOURCE_MODULES`, `read/_template.py`, [gwt-read sources](https://geiserx.github.io/gramps-web-toolkit/sources/)), and `run_tests.py --mutants` also reads `<pkg>/mutants.d/*.txt`. Existing commands, options and output are unchanged, except the new optional `place_orig` key on FamilySearch candidates (below).
- `gwt-read familysearch record`: a 403 whose answer says `Access to artifact denied` or names the record's permissions without `ThemisPrmAnyone`, or a 403 while the `--control-ark` record still answers, is `restricted` (exit 5, an envelope with `access: restricted`), and the gate only logs it as a denial. A plain 403 with no `--control-ark`, or one whose control read fails, is still a block.
- `gwt-fscache`: `FSCache.forget(method, url, headers)` drops one stored answer.
- `gwt-read familysearch` candidates carry `place_orig`, the event place as the index typed it, when the record has one: the `_ORIG` place and parish fields, and the typed place some collections keep inside `EXT_DATA` or `FS_UNSUPPORTED`.
- `gwt-report`, a new tool that reads an offline dump or snapshot. `gwt-report kin DUMP A B` lists every blood path between two people (through Birth, Adopted or Unknown parent links, never a marriage or a step link), the common ancestors with their Ahnentafel numbers from each side, the relationship name in English, Spanish and French (cousins of any degree and removal, half relations), how many links of each path are confirmed, probable, weak-tagged or unmarked, and implex in either pedigree. `gwt-report digest --from OLD --to NEW` lists the new people, events, citations and media by person and, with `--root`, by side and kin distance, with Gramps Web links (`--base-url`) and the web addresses their citations hold; `--told FILE --audience NAME` leaves out what that audience was told, and `--mark` records what it shows. Double cousins get both relationships, a big pedigree keeps both sides (the path cap drops the most distant paths first, and a digest says when it cut), tags that say `débil` or `faible` are weak by default, a digest of two different trees exits 2, and `--format html` keeps to the inline tags chat apps accept.
- `gwt-fetch --rawstore` (on `iiif`, `digibib` and `gazeta fetch`) moves each downloaded image or PDF to the gwt-rawstore bucket and deletes the local copy once a `HEAD` confirms it. `gwt-rawstore migrate --evict` does the same for images already committed: upload, `git rm --cached`, then delete the local copies, once every upload is confirmed.
- `gwt-check` finds media Gramps Web cannot show. `media-non-viewable`: a text, JSON, HTML, mail or archive file, shown only as a grey icon. `media-file-missing`: a media object whose file the server does not have (one request on a live check). With `--download`, which reads each image's 256 px thumbnail and needs Pillow: `media-tiny` (longer side under 200 px), `media-uniform` (a blank, grey or failed image) and `media-dup-page` (the same page twice on one citation, as different files). Without Pillow those three are skipped with a note. `gwt-check dump --download` keeps the measures in the dump for an offline check. `media-uniform` and `media-dup-page` are for information only. The answers sit in the dump under `media_status`, beside the objects, so gwt-apply can still write objects from a dump back. An offline check of a dump without them (an older dump, a gwt-apply snapshot) now prints `media files not checked: N`.
- `gwt_core.rules`, the shared rules new tools import: `possibly_living()` returns whether a person may be alive with the reason (`child-born-1990`, `dna-match`) and the latest year they can have been born, inferring from any dated event of the person's own and of their children, grandchildren, spouses, siblings and parents, reading every date by the latest year it allows (the end of a range, no end for «after», other calendars through their day number), and counting a DNA match with no dates as living; `find_ids()` finds a Spanish, French, Italian or US identity number with a right check digit in text of any language; `fold()` folds names with a language's spelling pairs and particles; `read_date()` reads gwt-apply's date grammar and, with a language, written dates such as «12 de marzo de 1890». Word tables (months, record roles, life events, church-name abbreviations) for es, en, fr, it, pt, de, nl, ca, eu, gl and la ship in the package. No tool uses them yet; nothing changes for existing tools.
- `gwt-ledger` keeps one name per source, proof for every negative, and requests to archives. `ledger/sources.tsv` is a shipped registry of sources (id, aliases, country, kind, covered years), extended by your own file (`--registry` or `$GWT_LEDGER_SOURCES`); `add` and `check` store and match the canonical id, so `fs` and `familysearch-index` rows answer a check for `familysearch` (the ids match `gwt read`'s), and a source close to a known name (a typo, a spelling drift) exits 2 unless `--new-source`; a source far from every known name is kept as its slug with a note. New results `restricted`, `paid`, `in-person`, `out-of-coverage` and `unproven`; `add --result none` without `--control-ok RUN` (a passing `gwt read <source> control` output, or a run id; a path that names no file is refused) is stored as `unproven` and counts as not done. `check` takes `--agent`, `--label` and `--date` like `add`, and works with `--person` and `--source` alone. New: `stats --by source` (hit, partial and blocked rates), `coverage --dump --sources [--root]` (a person by source matrix with `n/a` outside a source's years; rows keyed by Gramps id, `<id>-...` or a name only one person has), `import-tsv FILE --map [--control-ok RUN]` (column-mapped import with a synonym map for results, the same control check as `add`, and rows already in the ledger skipped), `request add|update|list|due` and `costs --by payer|archive|person` (requests with channel, thread, due date, cost, currency and payer), and `due --ics` (an iCalendar file of every pending date). The ledger gains a tenth column, `control`; older files read as before, and `list` and `check` print it. `gwt-queue done` takes the new results too. Breaking for scripts: a source name close to a registry name exits 2 (use the known name, add an alias in a `--registry` file, or pass `--new-source`), and a `none` with no control run is stored as `unproven`. A copy of `gwt_ledger.py` outside the package needs `sources.tsv` beside it.
- Four new `gwt-read` sources, open APIs with no account: `openarch` (Open Archives: person records from Dutch, Belgian and other archives; `record ARCHIVE:UUID [--gedcom]` reads every person of a record and can save its GEDCOM), `wikitree` (WikiTree public profiles; `record ID --ancestors N`), `archiveorg` (Internet Archive catalogue, or `--fulltext` inside the scanned text, with each item's IIIF manifest and OCR link) and `matchid` (France's deaths since 1970, the online twin of `insee`, optional `$MATCHID_TOKEN`). Each has its own gate, cache rules and canary, reads "none" only from the API's own zero count, and treats an answer of any other shape as a STOP. A 5xx or no answer from these APIs is an outage, exit 3, through the new `gwt_reader.get_api()`. Pages: [gwt-read sources](https://geiserx.github.io/gramps-web-toolkit/sources/).
- `gwt-fetch iiif --image-base URL/info.json` fetches a IIIF Image API base with no Presentation manifest (French departmental archives) at the native width from `info.json`; `{page}` in the URL takes each `--pages` number. `--native` does the same for each canvas of a manifest, past `full/max` caps such as Antenati's 10 megapixels. Both refuse an image whose width is not the one `info.json` gives, so an upscaled or shrunk file is never saved. Re-running a `full/max` download with `--native` in the same folder adds `<name>_native.jpg` beside each capped page.
- `gwt-fetch iiif` turns a Canadiana viewer URL (`heritage.canadiana.ca/view/<id>`) into its manifest. A Canadiana run waits the 10 seconds between requests that its robots.txt asks for. The fetch page lists manifest URL templates for Riksarkivet, ANNO, the Bayerische Staatsbibliothek, the National Diet Library and the Internet Archive.
- `gwt-fetch crop IMG (--box x,y,w,h [--from-size WxH] | --grid 2x3 [--overlap 10%] | --half left|right) [--max N] [--out DIR]` cuts a saved page into JPEG pieces beside it (or in `--out`), each with a `manifest.tsv` row that names the source file, its sha256 and the box. It never adds rows to a `manifest.tsv` another tool wrote. Needs Pillow (extra `crop`); without it the command exits 2.
- `gwt-import-gedcom`, a new tool. `gwt import-gedcom FILE --tree SNAPSHOT --batch NAME --out LIST` turns a GEDCOM 5.5.1 or 7 file into a gwt-apply change list (people, families, events, places as a hierarchy matched against the tree, sources, repositories, citations, notes, external ids), with keys from the file's SHA-256 and each record's id. A person who looks like someone in the tree is held until `--same @I3@=I0042` or `--different "@I3@=why"`, people who may be living are held unless `--allow-living`, and Julian and French Republican dates keep their calendar. `audit FILE` reports encodings (each line read in another encoding than declared), look-alike records, impossible dates, orphans and broken pointers; `split FILE --root ... --out-dir DIR` writes one GEDCOM per root with spouses as stubs. A character an exporter cut across a `CONC` line is put back together, a bare line inside a note is kept as a new line of it, a Windows file labelled ANSEL or UTF-8 labelled UNICODE is read as what it is and reported, a person whose `_UID` is on a tree person is held as that person (new people get their `_UID`), look-alike couples inside the file are kept apart like look-alike people, `_MARNM` becomes a Married Name, and `--decisions FILE` / `--write-decisions FILE` carry hundreds of `--same`/`--different` decisions. People and places are matched through indexes, so large files and trees take seconds; gwt-apply's preflight compares new people the same way.
- `gwt-gate human <lane> <what> --url U` parks every lane until a person solves a captcha, triples the gap like a hit, and runs the `--notify` command template (`{site}`, `{lane}`, `{url}`, `{what}`, no shell) once per parking. `gwt-gate wait-clear --probe CMD` ends the parking when the probe exits 0, reminds once after `--timeout`, then exits 5 still parked. `gwt-gate auth-expired <lane> --account A --session FILE` parks only the lanes that `wait --account A`, leaves the gap alone, and lets them go when the session file changes. `--profile HOST` applies a site's published limits from `gate/profiles.tsv` (per hour, burst and cooldown; each row cites its source). `gwt-gate eta <n>` estimates how long n requests take, and `gwt_gate.challenge()` / `gwt-gate challenge FILE` names the bot wall (Radware perfdrive, Imperva, Cloudflare, hCaptcha, reCAPTCHA) in a saved answer (exit 2 when it cannot read the file). `clear` also releases expired accounts, and a notice that fails makes `human` exit 1. Existing calls behave as before.
- `gwt-run`, a new tool: `gwt run LIST --config FILE` takes one change list through a backup (`gwt-apply --save-snapshot`, read back whole, md5 kept), a dry run on that backup, the apply, a verify and a `gwt-check check --from` gate against the backup and `--known`, and writes `run.json` with every step's status. It reads the steps' JSON, never their printed lines. A conflict, an error, or a dry-run skip that is not a no-op stops it before any write (exit 3) unless the skip is named with `--accept-skip`; an apply that fails on a busy server is tried again up to `--apply-tries` times without writing anything twice; a step with no output and no API call for `--stall` seconds is ended (exit 5, naming the last call). Until the apply writes, running the same command again starts over from a backup that reads only what changed (an edited list is accepted); after that it goes on from the first step that is not done, and refuses a changed list, another server or tree, or a changed backup. `--server-backup` stays on until the server confirms a backup, so nothing is written without one. Ctrl-C or SIGTERM on gwt-run ends the running step too. `gwt-apply --save-snapshot` now prints one progress line per page on stderr, and its snapshots record `read_started`, so `gwt-check --from` on one no longer guesses when the read began.
- `gwt-import-dna`, a new tool: `gwt-import-dna --site myheritage|ftdna|gedmatch|livingdna|ancestry|other --tested I0001 --snapshot DUMP --out changes.jsonl FILE...` turns a site's match list and segment file (CSV) into a change list: per match an `add_person` tagged `dna-match` (unless the match is already in the tree), an `add_association` with relation `DNA`, a Research note whose first line marks the site, the match and its cM, and a segment note in the format Gramps Web's DNA view parses (pinned in the tests to a string the server was seen to accept). A match already in the tree is found by `--id-map`, by an earlier import of the same site, or by another site's import with the same name and a close cM (20 cM and up); contact, e-mail, country and location columns are never read; output splits into files of at most `--batch` ops without splitting a match.
- Four more `gwt-read` sources, open APIs with no account: `riksarkivet` (Sweden's National Archives: scanned church books and other volumes by parish or series, each with its reference code and IIIF manifest for `gwt fetch iiif`), `tna` (The National Archives (UK) Discovery: wills, service records and the catalogues of 2,500 UK archives, with `archive` lines naming which archives hold matching entries, and `record ID`; the gate keeps it to 120 calls an hour, under the API's daily limit), `census1950` (the 1950 US census name index for every state and Puerto Rico: one row per matching schedule line with its enumeration district, line and page scan in NARA's public S3 bucket, marked `machine: true` when only the handwriting-recognition index read it) and `digitalnz` (Papers Past, New Zealand newspapers from 1839, through the DigitalNZ API, with each article's link and the words around the query). Each reads "none" only from the API's own zero count and stops on an answer of any other shape. Pages: [gwt-read sources](https://geiserx.github.io/gramps-web-toolkit/sources/).
- `gwt-lint`, a new tool: `gwt-lint LIST --snapshot DUMP` checks a change list against the tree before it is applied, with rules for scope (blood relatives of `--root` and their spouses, through birth, adoptive or unknown parent links only), protected people, possibly living people (exact dates, places, quotations, marriages, identity numbers), overlap with open lists and updates already applied, the weak tag on weak claims, and text that counts what the tree holds. Every finding has a stable code; it exits 0 clean, 1 on findings and 2 when a rule had nothing to check. `gwt-lint --prove LIST` runs every rule on its own broken copy of the list, under your own settings, and fails unless each could look and fires its own code.
- `gwt-corpus`, a new tool that searches everything already downloaded before an agent goes back to a site. `gwt-corpus update ROOT...` indexes gwt-fscache answers, reader envelopes, text files and PDF pages (with `pdftotext`) into one SQLite FTS5 file, each row with its URL, sha256 and page. `search` folds accents, needs every word near the others (never OR, and a `+` stays a separator), and answers 0 hits with «not downloaded yet»; a raw `--fts` expression needs a `--control` that matches. `sweep` looks for every person of a TSV or dump with control names checked before and after (a lost control voids the run) and can write gwt-ledger rows: a match is `partial` (a lead), a miss is `none` due again at once, so a corpus miss never stops the search on the site. Particles (de, la, van) are never a surname, and two-word names, pairs and surname-and-town matches count only when rare. `xref` crosses a book or gazette with a dump (full name, surname pair, surname and town; dictionary headings «SURNAME, Given» flipped) and writes gwt-rank candidates with page and md5. Standard library only.
- Four new `gwt-read` sources for historical newspapers, searched inside their text: `bvph` (Spain's Biblioteca Virtual de Prensa Histórica), `delpher` (Dutch newspapers 1618-1995, through the KB's open SRU API, with each article's OCR text), `trove` (Australian newspapers, with your own free API key in `$GWT_TROVE_KEY`; without it, exit 4) and `kramerius` (the MZK and NKP Czech digital libraries, public pages only, with each page's image URL and, at the MZK, its IIIF service for `gwt fetch iiif`). A row is a page or an article that mentions the words, role `mention`, never a person, with the words around them in `text`. `bvph` takes only a phrase in double quotes, walks every results page up to `--pages` (6) and prints a `capped` line when it stops there; a 429, the HTTP 200 skeleton the site serves when it soft-blocks, or "No hay resultados" on a later page is exit 3, never zero results. A page that shows hits but whose rows cannot be read is a STOP too, and an issue dated only in its title keeps that date. `delpher` and `trove` put the query in brackets when `--from`/`--to` add a date filter, so a quoted phrase or an `OR` keeps its meaning. Each source has its own gate, cache rules and canary. Pages: [gwt-read sources](https://geiserx.github.io/gramps-web-toolkit/sources/).
- `gwt-lint` evidence rules, read from the records you saved (`--records DIR` or `records` in lint.toml; nothing is fetched): `quote` (every quotation of four words or more is in a field of the cited record or a saved transcription, after NFC only, and shorter spans next to a cited record too; a quotation with only an image behind it needs a signed row in `--readings`), `principal` (every FamilySearch ark in an op, note text and reasons included, is the record's principal, of the right event; the fix is the principal's ark), `role` (the family role the list gives a persona matches the record; two parents of one sex) and `placement` (sacraments on a church the record's typed place names, civil events off churches). An ark with no saved record makes the rule exit 2. With no `--rules`, these run too; a list that cites no record and quotes nothing needs no saved records. See https://geiserx.github.io/gramps-web-toolkit/lint-evidence/
- Five new `gwt-read` sources for open parish and census indexes with no API: `dokuklik` (the Basque archive's index of baptisms, marriages and burials in Araba, Bizkaia and Gipuzkoa), `hiski` (Finnish parish registers, one parish and book at a time), `digitalarkivet` (Norway's transcribed censuses, church books and emigrant lists), `geneteka` (Polish parish and civil registers; one request every 2 minutes, as its robots.txt asks, so never in bulk) and `freeukgen` (FreeREG parish registers and FreeCEN censuses, `--site`). Every row is an index entry, a lead to the record. Each source has its own gate at its site's pace, cache rules and canary; "none" is only the site's own count or words, and any other page is a STOP. `Reader.get(..., data=b'...')` and `get_api(..., data=...)` send a POST, for a site whose search is a form or a JSON body. A page that carries Cloudflare's ordinary beacon script is no longer read as a wall; a real Cloudflare challenge still is. `gwt_reader.rows_check` makes a results page a STOP when it shows a row the reader cannot read, so a markup change never reads as fewer rows or none. A mistake in the search that the site itself answers (a HisKi book the parish does not keep, a FreeREG search with no county, type or years) exits 64 without pausing the gate. Pages: [gwt-read sources](https://geiserx.github.io/gramps-web-toolkit/sources/).

### Changed

- `gwt-rawstore migrate --remove` writes one `.gitignore` pattern per extension (`*.jpg`) in the migrated folder instead of one line per file in every subfolder.
- `gwt-check`: `dup-person-relative` and the shared-relative branch of `dup-person-fuzzy` leave out two namesakes whose Primary Birth dates cannot overlap. Before, a birth that was open (`before 1750`), a range, estimated or calculated was ignored, so a grandfather and a grandson with one name and same-named fathers came out as a duplicate. The dates are read as intervals with the slack `about`, estimated and calculated dates already get; a baptism alone, an unknown birth or an unreadable birth date never sets two people apart. `dup-person-birth` is unchanged: a shared birth or baptism year still reports the pair.

### Fixed

- `.github/scripts/check_wheel.sh` failed on macOS: it compared the venv path from `mktemp` (`/var/...`) with the module paths Python reports (`/private/var/...`). It now compares real paths, imports every source module the registry names, and runs `gwt read <source> --help` for each.
- `gwt-fetch` no longer downloads again a file that was moved to the bucket: a row in the folder's `rawstore.tsv` counts as finished.
- `gwt-fetch iiif` fetches Portale Antenati again. A pasted manifest URL was sent as its own Referer, and Antenati's firewall answers 403 to anything but `Referer: https://antenati.cultura.gov.it/`; every `dam-antenati` and `iiif-antenati` URL now gets the portal as Referer. Other archives get the Referer they got before.

## [0.3.0] - 2026-10-31

### Change lists

None. `--private-new` and `--server-backup` are off unless asked for, and `gwt-leads` output without `--archives` is byte-identical to 0.2.12.

### Added

- `gwt-read insee search --files F [F ...] --surname S [--surname S2 ...]` reads INSEE's French deaths files ("fichier des personnes décédées", data.gouv.fr) offline: no request, no gate, no cache. Each line whose surname matches any `--surname` (whole words, case and accents folded) becomes a `principal` gwt-rank candidate with a birth event (year, commune and country as written) and a death event (year, place code). The full dates, codes and act number stay as fields gwt-rank does not score. Every line is checked: a line whose columns do not line up (a date that is not eight digits, a sex that is not 1 or 2, a line too short, or one longer than two records) exits 2 with the file and line, and nothing is written. A value INSEE wrote that breaks its own rules (month 13, day 60, a name with no `*`) is kept as written and marked `"malformed"`, and each file's count is in the envelope and on stderr. A file that cannot be read or holds no line exits 1, so it never reads as "no results". Columns count characters. A line that is not valid UTF-8 is read as CP850 or Latin-1, whichever gives more accented letters (CP850 on a tie), since the years differ, and marked `"encoding": "cp850"` or `"latin-1"`.
- `gwt-apply --server-backup` asks the Gramps Web server to store a backup of the tree (`POST /api/backups/`) after the tree lock is taken and the plan has passed its checks, and before the first write. If the server does not store a restorable backup, nothing is written and the run exits 1. It only creates a backup: `gwt-apply` never restores one. It needs `--apply` and an owner or admin login. The server may answer 202 with a task; the run waits for that task, checks that the new backup is listed and restorable, and never repeats the POST. A refusal names the server's reason (403 role, 429 daily backup limit, 501 no backup directory). `--revert` does not count the backup request as a write.
- `gwt-apply --private-new` saves every object the run creates as private, so a guest login never sees a new person, family, event, place, source, citation, media object or note. Objects that already exist keep their own value. Off by default; nothing about existing change lists changes.
- `gwt-leads --archives FILE` tells each gap which archive keeps its record and whether its year is online there. The file is a TSV with `match`, `archive`, `series`, `years` and `url` columns, plus an optional `access` (`online`, `browser`, `letter`, `unknown`; empty reads `unknown`). A row matches any level of the gap's event place, whole names folded for case and accents; the deepest level wins, then the first row. `url` takes `{place}` and `{year}` only. A matched gap gets a `where` object in `--format json` and a `where:` part in the `--format queue` note; `online` is unknown for a "before" or "after" year. The file is read as plain UTF-8 tab-separated text, so a quote is an ordinary character, and a gap looks up each level of its place, so a commune-sized file stays fast. A bad file (not UTF-8, a repeated column among others) exits 2. Without the flag the output is unchanged. `examples/archives.tsv` is an invented file.
- `gwt-queue list --result R` prints only the rows closed with that result, so the searches that came back `blocked` or `denied` can be listed on their own.

### Fixed

- `gwt-rawstore put` names the file that failed. A FAIL line now gives the path and the error type (a bare `TimeoutError()` used to print an empty line), and a last line on stderr and stdout lists every file not stored, so a log of stdout alone shows it. A transient error (network, 5xx, 429, a body that changed while it was read, an object the store did not confirm) is retried, three attempts in all with the file hashed again each time; a 403 or a missing file is not.

## [0.2.12] - 2026-10-30

### Change lists

None. The summary line keeps its form; a run with skips prints one more line after it.

### Added

- `gwt-apply` `update_source` corrects a source's title. Like `update_citation`'s page, the new title needs `expect`, the title the source has now, so a source renamed since it was read is a conflict and is never overwritten; a title another source with the same author and publication info already has is a conflict too.
- `gwt-apply` tells a no-op skip (an `update_*`, `add_tag`, `remove_tag` or `set_attribute` that found the object already as it asks) from a rerun skip: a `NOOP: <n> of <m> skip(s) ...` line after the summary, and `"noop": true` with the skip line as `reason` in `--json`. A chain that wants a first dry run with no skips can accept these and still stop on a list that already ran.

## [0.2.11] - 2026-10-29

### Change lists

None.

### Added

- `gwt-read boe search`: a name in the text of the Gaceta de Madrid and the BOE (https://www.boe.es/buscar/gazeta.php, text search 1836-1959), over plain HTTP. Each hit becomes a candidate of role `mention`, never a principal, so `gwt-rank` keeps it at `weak`. The phrase is sent in quotes; own gate (one request every 6 s, never faster), 7-day cache for searches and 90-day for PDFs (`BOE_RULES`); a page that is not a results page (search form, captcha) is a STOP. `--from`/`--to` narrow the dates, `--pages` reads up to 5 pages, `--near` marks rows without hiding any. `--born` marks an act published before the birth or over 100 years after it; `--pdf-context N` reads the PDF of the first N plausible hits with `pdftotext`, from the `/gazeta/dias/` path robots.txt allows, adds the words around the whole name and, with `--parents`, compares the parents the act writes with the tree's. Every act gets a verdict (`excluded`, `consistent`, `open`).

## [0.2.10] - 2026-10-29

### Change lists

None.

### Added

- `gwt-read genae search`: name search on Genae (https://genae.org), the volunteer index of parish baptisms, marriages and burials of the Region of Murcia and nearby villages of Albacete, Alicante, Almería and Granada. It reads the baptism, groom, bride and burial blocks over plain HTTP and writes one candidates file `gwt-rank` ranks. Own gate (one request every 20 s at best), 7-day cache (`GENAE_RULES`), phonetic rows marked, a block cut at the site's page size flagged, and a page that is not the block asked for (empty frame, search form, captcha) is a STOP; a block the site says has no indexed books is zero rows.

## [0.2.9] - 2026-10-29

### Change lists

None.

### Added

- `gwt-check public OWNER GUEST`: compares a dump read with an owner login and one read with a guest login, and reports what a guest still sees: a private object, a visible object that points at a private one, a private person's name in a visible text, a possibly living person with a recent dated birth or baptism. IDs and kinds only. Exits 1 when the server shows a guest something private; possibly living people are listed but do not set the exit code. Read-only.

## [0.2.8] - 2026-10-29

### Change lists

None.

### Added

- `gwt-apply` `remove_tag` takes one tag off an object the tree already has, for example a weak mark once a document proves the fact. The tag stays in the tree; a name the tree has no tag for is a conflict.
- `gwt-apply` `move_event` moves an event reference, with its citations and notes, from one person or family to another; a move between a person and a family swaps the roles `Primary` and `Family`, and `role` sets another one. It writes the new owner first, so a stop in between leaves the event on both and a rerun finishes the move, and it keeps each person's birth and death indexes on the right events.

### Fixed

- `gwt-check check` `place-vs-description` no longer counts a place name that is a given name or surname of someone on the event, or that follows words saying where someone is from or lives («natural de», «vecino de», «domicilié à», «de la commune de», a clause opening with «de» after a comma or quote mark, not after an abbreviation dot such as «parr.»). On a residence those words still count, because there they name the event's place. On one real tree this halved the findings; none of the dropped events that were read had happened elsewhere. «Parroquia de Otropueblo», «en la casa de Otropueblo» and «murió en su domicilio de Otropueblo» still count.

## [0.2.7] - 2026-10-28

### Change lists

None.

### Added

- `gwt-apply` `add_tag` puts a tag on an object the tree already has, for example a research mark such as `Hypothesis` on a person, an event or a note.
- `gwt-check check` reports `note-marker-no-tag`: a note that says «hipótesis» or «homónimo» without the tag of that name, in a tree that has such a tag. For information only: it never sets the exit code. A mention with «descartada», «refutada» or a like word next to it in the same sentence is not reported, unless a «no» comes between them. A tree with no such tag is never blamed.
- `gwt-check check` reports `place-vs-description`: an event whose description names another place of the tree and not its own place or one enclosing it. Names are compared without accents, as whole words, and names under four letters are skipped. It is for information only (it never sets the exit code) and reports only events in scope with `--since` or `--tag`.

## [0.2.6] - 2026-10-28

### Change lists

None.

### Fixed

- `gwt-check check`: a finding key that lists several IDs (`dup-media`, `dup-citation`, `own-ancestor` and the others sorted by ID) no longer depends on the order the dump lists the objects in when two IDs have the same number (`O01` and `O001`). The ID itself breaks the tie, so `--known` and `--baseline` keep matching. `gwt-leads` orders such IDs the same way.

## [0.2.5] - 2026-10-28

### Change lists

None.

### Added

- `gwt-apply` `update_place` takes `name` in `set`: it renames a place and keeps the date and language of its name. A sibling of the same type that already has the name is a conflict, a rerun is a skip, and `--verify` reports a place renamed back as differing.

## [0.2.4] - 2026-10-28

### Change lists

None.

### Added

- `gwt-apply` `update_place` takes `enclosed_by` in `set`: it moves a place under another parent, refusing a parent that does not exist or one inside the place. A rerun is a skip, and `--verify` reports a place moved back as differing.
- `gwt-check repos --classify` sorts the sources with no repository by what they name (a repository the tree has, another institution, nothing), with the institutions grouped so one new repository can serve many sources. It only reports.
- `gwt-check check` reports `dup-media`: two media objects with the same checksum, the same file entered twice.

## [0.2.3] - 2026-10-26

### Change lists

None.

### Changed

- `gwt-check dump` and `check` read the first page of every type together, then the remaining pages, instead of one type after another; the dump holds the same objects. `check --from` does not page the tree, so it gains nothing. The per-type progress lines now appear together once the read ends. On a 260-person tree the read went from 20 s to 14 s.
- `gwt-leads` says how many of the people it held back are marked private and how many may be living, instead of one count for both.

### Fixed

- `gwt-leads --links` no longer stops with exit 2 on a links report that has a link that is itself cited (`the link itself is cited`), the first level `gwt-check links` writes.

## [0.2.2] - 2026-10-26

### Change lists

None.

### Changed

- `gwt-apply --snapshot` and `--since` accept a `gwt-check` dump as well as a snapshot, so `gwt-apply LIST --verify --snapshot POST` works on the `--save-dump` of `gwt-check check --from PRE`. Before, a dump was refused with "is not a gwt-apply snapshot".

### Fixed

- apply: a timeout or busy answer in the whole-tree list reads before the first op is retried like an op's read (`--retries`, `--retry-wait`): the duplicate check's, and on an `--apply --snapshot` the check of what changed since the snapshot. Before, one slow read ended the run with exit 1 before any op ran.
- apply: a timeout on the run's first login is retried the same way; only a login inside an op was retried before.
- apply: `--retries` below 1 is refused with exit 2. With 0, a run took no lock, ran no op and reported every op done.

## [0.2.1] - 2026-10-26

### Change lists

None.

### Added

- `gwt-check links`: a read-only report of which parent links have a source, on the link itself or in a record whose text names both parents. It never sets the exit code.
- `gwt-check cites`: events whose citation's page names another kind of act (a marriage act under a birth) or a marginal mention in another act; a report that never fails.
- `gwt-check act`: sets one transcribed act beside a dump and says, field by field, whether the tree's event agrees on date, place, parents and citation reference; `--person` names the subject. A report that always exits 0.
- `gwt-leads --links FILE`: an opt-in `unsupported-link` kind that turns birth links no cited record supports, read from `gwt-check links --json`, into verify tasks. A report from another tree or an older dump stops with exit 2.

### Changed

- `gwt-check fill-places` no longer proposes a place that only a citation of another kind of act or a marginal mention names; the event appears under `held_back` instead, so an `--ops` file has fewer ops than before.

## [0.2.0] - 2026-10-26

The first release: everything since the first public commit.

### Change lists

Lists written for the first public commit of `gwt-apply` behave differently in these ways:

- `allow_namesake` needs `namesake_reason` and `namesake_of`; a namesake without them is refused.
- Two ops in one list that make the same change are refused, and a pre-flight read reports every likely duplicate person before the first write.
- `add_transcription` is refused without `margin_reading` (what the margins say, or `none` once looked).
- `--apply` holds a lock in the tree; a second `--apply` on the same tree stops before writing and names the holder.
- A list planned on a snapshot is refused when the tree moved since the snapshot.
- Every object a run creates carries the run's tag. An empty run tag is refused, and a tag the server reports as created but does not keep stops the op.
- A Gramps ID with a suffix (`I0042-b`) is accepted in every op; a bare handle is still refused.
- A person with one name and no surname is accepted.
- `namesake_of` by `key:` resolves people an earlier list created.
- A value of the wrong kind, a citation `confidence` outside 0-4, or a full date that is not a real calendar day (`1890-02-31`) is refused before the first write.
- A write whose answer was lost (timeout, dropped connection, 5xx) but which landed reports `done` instead of `skip`.
- `gwt-import-excel` stops with exit 2 on an unknown mapping field instead of dropping that column.
- An `add_family` with both parents whose couple looks like a tree family's couple, or another `add_family` of the list, is flagged before the first write even when each parent carries `allow_namesake`; `allow_new_couple` with `new_couple_reason` and `new_couple_of` clears it.
- New op `unlink_child` takes a child out of a family; a list that used it was refused as an unknown op before. `--revert` now makes a note the run deleted again instead of sending it to the backup.

### Added

- `gwt`, one command for every Python tool (`gwt <tool> ...`), installed with one `pip install gramps-web-toolkit`; each `gwt-<tool>` command stays.
- `gwt-apply --verify` proves a list landed: a dry run that exits 1 when any op would write, conflicts or errs, and names each missing object and each field that differs; a Gramps ID merged away is read on its winner. `--json FILE` writes one result per op. A dry-run op retried after a server error no longer reads its own first try's plan as already in the tree.
- `gwt-apply`: offline planning on a snapshot (`--save-snapshot`, `--since`), merges of people, families and events through Gramps' own merge, DNA matches as associations, `attach_existing`, `dedupe_list`, `update_place`, repositories, `update_note`, `update_citation`, `update_family`, `update_child_ref`, `detach`, `unlink_child`, and `--revert` to undo one run from its log.
- `gwt-check`: read-only whole-tree checks, `--baseline`, `--ignore-check`, accepted findings with a reason, disconnected family components, contradictory dates, living-people privacy checks, missing event places found in their citations, citations entered twice, and offline checks of saved snapshots.
- `gwt-ledger` (shared search ledger, one file per lane), `gwt-gate` (one request pace per machine), `gwt-queue` (search tasks for parallel lanes), `gwt-leads` (tree gaps as queue tasks), `gwt-rank` (orders FamilySearch results), `gwt-import-excel` (workbooks to change lists), `gwt-rawstore` (originals in S3) and `gwt-mh` (MyHeritage through a signed-in browser).
- Both Gramps Web clients send a toolkit User-Agent and log in with a sync token (`GWT_TOKEN`).
- A documentation site at https://geiserx.github.io/gramps-web-toolkit/.
- Tagged releases publish to PyPI, and `gwt --version` in a clone prints a version read from git.
- `gwt-read familysearch`: FamilySearch record search and records with your own session token, through the cache and a slow per-account gate (one request every 30 s at best), stopping on a 403, 429, captcha, sign-in page or empty frame; it writes the same envelope and candidates files as `gwt-mh`, whose shared parts now live in `read/gwt_reader.py`.
- `gwt-read pares`: search and descriptions from PARES, the Spanish state archives' catalogue, on the same reader shape, cache (`PARES_RULES`: searches 7 days, descriptions 90 days) and STOP rules, with its own gate (one request every 15 s at best); a page without the record text it asked for (an empty frame, the site's error page) is a STOP.

### Changed

- `gwt-fetch` searches every Europeana provider by default.
- `poller` is now `spanish-poller`.
- `gwt-apply` runs about 30% faster, writes a person's associations once, and checks an apply on an updated snapshot instead of the server.
- `gwt-check` after a write refreshes a saved tree file instead of reading the whole tree again.
- `gwt-apply` logs a real run under `$XDG_STATE_HOME/gwt-apply/<tree>/` instead of the working directory, and a dry run or `--save-snapshot` keeps no log unless `--log` is given. Each log opens with a `start` line (arguments, change list hash, tree) and closes with an `end` line (counts, exit code), records refusals, marks dry-run writes `planned`, and names the Gramps ID of each object created.

### Fixed

- `gwt-apply`: a missing or unreadable change list is one line and exit 2 instead of a traceback, and a run whose login fails still prints its run id.
- `gwt-check`: timeouts are retried, a pedigree-collapsed Ahnentafel walk is bounded and a truncated walk fails, info findings no longer hold a read open, writes during a check no longer abort it, and namesakes with differently named parents are no longer fuzzy duplicates.
- `gwt-ledger`: a shared catalogue prefix no longer hides a different search, an unreadable ledger exits 2, and non-Latin and blocked queries are found again.
- `gwt-gate`: a dead session or a permission refusal is no longer reported as a rate limit.
- `gwt-leads`, `gwt-rank`, `gwt-import-excel` and `gwt-queue`: fixes to living-person detection, surname-first rows and lease handling.
