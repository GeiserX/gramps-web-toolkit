# gwt-prove

Offline, read-only checks over a `gwt-check dump` file: dated contradictions and the strength of each parent link.

    PYTHONPATH=check python3 prove/gwt_prove.py DUMP.json.gz [--links] [--json] [--include-living]

Exit codes: 0 no findings, 1 findings, 2 unreadable input. It never talks to Gramps and never writes.

Dates are read as intervals. A finding needs the intervals to be disjoint, and is marked `soft` when any date is
approximate, a range or free text. People who may be alive are held back unless `--include-living` is given.
Link kinds (direct, unverified, derived, hypothesis, unrecorded) come from the evidence notes and marks in the tree,
never from names. Tests: `python3 -m pytest prove/tests`.
