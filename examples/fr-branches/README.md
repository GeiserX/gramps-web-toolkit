# fr-branches (example)

Read-only helper. From a `gwt-check dump` and `gwt-leads --format json` output, it ranks the gaps of the people with a place in France and says which record to look for and in which departmental archive.

    gwt-check dump owner.json
    gwt-leads owner.json --include-living --format json --kinds branch-end,missing-vital > leads.jsonl
    GWT_FR_PRIORITY="surname1,surname2" python3 gwt_fr_branches.py owner.json leads.jsonl out.csv out.md [ledger.tsv]

It never calls a site and never writes to the tree. The dump and the output hold private people: do not publish them.
Archive commune codes for AD Indre-et-Loire come from the portal's own search (30/10/2026). Orne, Sarthe and Paris portals are not verified.
