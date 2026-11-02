"""Read a GEDCOM file, 5.5.1 or 7, with the standard library only.

    ged = read("family.ged")
    ged.individuals, ged.families, ged.sources, ged.by_xref["@I1@"]
    ged.repairs      lines decoded in another encoding than the file declares, one row each
    ged.problems     lines the reader could not place, with their line numbers

Encodings: a byte-order mark wins; else the header's CHAR (UTF-8, UNICODE, ANSEL, ANSI, ASCII, IBMPC, LATIN1);
GEDCOM 7 is always UTF-8. Files exported by some sites mix encodings, so each line is decoded on its own: a line
that is not valid in the declared encoding is read as UTF-8 when it is valid UTF-8, else as Windows-1252, and a line
of an ANSEL file whose accents cannot be ANSEL (a mark before a space, a `/` or the end of the line) is read as
Windows-1252. Each such line is reported, never silently changed. A file that says UNICODE but has no NUL bytes is
read as UTF-8, and UTF-16 with no byte-order mark is found by its NUL bytes; both are reported.

A character that an exporter cut in two across a CONC line (the first bytes of `ó` at the end of one line, the rest
at the start of the CONC) is put back together before the lines are decoded.

CONC and CONT lines are joined into the value they continue. A line that is not a GEDCOM line, right after a text
value (NOTE, TEXT, PAGE...), is a line break the exporter did not write as CONT: it is kept as a new line of that
value and listed in `ged.joined`. `@@` stands for one `@`.

    ged_date("@#DJULIAN@ 11 FEB 1700")   -> ("julian 1700-02-11", None)
    ged_date("ABT 1850")                 -> ("abt 1850", None)
    ged_date("Easter 1850")              -> ("text: Easter 1850", "not a GEDCOM date")

gives a date in gwt-apply's grammar and a problem, or None. A date the reader cannot turn into a sortable date is
kept as text with the problem said, never dropped and never guessed.
"""

from __future__ import annotations

import codecs
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from gwt_apply.dates import DateError, parse_date

_LINE = re.compile(r"^\s*(\d{1,2})\s+(?:(@[^@\s][^@]*@)\s+)?([A-Za-z0-9_]+)(?:\s(.*))?$")

# ANSEL (ANSI/NISO Z39.47), as GEDCOM 5.5.1 uses it: spacing characters, then combining marks, which ANSEL puts
# before the letter they mark and Unicode after it.
ANSEL_SPACING = {
    0xA1: "Ł", 0xA2: "Ø", 0xA3: "Đ", 0xA4: "Þ", 0xA5: "Æ", 0xA6: "Œ", 0xA7: "ʹ", 0xA8: "·", 0xA9: "♭", 0xAA: "®",
    0xAB: "±", 0xAC: "Ơ", 0xAD: "Ư", 0xAE: "ʼ", 0xB0: "ʻ", 0xB1: "ł", 0xB2: "ø", 0xB3: "đ", 0xB4: "þ", 0xB5: "æ",
    0xB6: "œ", 0xB7: "ʺ", 0xB8: "ı", 0xB9: "£", 0xBA: "ð", 0xBC: "ơ", 0xBD: "ư", 0xBE: "□", 0xBF: "■", 0xC0: "°",
    0xC1: "ℓ", 0xC2: "℗", 0xC3: "©", 0xC4: "♯", 0xC5: "¿", 0xC6: "¡", 0xC7: "ß", 0xC8: "€", 0xCD: "e", 0xCE: "o",
    0xCF: "ß",
}
ANSEL_COMBINING = {
    0xE0: "̉", 0xE1: "̀", 0xE2: "́", 0xE3: "̂", 0xE4: "̃", 0xE5: "̄", 0xE6: "̆",
    0xE7: "̇", 0xE8: "̈", 0xE9: "̌", 0xEA: "̊", 0xEB: "︠", 0xEC: "︡", 0xED: "̕",
    0xEE: "̋", 0xEF: "̐", 0xF0: "̧", 0xF1: "̨", 0xF2: "̣", 0xF3: "̤", 0xF4: "̥",
    0xF5: "̳", 0xF6: "̲", 0xF7: "̦", 0xF8: "̜", 0xF9: "̮", 0xFA: "︢", 0xFB: "︣",
    0xFE: "̓",
}
CHARSETS = {"UTF-8": "utf-8", "UTF8": "utf-8", "UNICODE": "utf-16", "ASCII": "ascii", "ANSEL": "ansel",
            "ANSI": "cp1252", "IBMPC": "cp437", "IBM WINDOWS": "cp1252", "WINDOWS-1252": "cp1252", "CP1252": "cp1252",
            "LATIN1": "latin-1", "ISO-8859-1": "latin-1", "ISO8859-1": "latin-1", "MACINTOSH": "mac_roman"}


# values that are free text, where an exporter may leave a raw line break
TEXT_TAGS = {"NOTE", "SNOTE", "TEXT", "PAGE", "TITL", "AUTH", "PUBL"}


class GedcomError(ValueError):
    pass


@dataclass
class Line:
    level: int
    tag: str
    value: str = ""
    xref: str | None = None
    lineno: int = 0
    children: list[Line] = field(default_factory=list)

    def first(self, tag: str) -> Line | None:
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, *tags: str) -> list[Line]:
        return [c for c in self.children if c.tag in tags]

    def val(self, tag: str) -> str:
        c = self.first(tag)
        return c.value.strip() if c else ""

    @property
    def pointer(self) -> str | None:
        """The xref this line points at (`@I1@`), or None for a text value. `@VOID@` points at nothing."""
        v = self.value.strip()
        return v if re.fullmatch(r"@[^@\s#][^@]*@", v) and v != "@VOID@" else None


@dataclass
class Gedcom:
    version: str
    encoding: str
    declared: str
    sha256: str
    records: list[Line]
    repairs: list[dict]
    problems: list[str]
    joined: list[dict] = field(default_factory=list)  # bare lines kept as a new line of the text value before them
    by_xref: dict[str, Line] = field(default_factory=dict)

    def of(self, tag: str) -> list[Line]:
        return [r for r in self.records if r.tag == tag]

    @property
    def individuals(self) -> list[Line]:
        return self.of("INDI")

    @property
    def families(self) -> list[Line]:
        return self.of("FAM")

    @property
    def sources(self) -> list[Line]:
        return self.of("SOUR")

    @property
    def header(self) -> Line | None:
        return next(iter(self.of("HEAD")), None)

    @property
    def major(self) -> int:
        m = re.match(r"\d+", self.version or "")
        return int(m.group(0)) if m else 5


def ansel_decode(raw: bytes) -> tuple[str, list[int]]:
    """ANSEL bytes as text, and the offsets of bytes ANSEL does not define (read as U+FFFD)."""
    out, marks, unknown = [], [], []
    for i, b in enumerate(raw):
        if b < 0x80:
            out.append(chr(b) + "".join(marks))
            marks = []
        elif b in ANSEL_COMBINING:
            marks.append(ANSEL_COMBINING[b])
        elif b in ANSEL_SPACING:
            out.append(ANSEL_SPACING[b] + "".join(marks))
            marks = []
        else:
            unknown.append(i)
            out.append("�")
    out.append("".join(marks))
    return unicodedata.normalize("NFC", "".join(out)), unknown


def _declared(raw: bytes) -> str:
    """The CHAR the header declares, upper case, or "" (a GEDCOM 7 file has none and is UTF-8)."""
    head = raw[:8192].decode("latin-1")
    m = re.search(r"(?m)^\s*1\s+CHAR\s+([^\r\n]+?)\s*$", head)
    return m.group(1).strip().upper() if m else ""


def _not_ansel(raw: bytes) -> bool:
    """An ANSEL line whose accents cannot be ANSEL: a combining mark (which ANSEL puts before its letter) before a
    space, punctuation or the end of the line. A Windows-1252 «José /Muñoz/» labelled ANSEL looks like that."""
    for i, b in enumerate(raw):
        if b in ANSEL_COMBINING:
            nxt = raw[i + 1] if i + 1 < len(raw) else None
            if nxt is None or (nxt < 0x80 and not chr(nxt).isalnum()):
                return True
    return False


def _decode_line(raw: bytes, codec: str) -> tuple[str, str | None]:
    """(text, the codec used when it is not `codec`)."""
    if codec == "ansel":
        try:
            text = raw.decode("utf-8")
            if any(ord(c) > 0x7F for c in text):  # valid UTF-8 with letters ANSEL never writes so: UTF-8
                return text, "utf-8"
        except UnicodeDecodeError:
            pass
        if _not_ansel(raw):
            try:
                return raw.decode("cp1252"), "cp1252"
            except UnicodeDecodeError:
                pass
        text, unknown = ansel_decode(raw)
        return text, ("ansel with unknown bytes" if unknown else None)
    try:
        return raw.decode(codec), None
    except UnicodeDecodeError:
        pass
    for other in ("utf-8", "cp1252"):
        if other == codec:
            continue
        try:
            return raw.decode(other), other
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def _latin_line_is_utf8(raw: bytes, codec: str) -> bool:
    """A line of a Latin-1 or Windows-1252 file that is in fact UTF-8: valid UTF-8 with at least one letter
    beyond ASCII. Mojibake such as «JosÃ©» read as Windows-1252 is what this catches."""
    if codec not in ("cp1252", "latin-1") or all(b < 0x80 for b in raw):
        return False
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


_CONC = re.compile(rb"^(\s*\d{1,2}\s+CONC ?)(.*)$", re.S | re.I)


def _utf8_cut(raw: bytes) -> int:
    """How many bytes the UTF-8 character cut at the end of `raw` still needs, or 0."""
    for back in range(1, min(4, len(raw)) + 1):
        b = raw[-back]
        if b & 0xC0 == 0x80:
            continue  # a continuation byte: look further back for the lead byte
        size = 2 if b & 0xE0 == 0xC0 else 3 if b & 0xF0 == 0xE0 else 4 if b & 0xF8 == 0xF0 else 0
        return size - back if size > back else 0
    return 0


def _mend_conc(chunks: list[bytes], codec: str) -> None:
    """Put back together a character an exporter cut in two across a CONC line, in place: the UTF-8 bytes the
    end of a line lacks move from the start of the CONC after it, and an ANSEL accent left at the end of a line
    moves to the start of the CONC, before the letter it marks."""
    for i in range(len(chunks) - 1):
        m = _CONC.match(chunks[i + 1])
        if not m or not chunks[i]:
            continue
        head, value = m.group(1), m.group(2)
        if codec == "ansel":
            k = len(chunks[i]) - len(chunks[i].rstrip(bytes(ANSEL_COMBINING)))
            if k and value[:1] and value[0] < 0x80 and chr(value[0]).isalpha():
                chunks[i], chunks[i + 1] = chunks[i][:-k], head + chunks[i][-k:] + value
            continue
        need = _utf8_cut(chunks[i])
        if need and len(value) >= need and all(b & 0xC0 == 0x80 for b in value[:need]):
            chunks[i], chunks[i + 1] = chunks[i] + value[:need], head + value[need:]


def decode(raw: bytes) -> tuple[list[tuple[int, str]], str, str, list[dict]]:
    """(numbered lines, codec, declared CHAR, repairs)."""
    declared = ""
    for bom, codec in ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
                       (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                       (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            body = raw[len(bom):]
            if codec != "utf-8":
                text = body.decode(codec, errors="replace")
                return list(enumerate(re.split(r"\r\n|\r|\n", text), 1)), codec, "UNICODE", []
            raw, declared = body, "UTF-8"
            break
    if not declared and b"\x00" in raw[:200]:  # UTF-16 with no byte-order mark: the NULs say which half is zero
        codec = "utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be"
        text = raw.decode(codec, errors="replace")
        lines = list(enumerate(re.split(r"\r\n|\r|\n", text), 1))
        said = _declared(text.encode("utf-8")) or "(none)"
        return lines, codec, said, ([] if said == "UNICODE" else [
            {"line": 1, "declared": said, "read_as": codec, "text": "no byte-order mark; UTF-16 by its NUL bytes"}])
    declared = declared or _declared(raw)
    codec = CHARSETS.get(declared, "utf-8")
    repairs: list[dict] = []
    if codec == "utf-16":  # UNICODE, yet no NUL byte: not UTF-16, whatever the header says
        codec = "utf-8"
        at = next((n for n, c in enumerate(re.split(rb"\r\n|\r|\n", raw), 1) if re.match(rb"\s*1\s+CHAR", c)), 1)
        repairs.append({"line": at, "declared": declared, "read_as": "utf-8",
                        "text": "the file says UNICODE (UTF-16) but has no NUL bytes; read as UTF-8"})
    lines = []
    chunks = re.split(rb"\r\n|\r|\n", raw)
    _mend_conc(chunks, codec)
    for n, chunk in enumerate(chunks, 1):
        if _latin_line_is_utf8(chunk, codec):
            text, used = chunk.decode("utf-8"), "utf-8"
        else:
            text, used = _decode_line(chunk, codec)
        if used:
            repairs.append({"line": n, "declared": declared or "(none)", "read_as": used, "text": text[:120]})
        lines.append((n, text))
    return lines, codec, declared or "(none)", repairs


def read(source: str | bytes) -> Gedcom:
    """Parse a GEDCOM file (a path, or its bytes)."""
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
    else:
        with open(source, "rb") as fh:
            raw = fh.read()
    sha = hashlib.sha256(raw).hexdigest()
    lines, codec, declared, repairs = decode(raw)
    problems: list[str] = []
    joined: list[dict] = []
    records: list[Line] = []
    stack: list[Line] = []
    text_of: Line | None = None  # the text value the last line wrote to, where a bare line belongs
    for n, text in lines:
        if not text.strip():
            continue
        m = _LINE.match(text)
        level = int(m.group(1)) if m else -1
        if not m or (level > 0 and (not stack or level > stack[-1].level + 1 or level > len(stack))):
            if text_of is not None:  # a line break inside a note the exporter did not write as CONT
                text_of.value += "\n" + text.rstrip()
                joined.append({"line": n, "into": text_of.tag, "of_line": text_of.lineno, "text": text[:120]})
                continue
            problems.append(f"line {n}: not a GEDCOM line: {text[:80]!r}" if not m
                            else f"line {n}: level {level} has no parent line")
            continue
        xref, tag, value = m.group(2), m.group(3).upper(), m.group(4) or ""
        if level == 0:
            line = Line(0, tag, value, xref, n)
            records.append(line)
            stack = [line]
            text_of = line if tag in TEXT_TAGS else None
            continue
        parent = stack[level - 1]
        if tag in ("CONC", "CONT"):
            parent.value += ("\n" if tag == "CONT" else "") + value
            text_of = parent if parent.tag in TEXT_TAGS else None
            continue
        line = Line(level, tag, value, xref, n)
        parent.children.append(line)
        del stack[level:]
        stack.append(line)
        text_of = line if tag in TEXT_TAGS and value.strip() else None
    version = ""
    if records and records[0].tag == "HEAD":
        gedc = records[0].first("GEDC")
        version = gedc.val("VERS") if gedc else ""
    elif records:
        problems.append("the file does not start with a HEAD record")
    if not records:
        raise GedcomError("no GEDCOM records in the file")
    ged = Gedcom(version or "5.5.1", codec, declared, sha, records, repairs, problems, joined)
    for rec in records:
        _unescape(rec, seven=ged.major >= 7)
        if rec.xref:
            if rec.xref in ged.by_xref:
                problems.append(f"line {rec.lineno}: {rec.xref} is used by two records; the second is ignored")
                continue
            ged.by_xref[rec.xref] = rec
    if ged.major >= 7 and declared not in ("(none)", "UTF-8"):
        problems.append(f"a GEDCOM 7 file declares CHAR {declared}; GEDCOM 7 is UTF-8 only")
    return ged


def _unescape(line: Line, *, seven: bool) -> None:
    """`@@` is one `@`: at the start of a value in GEDCOM 7, anywhere in a text value in 5.5.1."""
    if line.pointer is None:
        if seven:
            if line.value.startswith("@@"):
                line.value = line.value[1:]
        else:
            line.value = line.value.replace("@@", "@")
    for c in line.children:
        _unescape(c, seven=seven)


# --- dates ---------------------------------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
FRENCH_MONTHS = {m: i for i, m in enumerate("VEND BRUM FRIM NIVO PLUV VENT GERM FLOR PRAI MESS THER FRUC COMP".split(), 1)}
CALENDARS = {"@#DGREGORIAN@": "gregorian", "GREGORIAN": "gregorian", "@#DJULIAN@": "julian", "JULIAN": "julian",
             "@#DFRENCH R@": "french", "FRENCH_R": "french", "@#DHEBREW@": "hebrew", "HEBREW": "hebrew",
             "@#DROMAN@": "roman", "@#DUNKNOWN@": "unknown"}
_PAIRS = {"BET": "AND", "FROM": "TO"}
_SINGLE = {"ABT": "abt", "BEF": "bef", "AFT": "aft", "EST": "est", "CAL": "calc", "TO": "bef"}


def _one(tokens: list[str]) -> tuple[str, str, str | None]:
    """(calendar, YYYY[-MM[-DD]], note) of one GEDCOM date; DateError when it is not one."""
    cal = "gregorian"
    if tokens and tokens[0] in CALENDARS:
        cal = CALENDARS[tokens.pop(0)]
    if cal not in ("gregorian", "julian", "french"):
        raise DateError(f"the {cal} calendar is not one the tool converts")
    note = None
    if tokens and tokens[-1] in ("BC", "B.C.", "BCE"):
        raise DateError("a date before the year 1")
    if not 1 <= len(tokens) <= 3:
        raise DateError("not a GEDCOM date")
    year_tok = tokens[-1]
    m = re.fullmatch(r"(\d{1,4})(?:/(\d{2}))?", year_tok)
    if not m:
        raise DateError(f"unreadable year {year_tok!r}")
    year = int(m.group(1))
    if m.group(2):  # a dual year, 1700/01: the second is the year as counted from 1 January, the next one
        if int(m.group(2)) != (year + 1) % 100:
            raise DateError(f"dual year {year_tok}: the second year is not the one after the first")
        year += 1
        note = f"dual year {year_tok} read as {year}"
    months = FRENCH_MONTHS if cal == "french" else MONTHS
    month = day = 0
    if len(tokens) >= 2:
        if tokens[-2] not in months:
            raise DateError(f"unknown month {tokens[-2]!r} for the {cal} calendar")
        month = months[tokens[-2]]
    if len(tokens) == 3:
        if not tokens[0].isdigit():
            raise DateError(f"unreadable day {tokens[0]!r}")
        day = int(tokens[0])
    ymd = f"{year}" + (f"-{month:02d}" if month else "") + (f"-{day:02d}" if day else "")
    return cal, ymd, note


def ged_date(text: str | None) -> tuple[str | None, str | None]:
    """(date in gwt-apply's grammar, or None when empty; the problem, or None). A date the tool cannot read
    becomes "text: <as written>" with the problem said."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return None, None
    phrase = re.search(r"\((.*)\)\s*$", raw)
    body = raw[:phrase.start()].strip() if phrase else raw
    if not body:
        return f"text: {phrase.group(1).strip() or raw}", None  # a date phrase only: text, as GEDCOM means it
    if body.upper().startswith("INT "):
        body = body[4:]
    up = body.upper()
    # a calendar escape that holds a space ("@#DFRENCH R@") is one token
    up = up.replace("@#DFRENCH R@", "@#DFRENCH_R@")
    tokens = [t.replace("@#DFRENCH_R@", "@#DFRENCH R@") for t in up.split()]
    lead = None  # "@#DJULIAN@ BET 1700 AND 1701": a calendar written once before the modifier
    if len(tokens) > 1 and tokens[0] in CALENDARS and (tokens[1] in _PAIRS or tokens[1] in _SINGLE):
        lead = tokens.pop(0)
    try:
        word, parts = None, [tokens]
        if tokens[0] in _PAIRS and _PAIRS[tokens[0]] in tokens:
            i = tokens.index(_PAIRS[tokens[0]])
            word, parts = ("bet" if tokens[0] == "BET" else "from"), [tokens[1:i], tokens[i + 1:]]
        elif tokens[0] == "FROM":  # an open span: the earliest date of it is all that is known
            word, parts = "aft", [tokens[1:]]
        elif tokens[0] in _SINGLE:
            word, parts = _SINGLE[tokens[0]], [tokens[1:]]
        if lead:
            parts = [p if p and p[0] in CALENDARS else [lead, *p] for p in parts]
        read = [_one(list(p)) for p in parts]
        cals = {c for c, _, _ in read}
        if len(cals) > 1:
            raise DateError("the two ends are in different calendars")
        cal = cals.pop()
        notes = [n for _, _, n in read if n]
        if word in ("bet", "from"):
            out = f"{word} {read[0][1]} {'and' if word == 'bet' else 'to'} {read[1][1]}"
        else:
            out = (f"{word} " if word else "") + read[0][1]
        if cal != "gregorian":
            out = f"{cal} {out}"
        parsed = parse_date(out)  # the real calendar check: 31 FEB is refused here
        if word in ("bet", "from") and parse_date(read[1][1] if cal == "gregorian" else f"{cal} {read[1][1]}")[
                "sortval"] < parsed["sortval"]:
            raise DateError("the range ends before it starts")
        if tokens[0] in ("FROM", "TO") and word in ("aft", "bef"):
            notes.append(f"an open span {tokens[0]} date read as {'after' if word == 'aft' else 'before'} that date")
        if phrase:
            notes.append(f"date phrase dropped: ({phrase.group(1).strip()})")
        return out, "; ".join(notes) or None
    except (DateError, IndexError) as exc:
        return f"text: {raw}", str(exc) or "not a GEDCOM date"
