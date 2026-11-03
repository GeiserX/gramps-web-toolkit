"""Keep every agent skill under skills/ in step with the tools and the docs. Run: cd skills && python3 -m pytest -q

A skill is skills/<name>/SKILL.md in the Agent Skills format (https://agentskills.io/specification). Agents trust
what it says, so a renamed flag or a moved docs heading must fail here, not in an agent's run:

- the frontmatter has a valid `name` equal to the folder name and a `description` of at most 1024 characters, and
  each value is one a YAML loader reads as the same plain string;
- every `gwt <tool>` or `gwt-<tool>` it names is a real tool, and a word right after the tool inside a code span is
  one of that tool's subcommands when the tool has any; in prose, a word right after it that is close to a
  subcommand's name must be that subcommand;
- every `--flag` belongs to the tool named last before it, and is in that tool's --help, or, after a subcommand,
  in that subcommand's --help only (argparse rejects a tool-level option after the subcommand; in a code span a
  tool-level option may come before the subcommand);
- every link to the docs site names a page under docs/ and, with `#anchor`, a heading on that page.
"""
import contextlib
import difflib
import io
import os
import re
import sys
import unicodedata
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
SITE = "https://geiserx.github.io/gramps-web-toolkit/"
for sub in ("core", "apply", "check", "fetch", "ledger", "gate", "fscache", "queue", "leads", "rank", "importxl",
            "rawstore", "myheritage", "read", "report", "geocode", "gedcom", "dna", "lint", "corpus", "run"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from gwt_core import cli  # noqa: E402

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
TOOL = re.compile(r"\bgwt[ -]([a-z][a-z0-9-]*[a-z0-9])")
FLAG = re.compile(r"(?<![\w-])--([A-Za-z0-9_][A-Za-z0-9_-]*)")
URL = re.compile(re.escape(SITE) + r"([A-Za-z0-9_./-]*)(?:#([A-Za-z0-9_-]+))?")
CODE = re.compile(r"```.*?```|`[^`\n]+`", re.S)


def skills():
    return sorted(d for d in os.listdir(HERE) if os.path.isfile(os.path.join(HERE, d, "SKILL.md")))


def read_skill(name):
    with open(os.path.join(HERE, name, "SKILL.md"), encoding="utf-8") as f:
        return f.read()


def yaml_problem(value):
    """Why a one-line frontmatter value would not load in YAML as the plain string it reads as, or None. Agent loaders
    parse the block with a real YAML parser; CI has none, so the rules for a plain (unquoted) scalar are checked here."""
    if not value:
        return None
    if value[0] in "\"'":
        return None if len(value) > 1 and value[-1] == value[0] else "a quoted value must end with its quote"
    if value[0] in "[]{}>|*&!%@`#,?:-" and not (value[0] in "?:-" and len(value) > 1 and value[1] != " "):
        return f"a plain value may not start with {value[0]!r}; quote it"
    if ": " in value or value.endswith(":"):
        return "': ' inside a plain value starts a mapping; quote the value"
    if " #" in value:
        return "' #' inside a plain value starts a comment; quote the value"
    return None


def frontmatter(text):
    """(fields, body) of a SKILL.md; fields is None when the file does not open with a --- block."""
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        return None, text
    fields = {}
    for line in m.group(1).splitlines():
        if line.strip() and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    return fields, m.group(2)


_help = {}


def tool_help(tool, sub=None):
    """The --help text of `gwt <tool> [<sub>]`, as the tool's own parser prints it."""
    argv = ([sub] if sub else []) + ["--help"]
    if (tool, sub) not in _help:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.run(tool, argv)
            except SystemExit:
                pass
        _help[(tool, sub)] = out.getvalue()
    return _help[(tool, sub)]


def subcommands(tool):
    m = re.search(r"^usage:.*?\{([a-z0-9,-]+)\}", tool_help(tool), re.S | re.M)
    return set(m.group(1).split(",")) if m else set()


def flags(help_text):
    return set(FLAG.findall(help_text))


def commands(text):
    """Problems, and the (tool, sub, flag) triples the text names, in order."""
    problems, named = [], []
    code = [(m.start(), m.end()) for m in CODE.finditer(text)]
    events = sorted([(m.start(), "tool", m) for m in TOOL.finditer(text)] +
                    [(m.start(), "flag", m) for m in FLAG.finditer(text)], key=lambda e: e[0])
    current, after = None, None  # after: (tool end, code span end) while a subcommand may still follow tool flags
    for pos, kind, m in events:
        line = text.count("\n", 0, pos) + 1
        if kind == "tool":
            tool = m.group(1)
            if tool not in cli.TOOLS:
                problems.append(f"line {line}: `gwt {tool}` is not a tool (known: {', '.join(cli.TOOLS)})")
                current = None
                continue
            sub = None
            span = next(((a, b) for a, b in code if a <= pos < b), None)
            nxt = re.match(r"[ \t]+([A-Za-z][A-Za-z0-9-]*)", text[m.end():span[1] if span else len(text)])
            subs = subcommands(tool)
            if nxt and subs:
                word = nxt.group(1)
                if word in subs:
                    sub = word
                # in a code span the word after the tool is the subcommand; in prose it is only when it looks like one
                elif span or difflib.get_close_matches(word, subs, n=1, cutoff=0.75):
                    problems.append(f"line {line}: `gwt {tool} {word}`: no such subcommand "
                                    f"(known: {', '.join(sorted(subs))})")
            current = (tool, sub)
            after = (m.end(), span[1]) if span and subs and sub is None else None
            named.append((tool, sub, None))
        else:
            flag = m.group(1)
            if current is None:
                problems.append(f"line {line}: --{flag} follows no gwt tool, so nothing proves it exists")
                continue
            tool, sub = current
            if after and pos < after[1]:
                # `gwt ledger --ledger L check --exact`: a tool flag first, then the subcommand and its flags
                word = next((w for w in text[after[0]:pos].split() if w in subcommands(tool)), None)
                if word:
                    sub, current, after = word, (tool, word), None
                    named.append((tool, sub, None))
            known = flags(tool_help(tool, sub)) if sub else flags(tool_help(tool))
            if flag not in known:
                where = f"gwt {tool}" + (f" {sub}" if sub else "")
                problems.append(f"line {line}: --{flag} is not an option of `{where}` (see `{where} --help`)")
            named.append((tool, sub, flag))
    return problems, named


def slug(heading):
    """The anchor Python-Markdown's toc extension gives a heading (the docs site's default slugify)."""
    m = re.search(r"\{\s*#([\w-]+)[^}]*\}\s*$", heading)
    if m:
        return m.group(1)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def anchors(path):
    found, fence = set(), None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*(```+|~~~+)", line)
            if m:
                fence = None if fence and m.group(1).startswith(fence) else (fence or m.group(1))
                continue
            if not fence:
                h = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
                if h:
                    found.add(slug(h.group(1)))
    return found


def doc_page(path):
    """docs/ file for a site path such as `apply/` or `sources/openarch/`, or None."""
    path = path.strip("/")
    for cand in ([path + ".md", os.path.join(path, "index.md")] if path else ["index.md"]):
        if os.path.isfile(os.path.join(DOCS, cand)):
            return os.path.join(DOCS, cand)
    return None


def links(text):
    problems, seen = [], []
    for m in URL.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        page = doc_page(m.group(1))
        seen.append(m.group(0))
        if page is None:
            problems.append(f"line {line}: {m.group(0)}: no docs/ page for {m.group(1) or '(home)'}")
        elif m.group(2) and m.group(2) not in anchors(page):
            problems.append(f"line {line}: {m.group(0)}: {os.path.relpath(page, ROOT)} has no heading #{m.group(2)}")
    return problems, seen


class Skills(unittest.TestCase):
    def test_there_is_a_skill(self):
        self.assertIn("gramps-web-toolkit", skills())

    def test_frontmatter(self):
        for name in skills():
            with self.subTest(skill=name):
                fields, body = frontmatter(read_skill(name))
                self.assertIsNotNone(fields, "SKILL.md must open with a --- frontmatter block")
                self.assertLessEqual(set(fields), FIELDS, "fields the spec does not define")
                self.assertEqual(fields.get("name"), name, "name must equal the folder name")
                self.assertRegex(name, NAME)
                self.assertLessEqual(len(name), 64)
                for k, v in fields.items():
                    self.assertIsNone(yaml_problem(v), f"frontmatter {k}: {v[:60]!r}")
                desc = fields.get("description", "")
                self.assertTrue(desc, "description is required")
                self.assertLessEqual(len(desc), 1024)
                self.assertLess(len(body.splitlines()), 500, "the spec keeps a SKILL.md body under 500 lines")

    def test_tools_and_flags_exist(self):
        for name in skills():
            with self.subTest(skill=name):
                problems, named = commands(read_skill(name))
                self.assertEqual(problems, [])
                # the scan itself must see something, or a broken pattern would pass every skill
                self.assertTrue(any(f for _, _, f in named), "no --flag found: the scan is broken")

    def test_docs_links_exist(self):
        for name in skills():
            with self.subTest(skill=name):
                problems, seen = links(read_skill(name))
                self.assertEqual(problems, [])
                self.assertTrue(any("#" in u for u in seen), "no docs link with an anchor: the scan is broken")

    def test_install_command_names_the_skill(self):
        # the repo also tracks other skills (.agents/skills/); `npx skills add` without --skill installs them all
        found = 0
        for rel in ("README.md", "CHANGELOG.md", os.path.join("docs", "getting-started.md")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    for cmd in re.findall(r"npx skills add[^`\n]*", line):
                        found += 1
                        self.assertIn("--skill gramps-web-toolkit", cmd, f"{rel}:{n}")
        self.assertTrue(found, "no `npx skills add` line found: the scan is broken")


class TheCheckerCanFail(unittest.TestCase):
    """Each check goes red on a planted break, so a green run means the skill was read."""

    def test_unknown_flag(self):
        problems, _ = commands("`gwt apply changes.jsonl --verify --no-such-flag`")
        self.assertEqual(len(problems), 1)
        self.assertIn("--no-such-flag", problems[0])

    def test_flag_of_another_tool(self):
        self.assertTrue(commands("`gwt lint changes.jsonl --break-lock`")[0])

    def test_subcommand_flag(self):
        self.assertEqual(commands("`gwt ledger check --exact --query Q`")[0], [])
        self.assertTrue(commands("`gwt ledger add --exact`")[0])
        self.assertTrue(commands("`gwt ledger chek --exact`")[0])

    def test_tool_flag_after_subcommand(self):
        # argparse: `gwt-ledger check --ledger L` is "unrecognized arguments"; --ledger goes before the subcommand
        self.assertIn("ledger", flags(tool_help("ledger")))
        self.assertTrue(commands("`gwt ledger check --ledger L --exact`")[0])

    def test_tool_flag_before_subcommand(self):
        self.assertEqual(commands("`gwt ledger --ledger L check --exact`")[0], [])
        self.assertTrue(commands("`gwt ledger --ledger L check --no-such-flag`")[0])
        self.assertTrue(commands("`gwt ledger --exact check`")[0])

    def test_flag_spelled_past_the_pattern(self):
        # `--verify_all` must not read as --verify, and `--Verify` must be seen at all
        self.assertTrue(commands("`gwt apply changes.jsonl --verify_all`")[0])
        self.assertTrue(commands("`gwt apply changes.jsonl --Verify`")[0])

    def test_subcommand_typo_in_prose(self):
        self.assertTrue(commands("log it (gwt ledger chek, a person)")[0])
        self.assertEqual(commands("with gwt ledger check, or gwt gate or gwt corpus.")[0], [])

    def test_unknown_tool(self):
        self.assertTrue(commands("`gwt aply changes.jsonl`")[0])
        self.assertTrue(commands("use gwt-aply first")[0])

    def test_flag_with_no_tool(self):
        self.assertTrue(commands("pass `--apply` to write")[0])

    def test_bad_links(self):
        self.assertEqual(links(SITE + "apply/#undo-a-run")[0], [])
        self.assertTrue(links(SITE + "apply/#no-such-heading")[0])
        self.assertTrue(links(SITE + "no-such-page/")[0])

    def test_anchor_inside_code_is_not_a_heading(self):
        # docs/ledger.md has "# Before a search..." inside a sh block; mkdocs gives it no anchor
        self.assertNotIn("before-a-search", {a[:15] for a in anchors(os.path.join(DOCS, "ledger.md"))})

    def test_frontmatter_value_yaml_would_misread(self):
        for bad in ("Use when: change things", "x # y", "'unclosed", "[a list]", "> folded", "- item"):
            self.assertIsNotNone(yaml_problem(bad), bad)
        for good in ("Change a tree, safely.", "'quoted: ok'", "\"quoted # ok\"", "-1 is fine", "key:value"):
            self.assertIsNone(yaml_problem(good), good)

    def test_bad_frontmatter(self):
        self.assertIsNone(frontmatter("# no frontmatter\n")[0])
        fields, _ = frontmatter("---\nname: other-name\ndescription: x\n---\nbody\n")
        self.assertNotEqual(fields["name"], "gramps-web-toolkit")


if __name__ == "__main__":
    unittest.main()
