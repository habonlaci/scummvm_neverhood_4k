#!/usr/bin/env python3
"""upscale_wrap.py - coordinate upscale-macro wrapper for The Neverhood 4K fork.

For a given module .cpp file under engines/neverhood/modules/ this tool:
  * finds call sites / code patterns that take game-space (640x480) coordinates
    (catalog: tools/nhk4k/coordinate_sites.md),
  * wraps INTEGER-LITERAL coordinate arguments in UPSCALE_X / UPSCALE_Y /
    UPSCALE(x, y) (pair form matches the style used by the fully covered
    modules module1000/module1400/module2200/module2800),
  * SKIPS and FLAGS any site whose coordinate argument is an expression or a
    variable (double-scaling risk) into tools/nhk4k/review/<module>.md.

Idempotent: an argument already inside an UPSCALE macro is never re-wrapped.
--dry-run reports proposals without touching the source file (review files are
still written unless --no-review is given).

The insertSprite<T>/insertKlaymen<T>/new T(...) argument positions are derived
automatically from constructor signatures parsed from engines/neverhood/**/*.h:
  insertSprite<T>(a, b, ...)  -> T(vm, a, b, ...)          (offset 1)
  insertKlaymen<T>(a, b, ...) -> T(vm, this, a, b, ...)    (offset 2)
  new T(a, b, ...)            -> T(a, b, ...)              (offset 0)
Constructor parameters named x/y/x1/y1/x2/y2/width/height are coordinate slots.

Usage:
  python upscale_wrap.py [--dry-run] [--no-review] [--review-dir DIR] file.cpp ...
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
NEVERHOOD_DIR = os.path.join(REPO_ROOT, "engines", "neverhood")
DEFAULT_REVIEW_DIR = os.path.join(HERE, "review")

# --------------------------------------------------------------------------
# Catalog: fixed-signature functions.
# name -> list of (min_slots, max_slots, {slot_index: axis})
# slot = effective argument position AFTER expanding UPSCALE(x, y) pair args
# into two positions (the macro expands to two comma-separated values).
# --------------------------------------------------------------------------
FIXED_FUNCS = {
    "setClipRect":          [(4, 4, {0: "X", 1: "Y", 2: "X", 3: "Y"})],
    "insertPuzzleMouse":    [(3, 3, {1: "X", 2: "X"})],
    "createSurface":        [(3, 3, {1: "X", 2: "Y"})],
    "drawString":           [(4, 5, {1: "X", 2: "Y"})],
    "copyFrom":             [(4, 4, {1: "X", 2: "Y"})],
    "copyFromWithAlpha":    [(4, 4, {1: "X", 2: "Y"})],
    "loadSprite":           [(5, 5, {3: "X", 4: "Y"}), (4, 4, {3: "X"})],
    "setX":                 [(1, 1, {0: "X"})],
    "setY":                 [(1, 1, {0: "Y"})],
    "startSpecialWalkRight": [(1, 1, {0: "X"})],
    "startSpecialWalkLeft": [(1, 1, {0: "X"})],
    "startWalkToX":         [(2, 2, {0: "X"})],
    "startWalkToXSmall":    [(1, 1, {0: "X"})],
    "startWalkToXDistance": [(2, 2, {0: "X", 1: "X"})],
}
# NRect::set(x1, y1, x2, y2): method name "set" is too generic; only matched
# when the receiver identifier contains "rect" (e.g. _leftDoorClipRect.set()).
RECT_SET_SPEC = [(4, 4, {0: "X", 1: "Y", 2: "X", 3: "Y"})]

# Look-alikes that must NEVER be scaled; listed so the tool can explicitly
# refuse them even if someone adds them to FIXED_FUNCS by mistake.
NEVER_SCALE_FUNCS = {
    "insertStaticSprite", "createScene", "createNavigationScene",
    "createSmackerScene", "setBackground", "setPalette", "setMessageList",
    "setMessageList2", "setRectList", "setSubVar", "setGlobalVar",
    "getGlobalVar", "playSound", "startAnimation", "setSurfacePriority",
    "setRepl", "setDoDeltaX", "createSurface1", "startMusic", "stopMusic",
    "addBasePalette", "startFadeToPalette", "leaveScene", "leaveModule",
    "setSoundListParams", "setSoundVolume", "setSoundParams", "playTwoSounds",
}

COORD_PARAM_AXIS = {
    "x": "X", "y": "Y", "x1": "X", "y1": "Y", "x2": "X", "y2": "Y",
    "width": "X", "height": "Y",
}

INT_LIT_RE = re.compile(r"^[-+]?\d+$")
HEX_LIT_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
UPSCALE_PAIR_RE = re.compile(r"^UPSCALE\s*\(")
UPSCALE_ONE_RE = re.compile(r"^UPSCALE_[XY]\s*\(")


# --------------------------------------------------------------------------
# Comment / string masking
# --------------------------------------------------------------------------
def make_scan_text(text):
    """Return a copy of text with comments and string/char literal contents
    replaced by spaces (same length, offsets preserved)."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = i
            while j < n and text[j] != "\n":
                out[j] = " "
                j += 1
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = i
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            while j < end:
                if text[j] != "\n":
                    out[j] = " "
                j += 1
            i = end
        elif c == '"' or c == "'":
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == "\\":
                    j += 1
                j += 1
            for k in range(i + 1, min(j, n)):
                if text[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def line_text(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return text[start:end].strip()


# --------------------------------------------------------------------------
# Constructor signature database
# --------------------------------------------------------------------------
def split_top_commas(s):
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{<" and ch != "<":
            depth += 1
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


@dataclass
class CtorSig:
    params: list          # list of (name, axis_or_None, has_default)
    header: str

    def min_args(self):
        n = len(self.params)
        while n > 0 and self.params[n - 1][2]:
            n -= 1
        return n


CTOR_DECL_RE = re.compile(
    r"\b([A-Z]\w*)\s*\(\s*NeverhoodEngine\s*\*\s*\w+\s*"
    r"((?:,[^;{}()]*)*)\)\s*(?:;|:|\{)")


def build_ctor_db():
    db = {}
    for root, _dirs, files in os.walk(NEVERHOOD_DIR):
        for fn in files:
            if not fn.endswith(".h"):
                continue
            path = os.path.join(root, fn)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = make_scan_text(f.read())
            for m in CTOR_DECL_RE.finditer(text):
                name, rest = m.group(1), m.group(2)
                params = []
                rest = rest.strip()
                if rest.startswith(","):
                    rest = rest[1:]
                if rest:
                    for p in split_top_commas(rest):
                        p = p.strip()
                        has_default = "=" in p
                        p_nodef = p.split("=")[0].strip()
                        idm = re.findall(r"[A-Za-z_]\w*", p_nodef)
                        pname = idm[-1] if idm else ""
                        axis = COORD_PARAM_AXIS.get(pname)
                        params.append((pname, axis, has_default))
                db.setdefault(name, []).append(
                    CtorSig(params, os.path.relpath(path, REPO_ROOT)))
    return db


# --------------------------------------------------------------------------
# Site / result model
# --------------------------------------------------------------------------
@dataclass
class Proposal:
    start: int
    end: int
    replacement: str
    line: int
    desc: str


@dataclass
class Flag:
    line: int
    category: str
    code: str
    hint: str


@dataclass
class FileResult:
    path: str = ""
    wrapped: int = 0        # coordinate slots already wrapped in UPSCALE*
    proposals: list = field(default_factory=list)   # auto-wrappable literals
    flags: list = field(default_factory=list)       # non-literal / uncertain
    expr_ok: int = 0        # non-literal slots silently accepted (contain UPSCALE)


def classify_expr_hint(arg):
    a = arg.strip()
    if re.search(r"getDrawRect\(\)|getClipRect\(\)|getCollision", a):
        return ("runtime rect from another sprite - already in scaled space; "
                "do NOT wrap")
    if re.search(r"\bpt\s*\.\s*[xy]\b|_dataResource\s*\.\s*getPoint|getPoint\(", a):
        return ("point from _dataResource - data resources are pre-scaled at "
                "load; do NOT wrap")
    if re.search(r"get[XY]\(\)", a):
        return "runtime sprite position - already scaled; do NOT wrap"
    if re.search(r"asInteger\(\)", a):
        return ("game-space value from a message list param; covered modules "
                "wrap these as UPSCALE_X(param.asInteger()) at the receiver")
    if re.search(r"^[A-Za-z_]\w*$", a):
        return "plain variable - trace its origin before scaling"
    if HEX_LIT_RE.match(a):
        return "hex literal in a coordinate slot - probably a hash; verify"
    return "expression - verify whether the value is game-space or already scaled"


# --------------------------------------------------------------------------
# Argument scanning
# --------------------------------------------------------------------------
def scan_args(text, scan, open_paren):
    """Return (args, close_idx). args = list of (raw_text, start, end)
    for each top-level argument between open_paren and its match."""
    assert scan[open_paren] == "("
    depth = 0
    i = open_paren
    n = len(scan)
    args = []
    arg_start = open_paren + 1
    while i < n:
        c = scan[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                if i > arg_start or args:
                    args.append((text[arg_start:i], arg_start, i))
                return args, i
        elif c == "," and depth == 1:
            args.append((text[arg_start:i], arg_start, i))
            arg_start = i + 1
        i += 1
    return args, n - 1


def expand_slots(args):
    """Expand argument list into effective slots. UPSCALE(x, y) covers two
    slots. Returns list of dicts: {kind, arg, start, end}
    kind in {'wrapped', 'literal', 'expr', 'empty'}"""
    slots = []
    for raw, s, e in args:
        a = raw.strip()
        if not a:
            continue
        if UPSCALE_PAIR_RE.match(a):
            slots.append({"kind": "wrapped", "arg": a, "start": s, "end": e})
            slots.append({"kind": "wrapped", "arg": a, "start": s, "end": e})
        elif UPSCALE_ONE_RE.match(a) or "UPSCALE" in a:
            slots.append({"kind": "wrapped", "arg": a, "start": s, "end": e})
        elif INT_LIT_RE.match(a):
            # keep exact literal span (strip whitespace inside the arg span)
            ls = s + (len(raw) - len(raw.lstrip()))
            le = ls + len(a)
            slots.append({"kind": "literal", "arg": a, "start": ls, "end": le})
        else:
            slots.append({"kind": "expr", "arg": a, "start": s, "end": e})
    return slots


CALL_RE = re.compile(
    r"(?P<new>\bnew\s+)?\b(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:<\s*(?P<tmpl>\w+)\s*>)?\s*\(")


def pick_ctor_map(sigs, offset, nslots):
    """Return ({slot: axis} or None, ambiguous_bool)."""
    candidates = []
    for sig in sigs:
        usable = sig.params[offset:]
        min_req = max(0, sig.min_args() - offset)
        if min_req <= nslots <= len(usable):
            amap = {i: ax for i, (_n, ax, _d) in enumerate(usable[:nslots]) if ax}
            candidates.append(amap)
    if not candidates:
        return None, False
    first = candidates[0]
    for c in candidates[1:]:
        if c != first:
            return None, True
    return first, False


def emit_coord_slots(res, text, callname, slots, coord_map):
    """Given effective slots and coordinate map, record wrapped counts,
    proposals (pair-form when X,Y adjacent literals) and flags."""
    i = 0
    n = len(slots)
    while i < n:
        axis = coord_map.get(i)
        if axis is None:
            i += 1
            continue
        slot = slots[i]
        if slot["kind"] == "wrapped":
            res.wrapped += 1
            i += 1
            continue
        if slot["kind"] == "literal":
            nxt = coord_map.get(i + 1)
            if (axis == "X" and nxt == "Y" and i + 1 < n
                    and slots[i + 1]["kind"] == "literal"
                    and slots[i + 1] is not slot):
                a, b = slot, slots[i + 1]
                res.proposals.append(Proposal(
                    a["start"], b["end"],
                    "UPSCALE(%s, %s)" % (a["arg"], b["arg"]),
                    line_of(text, a["start"]),
                    "%s: (%s, %s) -> UPSCALE(%s, %s)" % (
                        callname, a["arg"], b["arg"], a["arg"], b["arg"])))
                i += 2
                continue
            res.proposals.append(Proposal(
                slot["start"], slot["end"],
                "UPSCALE_%s(%s)" % (axis, slot["arg"]),
                line_of(text, slot["start"]),
                "%s: %s -> UPSCALE_%s(%s)" % (
                    callname, slot["arg"], axis, slot["arg"])))
            i += 1
            continue
        # expression / variable -> flag
        res.flags.append(Flag(
            line_of(text, slot["start"]),
            "call-arg (%s axis %s, slot %d)" % (callname, axis, i),
            line_text(text, slot["start"]),
            classify_expr_hint(slot["arg"])))
        i += 1


# --------------------------------------------------------------------------
# Pass A: catalog function calls / constructors
# --------------------------------------------------------------------------
def pass_calls(text, scan, ctor_db, res):
    for m in CALL_RE.finditer(scan):
        name = m.group("name")
        tmpl = m.group("tmpl")
        is_new = bool(m.group("new"))
        open_paren = m.end() - 1
        spec = None
        callname = name

        if name in ("insertSprite", "insertKlaymen") and tmpl:
            offset = 1 if name == "insertSprite" else 2
            args, _ = scan_args(text, scan, open_paren)
            slots = expand_slots(args)
            sigs = ctor_db.get(tmpl)
            callname = "%s<%s>" % (name, tmpl)
            if not sigs:
                if any(s["kind"] == "literal" for s in slots):
                    res.flags.append(Flag(
                        line_of(text, m.start()), "unknown-class",
                        line_text(text, m.start()),
                        "no constructor signature found for %s; cannot map "
                        "coordinate positions - review manually" % tmpl))
                continue
            cmap, ambiguous = pick_ctor_map(sigs, offset, len(slots))
            if cmap is None:
                if ambiguous and any(s["kind"] == "literal" for s in slots):
                    res.flags.append(Flag(
                        line_of(text, m.start()), "ambiguous-overload",
                        line_text(text, m.start()),
                        "multiple %s constructors match this arg count with "
                        "different coordinate positions" % tmpl))
                continue
            emit_coord_slots(res, text, callname, slots, cmap)
            continue

        if is_new and name in ctor_db:
            args, _ = scan_args(text, scan, open_paren)
            slots = expand_slots(args)
            # new T(vm, ...) -> params map directly (offset 0)
            cmap, ambiguous = pick_ctor_map(ctor_db[name], 0, len(slots))
            callname = "new %s" % name
            if cmap is None:
                if ambiguous and any(s["kind"] == "literal" for s in slots):
                    res.flags.append(Flag(
                        line_of(text, m.start()), "ambiguous-overload",
                        line_text(text, m.start()),
                        "multiple %s constructors match this arg count with "
                        "different coordinate positions" % name))
                continue
            emit_coord_slots(res, text, callname, slots, cmap)
            continue

        if name == "set":
            # NRect::set(x1, y1, x2, y2) - require rect-ish receiver
            before = scan[:m.start()].rstrip()
            if not (before.endswith(".") or before.endswith("->")):
                continue
            recv = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:\.|->)$",
                             before + ("." if before.endswith(".") else "->"))
            recv_m = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:\.|->)\s*$",
                               scan[:m.start()])
            rid = recv_m.group(1) if recv_m else (recv.group(1) if recv else "")
            if "rect" not in rid.lower():
                continue
            spec = RECT_SET_SPEC
            callname = "%s.set" % rid

        elif name in FIXED_FUNCS:
            spec = FIXED_FUNCS[name]
        else:
            continue

        args, _ = scan_args(text, scan, open_paren)
        slots = expand_slots(args)
        for mn, mx, cmap in spec:
            if mn <= len(slots) <= mx:
                emit_coord_slots(res, text, callname, slots, cmap)
                break


# --------------------------------------------------------------------------
# Pass B: member / local coordinate assignments and comparisons
# --------------------------------------------------------------------------
WRAPPED_LIT = r"UPSCALE_[XY]\s*\(\s*-?\d+\s*\)"

B_MEMBER_ASSIGN = re.compile(
    r"(?<![\w.>])(_x|_y|_newX|_newY)\s*(=|\+=|-=)\s*"
    r"(?:(?P<lit>-?\d+)|(?P<wr>" + WRAPPED_LIT + r"))\s*;")
B_MEMBER_COMPLEX = re.compile(
    r"(?<![\w.>])(_x|_y)\s*=\s*(?P<rhs>[^;=][^;]*);")
B_FIELD_ASSIGN = re.compile(
    r"(?:\w|\)|\])\s*(?:\.|->)\s*(x1|x2|y1|y2|x|y|width|height)\s*=\s*"
    r"(?:(?P<lit>-?\d+)|(?P<wr>" + WRAPPED_LIT + r"))\s*;")
COORD_EXPR = (r"(?:\bget(?P<gxy>[XY])\(\)"
              r"|(?<![\w.>])_(?P<mxy>x|y|newX|newY)\b"
              r"|(?:\.|->)(?P<fxy>x1|x2|y1|y2|x|y)\b(?!\s*\()"
              r")")
B_CMP = re.compile(
    COORD_EXPR + r"\s*(?:==|!=|<=|>=|<|>)\s*"
    r"(?:(?P<lit>-?\d+)\b(?![\d.xX])|(?P<wr>" + WRAPPED_LIT + r"))")
B_CMP_REV = re.compile(
    r"(?:(?P<lit>-?\d+)|(?P<wr>" + WRAPPED_LIT + r"))\s*(?:<=|>=|<|>)\s*"
    + COORD_EXPR)
B_DELTA = re.compile(
    COORD_EXPR + r"\s*(?P<op>[-+])\s*(?:(?P<lit>\d+)\b(?![\d.])|(?P<wr>"
    + WRAPPED_LIT + r"))")
B_LOCAL_DECL = re.compile(
    r"\bint16\s+(x|y)\s*=\s*(?:(?P<lit>-?\d+)|(?P<wr>" + WRAPPED_LIT + r"))\s*;")
B_LOCAL_INC = re.compile(
    r"(?<![\w.>])(x|y)\s*(\+=|-=)\s*(?:(?P<lit>-?\d+)|(?P<wr>"
    + WRAPPED_LIT + r"))\s*;")


def axis_of_coord_token(m):
    g = m.groupdict()
    tok = g.get("gxy") or g.get("mxy") or g.get("fxy") or ""
    return "X" if "x" in tok.lower() or tok == "X" else "Y"


def _record_simple(res, text, m, axis, what, flag_only=False, skip_zero=False):
    lit = m.group("lit")
    if m.group("wr"):
        res.wrapped += 1
        return
    if lit is None:
        return
    if skip_zero and int(lit) == 0:
        return  # UPSCALE(0) == 0: wrapping a comparison against 0 is a no-op
    s, e = m.start("lit"), m.end("lit")
    if flag_only:
        res.flags.append(Flag(
            line_of(text, s), what, line_text(text, s),
            "coordinate delta/offset arithmetic - wrap the literal in "
            "UPSCALE_%s(...) only if the other operand is a scaled "
            "coordinate" % axis))
        return
    res.proposals.append(Proposal(
        s, e, "UPSCALE_%s(%s)" % (axis, lit), line_of(text, s),
        "%s: %s -> UPSCALE_%s(%s)" % (what, lit, axis, lit)))


def pass_patterns(text, scan, res):
    claimed = []

    def overlaps(s, e):
        return any(not (e <= cs or s >= ce) for cs, ce in claimed)

    for m in B_MEMBER_ASSIGN.finditer(scan):
        axis = "X" if "x" in m.group(1).lower() or "X" in m.group(1) else "Y"
        axis = "X" if m.group(1) in ("_x", "_newX") else "Y"
        _record_simple(res, text, m, axis, "%s %s" % (m.group(1), m.group(2)))
        claimed.append((m.start(), m.end()))

    for m in B_MEMBER_COMPLEX.finditer(scan):
        if overlaps(m.start(), m.end()):
            continue
        rhs = m.group("rhs").strip()
        if "UPSCALE" in rhs or INT_LIT_RE.match(rhs):
            continue
        if rhs in ("x", "y", "_x", "_y"):
            hint = ("constructor/param passthrough - value already scaled by "
                    "the caller; do NOT wrap")
        else:
            hint = classify_expr_hint(rhs)
        res.flags.append(Flag(
            line_of(text, m.start()), "%s = <expr>" % m.group(1),
            line_text(text, m.start()), hint))
        claimed.append((m.start(), m.end()))

    for m in B_FIELD_ASSIGN.finditer(scan):
        f = m.group(1)
        axis = "X" if f in ("x", "x1", "x2", "width") else "Y"
        _record_simple(res, text, m, axis, ".%s =" % f)
        claimed.append((m.start(), m.end()))

    for m in B_LOCAL_DECL.finditer(scan):
        # skip loop inits: 'for (int16 x = 0;'
        prefix = scan[max(0, m.start() - 8):m.start()]
        if "for" in prefix and "(" in prefix:
            continue
        _record_simple(res, text, m, "X" if m.group(1) == "x" else "Y",
                       "int16 %s =" % m.group(1))
        claimed.append((m.start(), m.end()))

    for m in B_LOCAL_INC.finditer(scan):
        if overlaps(m.start(), m.end()):
            continue
        _record_simple(res, text, m, "X" if m.group(1) == "x" else "Y",
                       "%s %s" % (m.group(1), m.group(2)))
        claimed.append((m.start(), m.end()))

    for m in B_DELTA.finditer(scan):
        if overlaps(m.start(), m.end()):
            continue
        _record_simple(res, text, m, axis_of_coord_token(m),
                       "coord %s literal" % m.group("op"), flag_only=True)
        claimed.append((m.start(), m.end()))

    for m in B_CMP.finditer(scan):
        if overlaps(m.start(), m.end()):
            continue
        _record_simple(res, text, m, axis_of_coord_token(m), "comparison",
                       skip_zero=True)
        claimed.append((m.start(), m.end()))

    for m in B_CMP_REV.finditer(scan):
        if overlaps(m.start(), m.end()):
            continue
        # skip if literal is part of arithmetic (binary +/-), B_DELTA handles
        j = m.start()
        k = j - 1
        while k >= 0 and scan[k] in " \t":
            k -= 1
        if k >= 0 and scan[k] in "+-*/%":
            continue
        _record_simple(res, text, m, axis_of_coord_token(m), "comparison(rev)",
                       skip_zero=True)
        claimed.append((m.start(), m.end()))


# --------------------------------------------------------------------------
# Pass C: static coordinate tables
# --------------------------------------------------------------------------
NPOINT_TABLE_RE = re.compile(
    r"static\s+const\s+NPoint\s+(\w+)\s*\[\s*\]\s*=\s*\{")
INT16_TABLE_RE = re.compile(
    r"static\s+const\s+int16\s+(\w+)\s*\[\s*\]\s*=\s*\{")
OTHER_TABLE_RE = re.compile(
    r"static\s+const\s+([A-Z]\w*)\s+(\w+)\s*\[\s*\]\s*=\s*\{")
NPOINT_ENTRY_RE = re.compile(r"\{\s*(-?\d+)\s*,\s*(-?\d+)\s*\}")
NPOINT_WRAPPED_RE = re.compile(r"\{\s*UPSCALE\s*\(")
INT_ELEM_RE = re.compile(r"(?<![\w(])(-?\d+)(?![\w.])")
WRAP_ELEM_RE = re.compile(WRAPPED_LIT)


def find_block_end(scan, open_brace):
    depth = 0
    for i in range(open_brace, len(scan)):
        if scan[i] == "{":
            depth += 1
        elif scan[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(scan) - 1


def table_axis_from_name(name):
    has_x = "X" in name
    has_y = "Y" in name
    if has_x and not has_y:
        return "X"
    if has_y and not has_x:
        return "Y"
    return None


def pass_tables(text, scan, res):
    for m in NPOINT_TABLE_RE.finditer(scan):
        b0 = m.end() - 1
        b1 = find_block_end(scan, b0)
        block = scan[b0:b1]
        for w in NPOINT_WRAPPED_RE.finditer(block):
            res.wrapped += 2
        for em in NPOINT_ENTRY_RE.finditer(block):
            s = b0 + em.start()
            res.proposals.append(Proposal(
                s, b0 + em.end(),
                "{UPSCALE(%s, %s)}" % (em.group(1), em.group(2)),
                line_of(text, s),
                "NPoint table %s: {%s, %s} -> {UPSCALE(%s, %s)}" % (
                    m.group(1), em.group(1), em.group(2),
                    em.group(1), em.group(2))))

    for m in INT16_TABLE_RE.finditer(scan):
        name = m.group(1)
        b0 = m.end() - 1
        b1 = find_block_end(scan, b0)
        block = scan[b0:b1]
        axis = table_axis_from_name(name)
        wrapped_spans = [(w.start(), w.end()) for w in WRAP_ELEM_RE.finditer(block)]
        res.wrapped += len(wrapped_spans)
        bare = [em for em in INT_ELEM_RE.finditer(block)
                if not any(ws <= em.start() < we for ws, we in wrapped_spans)]
        if not bare:
            continue
        if axis is None:
            res.flags.append(Flag(
                line_of(text, m.start()), "int16 table (axis unknown)",
                line_text(text, m.start()),
                "cannot infer X/Y axis from table name '%s'; decide manually "
                "whether these are coordinates at all" % name))
            continue
        for em in bare:
            s = b0 + em.start()
            res.proposals.append(Proposal(
                s, b0 + em.end(),
                "UPSCALE_%s(%s)" % (axis, em.group(1)),
                line_of(text, s),
                "int16 table %s: %s -> UPSCALE_%s(%s)" % (
                    name, em.group(1), axis, em.group(1))))

    # unknown struct tables that contain {int, int} pairs -> flag once
    for m in OTHER_TABLE_RE.finditer(scan):
        typ, name = m.group(1), m.group(2)
        if typ in ("NPoint", "NRect", "int16", "uint32", "byte", "uint",
                   "int", "char"):
            if typ != "NRect":
                continue
        b0 = m.end() - 1
        b1 = find_block_end(scan, b0)
        block = scan[b0:b1]
        if "UPSCALE" in block:
            res.wrapped += len(WRAP_ELEM_RE.findall(block)) + \
                2 * len(re.findall(r"UPSCALE\s*\(", block)) - \
                len(WRAP_ELEM_RE.findall(block))
            continue
        if NPOINT_ENTRY_RE.search(block):
            res.flags.append(Flag(
                line_of(text, m.start()),
                "struct table %s %s" % (typ, name),
                line_text(text, m.start()),
                "table of struct type '%s' contains integer pairs; if the "
                "struct embeds NPoint/NRect coordinates they must be wrapped "
                "manually (cf. kAsScene1404ProjectorItems in "
                "module1400_sprites.cpp)" % typ))


# --------------------------------------------------------------------------
# Pass D: informational sendMessage flags
# --------------------------------------------------------------------------
SENDMSG_RE = re.compile(r"\bsendMessage\s*\(")


def pass_sendmessage(text, scan, res):
    for m in SENDMSG_RE.finditer(scan):
        args, _ = scan_args(text, scan, m.end() - 1)
        if len(args) != 3:
            continue
        third = args[2][0].strip()
        if INT_LIT_RE.match(third) and third not in ("0", "1", "-1"):
            res.flags.append(Flag(
                line_of(text, args[2][1]), "sendMessage param",
                line_text(text, args[2][1]),
                "non-zero integer message param: may be a coordinate (cf. "
                "sendMessage(_asElevator, 0x2000, UPSCALE_Y(480)) in "
                "module2200.cpp:1178) or an enum/count - verify the "
                "receiver's handleMessage before scaling"))


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def analyze_file(path, ctor_db):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    scan = make_scan_text(text)
    res = FileResult(path=path)
    pass_calls(text, scan, ctor_db, res)
    pass_patterns(text, scan, res)
    pass_tables(text, scan, res)
    pass_sendmessage(text, scan, res)
    # de-dup and sort proposals; drop overlapping (defensive)
    res.proposals.sort(key=lambda p: p.start)
    dedup = []
    last_end = -1
    for p in res.proposals:
        if p.start >= last_end:
            dedup.append(p)
            last_end = p.end
    res.proposals = dedup
    res.flags.sort(key=lambda fl: fl.line)
    return res, text


def apply_proposals(text, proposals):
    out = text
    for p in sorted(proposals, key=lambda p: p.start, reverse=True):
        out = out[:p.start] + p.replacement + out[p.end:]
    return out


def write_review(res, review_dir, dry_run):
    os.makedirs(review_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(res.path))[0]
    out = os.path.join(review_dir, base + ".md")
    lines = []
    lines.append("# Review: %s" % os.path.basename(res.path))
    lines.append("")
    lines.append("Generated by upscale_wrap.py%s. Flagged sites are NOT "
                 "auto-wrapped (non-literal coordinate arguments = "
                 "double-scaling risk). Fill in a decision for each."
                 % (" (dry-run)" if dry_run else ""))
    lines.append("")
    lines.append("Summary: %d slot(s) already wrapped, %d auto-wrappable "
                 "literal(s), %d flagged site(s)."
                 % (res.wrapped, len(res.proposals), len(res.flags)))
    lines.append("")
    if not res.flags:
        lines.append("No flagged sites.")
    for fl in res.flags:
        lines.append("## line %d - %s" % (fl.line, fl.category))
        lines.append("")
        lines.append("```cpp")
        lines.append(fl.code)
        lines.append("```")
        lines.append("")
        lines.append("- Tool hint: %s" % fl.hint)
        lines.append("- Decision: _(scale / don't scale / unsure - fill in)_")
        lines.append("")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--dry-run", action="store_true",
                    help="report proposals only; do not modify source files")
    ap.add_argument("--no-review", action="store_true",
                    help="do not write review files")
    ap.add_argument("--review-dir", default=DEFAULT_REVIEW_DIR)
    args = ap.parse_args(argv)

    ctor_db = build_ctor_db()
    print("[ctor-db] %d classes with NeverhoodEngine* constructors" % len(ctor_db))

    grand_wrap = grand_prop = grand_flag = 0
    for path in args.files:
        path = os.path.abspath(path)
        res, text = analyze_file(path, ctor_db)
        print("")
        print("=== %s" % os.path.relpath(path, REPO_ROOT))
        print("already wrapped slots : %d" % res.wrapped)
        print("auto-wrappable        : %d" % len(res.proposals))
        print("flagged (review)      : %d" % len(res.flags))
        for p in res.proposals:
            print("  L%-5d %s" % (p.line, p.desc))
        if not args.no_review:
            out = write_review(res, args.review_dir, args.dry_run)
            print("review file           : %s" % os.path.relpath(out, REPO_ROOT))
        if not args.dry_run and res.proposals:
            newtext = apply_proposals(text, res.proposals)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(newtext)
            print("APPLIED %d replacements" % len(res.proposals))
        grand_wrap += res.wrapped
        grand_prop += len(res.proposals)
        grand_flag += len(res.flags)
    print("")
    print("TOTAL: wrapped=%d auto-wrappable=%d flagged=%d%s" % (
        grand_wrap, grand_prop, grand_flag,
        " (dry-run, no source changes)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
