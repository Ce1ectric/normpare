"""
values.py -- parameters/limits as VALUES (Decimal), not strings.

Captures: sign, decimal comma/point, thousands separators (dot and space),
exponents, comparison operators (<= < >= > =), tolerances (+/-), ranges
("10 bis 20 kV", "10...20 kV", "10-20 kV") and units including SI-prefix
normalization (950 kW == 0.95 MW).

Two functions, on purpose: `extract_values` reads running text and needs a unit,
`extract_cell_values` reads a table cell, where the unit stands in the column
heading and the cell carries the bare number (AP-34).
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

# Units (longest first); base + prefix factor
_UNITS = [
    "kvar", "Mvar", "var", "kVA", "MVA", "GVA", "VA",
    "kWh", "MWh", "GWh", "Wh",
    "kW", "MW", "GW", "W",
    "kV", "MV", "V",
    "kA", "mA", "A",
    "kHz", "MHz", "Hz",
    "ms", "µs", "us", "min", "h", "s",
    "km", "cm", "mm", "m",
    "kΩ", "MΩ", "Ω", "Ohm", "mΩ",
    "µF", "nF", "pF", "F",
    "mH", "µH", "H",
    "°C", "K",
    "%", "p.u.", "pu", "Grad", "°",
]
_PREFIX = {"G": Decimal(1e9), "M": Decimal(1e6), "k": Decimal(1e3),
           "m": Decimal("0.001"), "µ": Decimal("0.000001"), "u": Decimal("0.000001"),
           "n": Decimal("1e-9"), "p": Decimal("1e-12")}
_BASE_UNITS = {"var", "VA", "Wh", "W", "V", "A", "Hz", "s", "m", "Ω", "F", "H"}

_UNIT_RE = "|".join(re.escape(u) for u in _UNITS)
# The thousands separator is a dot or a space (AP-34). "1 000 ms" used to read "000 ms"
# = 0 -- silently wrong rather than missing, and the docstring promised otherwise.
# Exactly three digits behind the separator and no further digit behind them, so that
# "1 0000" is not pulled together. A protected space never reaches this point: every
# production path runs the text through textnorm.n1 first, which turns it into a plain one.
_NUM = (r"[+-]?\d{1,3}(?:[. ]\d{3})+(?!\d)(?:,\d+)?"
        r"|[+-]?\d+(?:[.,]\d+)?(?:\s?[·x]\s?10[⁻\-]?\d+)?")
_OP = r"(?:≤|≥|<=|>=|<|>|=|±|kleiner(?:\s+gleich)?|größer(?:\s+gleich)?|höchstens|mindestens|maximal|minimal|bis zu)"

VALUE_RE = re.compile(
    rf"(?P<op>{_OP})?\s*(?P<num>{_NUM})\s*(?:(?P<range>bis|…|\.\.\.|-)\s*(?P<num2>{_NUM})\s*)?"
    rf"(?P<unit>{_UNIT_RE})(?![\wµ%])")

_OP_CANON = {"kleiner gleich": "<=", "größer gleich": ">=", "kleiner": "<", "größer": ">",
             "höchstens": "<=", "mindestens": ">=", "maximal": "<=", "minimal": ">=",
             "bis zu": "<=", "≤": "<=", "≥": ">=", "<=": "<=", ">=": ">=",
             "<": "<", ">": ">", "=": "=", "±": "±"}


def _parse_num(s: str) -> Decimal | None:
    s = s.strip().replace(" ", "")
    s = re.sub(r"[·x]10[⁻\-]?(\d+)", lambda m: f"e{m.group(1)}", s)
    if re.match(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$", s):      # 1.234,56 / 1.234
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:                          # 0,5
        s = s.replace(",", ".")
    elif re.match(r"^[+-]?\d{1,3}(,\d{3})+$", s):            # 1,234 (English thousands) -- rare
        s = s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _norm_unit(value: Decimal, unit: str) -> tuple[Decimal, str]:
    """Normalize the SI prefix to the base unit (950 kW -> 950000 W)."""
    unit = "Ω" if unit == "Ohm" else unit
    unit = "p.u." if unit == "pu" else unit
    if unit in ("%", "p.u.", "°C", "K", "°", "Grad", "min", "h"):
        return value, unit
    for base in sorted(_BASE_UNITS, key=len, reverse=True):
        if unit.endswith(base):
            pre = unit[: -len(base)]
            if pre == "":
                return value, base
            if pre in _PREFIX:
                return value * _PREFIX[pre], base
    return value, unit


def extract_values(text: str) -> list[dict]:
    """All parameter values in a text: [{op, value, value2, unit, base_value, base_unit, raw}]."""
    out = []
    for m in VALUE_RE.finditer(text or ""):
        v = _parse_num(m.group("num"))
        if v is None:
            continue
        v2 = _parse_num(m.group("num2")) if m.group("num2") else None
        bv, bu = _norm_unit(v, m.group("unit"))
        rec = {"op": _OP_CANON.get((m.group("op") or "").strip().lower(), m.group("op")),
               "value": str(v), "unit": m.group("unit"),
               "base_value": str(bv.normalize()), "base_unit": bu,
               "raw": m.group(0).strip()}
        if v2 is not None:
            bv2, _ = _norm_unit(v2, m.group("unit"))
            rec["value2"], rec["base_value2"] = str(v2), str(bv2.normalize())
        out.append(rec)
    return out


#: A number with a decimal place, with an optional operator in front and **no** unit
#: behind it -- the form a limit value takes in a table cell (AP-34, variant B of the
#: decision gate). The decimal place is what separates a limit value from a row number:
#: taking every bare number would have added 1115 one- and two-digit integers to the new
#: 4110 edition alone, against 374 for this rule, and none of those 374 is an ordinal.
_DECIMAL = r"[+-]?\d{1,3}(?:[. ]\d{3})+(?!\d),\d+|[+-]?\d+,\d+(?:\s?[·x]\s?10[⁻\-]?\d+)?"

BARE_VALUE_RE = re.compile(rf"(?P<op>{_OP})?\s*(?P<num>{_DECIMAL})")


def extract_cell_values(text: str) -> list[dict]:
    """All values of a table **cell**: those of :func:`extract_values` plus bare numbers.

    In a table the unit stands in the column heading and the cell carries the number
    alone -- "Dämpfungsmaß" over "≥ 0,06", "c" over "1,10". :func:`extract_values` demands
    a unit and misses exactly those, and with them the parameters a table exists for.

    Deliberately a function of its own instead of a flag inside :func:`extract_values`:
    the cell carries its context in the column heading, running text does not, where
    "0,06" without a unit is usually a chapter number or a list marker. Paragraph
    enrichment cannot reach the wider rule, and ``paragraphs[].values``, the paragraph
    diff, ``kennwert_changes`` and the synopsis stay as they are.

    A bare number is only taken where no value with a unit already covers it, so
    "≤ 500 kW" stays one value and does not become two. It carries ``unit`` and
    ``base_unit`` as ``None``, which makes it distinguishable from a value with a unit and
    -- through the unchanged comparison key ``(base_unit, base_value)`` -- pairable only
    with another unitless value, never with "0,06 s".

    The extra hits come after the values with a unit, in the order the text yields them.
    """
    text = text or ""
    out = extract_values(text)
    taken = [m.span() for m in VALUE_RE.finditer(text)]
    for m in BARE_VALUE_RE.finditer(text):
        start, end = m.span("num")
        if any(start < hi and lo < end for lo, hi in taken):
            continue
        v = _parse_num(m.group("num"))
        if v is None:
            continue
        out.append({"op": _OP_CANON.get((m.group("op") or "").strip().lower(),
                                        m.group("op")),
                    "value": str(v), "unit": None,
                    "base_value": str(v.normalize()), "base_unit": None,
                    "raw": m.group(0).strip()})
    return out


def cell_values(cells) -> list[dict]:
    """The values of a table's cells: ``[{row, col, values}]``, one record per cell.

    The same normal form the paragraph text runs through (``n1``), so a limit value in a
    cell comes out as the record it would produce in running text -- otherwise the two
    halves of the inventory could not be compared with each other. Since AP-34 through
    :func:`extract_cell_values`, which additionally takes the bare number whose unit
    stands in the column heading; a value with a unit is unaffected by that.

    Only cells that carry a value get a record. Empty and purely textual cells produce
    nothing: a record per cell would repeat the whole matrix, and the length of this list
    is exactly the number that matters (AP-31).
    """
    from ...text.textnorm import n1
    out = []
    for r, row in enumerate(cells or []):
        for c, cell in enumerate(row or []):
            found = extract_cell_values(n1(cell or ""))
            if found:
                out.append({"row": r, "col": c, "values": found})
    return out


def value_keys(records) -> set[tuple[str | None, str]]:
    """``(base_unit, base_value)`` of every value in ``[{row, col, values}]`` records.

    The comparison key of a value, deliberately without the operator: a cell reads
    "≤ 500 kW" where an interpretation says "500 kW -> 400 kW", and demanding the same
    operator would fail a value that is demonstrably present.

    The unit stays in the key, and ``None`` is a unit in it like any other -- one that
    equals only itself (AP-34). A damping factor of 0,06 must not confirm a claim about
    0,06 seconds.
    """
    return {(v["base_unit"], v["base_value"])
            for rec in records or [] for v in rec.get("values") or []}


def _key(rec: dict) -> tuple:
    return (rec["base_unit"], rec["base_value"], rec.get("base_value2"), rec.get("op"))


def diff_values(old_text: str, new_text: str) -> dict:
    """Value-level parameter diff. changed = same unit, different value
    (only when exactly one 'free' value of that unit is left on each side)."""
    ov, nv = extract_values(old_text), extract_values(new_text)
    okeys, nkeys = [_key(r) for r in ov], [_key(r) for r in nv]
    # cancel out exactly equal values (multiset)
    from collections import Counter
    oc, nc = Counter(okeys), Counter(nkeys)
    same = oc & nc
    # remove equal values pairwise, compare the rest
    rem_o, rem_n = [], []
    used = Counter()
    for r in ov:
        k = _key(r)
        if used[k] < same.get(k, 0):
            used[k] += 1
        else:
            rem_o.append(r)
    used = Counter()
    for r in nv:
        k = _key(r)
        if used[k] < same.get(k, 0):
            used[k] += 1
        else:
            rem_n.append(r)
    changed, added, removed = [], [], []
    by_unit_o, by_unit_n = {}, {}
    for r in rem_o: by_unit_o.setdefault(r["base_unit"], []).append(r)
    for r in rem_n: by_unit_n.setdefault(r["base_unit"], []).append(r)
    for u in sorted(set(by_unit_o) | set(by_unit_n)):  # sorted -> deterministic order (reproducibility)
        o_list, n_list = by_unit_o.get(u, []), by_unit_n.get(u, [])
        if len(o_list) == 1 and len(n_list) == 1:
            changed.append({"unit": u, "old": o_list[0], "new": n_list[0]})
        else:
            removed += o_list
            added += n_list
    return {"changed": changed, "added": added, "removed": removed,
            "n_old": len(ov), "n_new": len(nv)}
