"""Tests for normpare.text.textnorm (normalization layers N1-N3)."""
from normpare.text.textnorm import n1, n2, n3


# --- U+2011 (non-breaking hyphen): regression test for the 2026-07-04 fix ---
def test_u2011_normalized_in_n1():
    assert n1("DIN EN 60909‑0") == "DIN EN 60909-0"
    assert n1("Typ‑2-EZA") == "Typ-2-EZA"
    assert n1("IEC 60364‑4") == "IEC 60364-4"


def test_u2010_normalized_in_n1():
    assert n1("A‐B") == "A-B"


def test_supplementary_hyphen_is_preserved():
    # 'Erzeugungs- und Speichereinheit' must NOT be joined
    assert n1("Erzeugungs‑ und Speichereinheit") == "Erzeugungs- und Speichereinheit"
    assert n1("Erzeugungs- und Speichereinheit") == "Erzeugungs- und Speichereinheit"


def test_hyphenation_is_undone():
    assert n1("Wort- fortsetzung") == "Wortfortsetzung"


def test_soft_hyphen_removed():
    assert n1("Wort­teil") == "Wortteil"


def test_ligatures():
    assert n1("eﬃzient") == "effizient"     # ﬃ → ffi
    assert n1("ﬁx") == "fix"                  # ﬁ → fi


def test_n1_keeps_en_dash_n2_normalizes():
    assert "–" in n1("a – b")           # en dash kept in N1
    assert n2("a – b") == "a - b"            # N2 is the first to unify dashes


def test_n2_collapses_whitespace():
    assert n2("a   b\t c") == "a b c"


def test_n3_golden():
    assert n3("50-Hz-Netz") == "50hznetz"
    assert n3("Wert 1.234,56 kV") == "wert 1234 56 kv"


def test_empty():
    assert n1("") == ""
    assert n1(None) == ""
