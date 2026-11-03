"""gwt-lint: one preflight for a gwt-apply change list, run as rules that each prove they can fail.

    gwt-lint LIST --snapshot DUMP [--config lint.toml] [--rules a,b] [--json]
    gwt-lint --prove LIST --snapshot DUMP

Every finding has a stable code. Exit 0: clean; 1: findings; 2: a rule had nothing to check (2 wins over 1).
A rule is a class in a module listed in framework.RULE_MODULES; see docs/lint.md.
"""
