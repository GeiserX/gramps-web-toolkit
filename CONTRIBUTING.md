# Contributing to gramps-web-toolkit

Thanks for helping. This page says where new work goes, what its tests must prove, and how a pull request should look. The commands to run the tests and build the docs are in the development guide: https://github.com/GeiserX/gramps-web-toolkit/blob/main/docs/development.md

## Where a new capability goes

Most new work fits an existing tool. Put it where the table says, and open an issue first if it fits nowhere.

| You want to add | It goes in |
|---|---|
| A finding that should fail a gate | A new check kind in `gwt-check`, with an error planted for it in `gwt-check selftest` |
| A report that never fails anything | A `gwt-check` subcommand, documented as never setting the exit code |
| A new source of research tasks | A new kind in `gwt-leads`, or a new input to its priority score |
| Search history (what was searched, where, with what result) | Calls to the `gwt-ledger` functions (`read_rows`, `find`, `add_row`), not a new file format |
| A change to the tree | A new `gwt-apply` op. Never a CSV or a sheet for a person to fill in and type back |
| A rule several tools share (who counts as living, which events are vital, date reading, name folding) | The shared core (coming). Until it lands, say in the PR which copy you followed |
| Words in a particular language (month names, record terms) | One locale table (coming). No language regex inside a tool |
| A new top-level tool | Open an issue first, so we agree it is not a mode of an existing tool |

## Tests must be able to fail

A test that passes whether or not the code works proves nothing. So:

- Every rule a doc states with *never*, *only* or *unless* has a test for it.
- In the PR, show that test failing with its guard removed: delete or invert the line that enforces the rule, run the test, paste the red output, then put the line back.
- Once the repository has `mutants.txt` files, add a row there instead, and CI will remove the guard for you.
- A new check kind gets an error planted in the self-test; the suite already fails when a check has none.

## Pull requests

- One concern per pull request. A fix and a refactor are two pull requests.
- No stacked pull requests. Each one starts from `main` and stands alone, so it can be reviewed and merged in any order.
- Commits and titles follow Conventional Commits. The title says why the change matters, not a list of files.
- Behaviour that changes updates the tool's page under `docs/` in the same pull request.
- Every change reaches `main` with green CI. Do not force push a branch someone is reviewing.

Pull requests written with an AI agent are welcome. Say so in the description, and have a person read the whole diff and the description before posting. Do not add AI attribution trailers or "Generated with" lines to commits.

## Privacy

Family trees hold real people, many of them alive. The rules:

- Examples, fixtures and test data are invented. No real people's names from anyone's tree, no real case numbers, tree ids, hostnames, IP addresses, accounts or email addresses.
- Public records, such as a published gazette page or parish entry, are fine with their source.
- Never commit a secret, a certificate or a configuration file with credentials.
- Logs and screenshots in issues follow the same rules: replace names and ids before posting.

Security problems go through the security policy, never a public issue: https://github.com/GeiserX/gramps-web-toolkit/blob/main/SECURITY.md

The toolkit is licensed GPL-3.0-or-later. By contributing you agree your work is released under that licence.
