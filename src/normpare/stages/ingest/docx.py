"""
ingest_docx.py -- DOCX -> norm_doc.json (greenfield).

Specifics:
- Heading numbers are counted here (Word auto-numbering is not in the text): main body
  1..n via outlineLvl styles, annexes via ANNEX* styles with letters (an explicit
  "Anhang X" in the title takes precedence).
- Track changes: accepted state (w:ins counts, w:del does not); ins/del are counted per
  paragraph and exported document-wide with author/date.
- Annex classification normative/informative from the title ("(normativ)"/"(informativ)").
- Captions (incl. complex SEQ fields), tables as a cell matrix, images in assets/,
  formulas (OMML) linearized + optional LaTeX via pandoc; every asset hangs off its paragraph.
"""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from lxml import etree

from . import SKIP_TITLES, VORSPANN_TITLES

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
V_IMG = "{urn:schemas-microsoft-com:vml}imagedata"

# Main-body headings: German FNN styles ("berschrift{n}") and standard English styles
# ("Heading{n}"): documents converted by different tools use either naming, and matching
# both is required: a document converted by a different tool may carry either naming.
_HEAD_NAME_RE = re.compile(r"^(?:berschrift|heading)\s*([1-9])$", re.I)
_HEAD_ANNEX_TITLE = {"ANNEXtitle", "ANNEX", "ANNEXZ", "Anhang1"}
_HEAD_ANNEX_SUB = {f"ANNEX-heading{i}": i for i in range(1, 6)}
_HEAD_ANNEX_SUB.update({f"Anhang{i}": i - 1 for i in range(2, 6)})  # Anhang2 = Ebene 1 unter Anhang
_SKIP_STYLES_PREFIX = ("Verzeichnis", "Inhaltsverzeichnis", "TOC", "Kopfzeile", "Fuzeile")
_CAPTION_STYLES = {"Beschriftung", "Caption", "TABLE-title", "FIGURE-title"}
_CAPTION_RE = re.compile(r"^\s*(Tabelle|Bild|Abbildung)\s+([A-Z]?\.?\d+)\s*[–—\-:]?\s*(.*)", re.S)


def _main_heading_level(sid) -> int | None:
    """Return the 1-based main-heading level for a paragraph style id, or None.

    Matches the German FNN styles (``berschrift{n}``) and the standard English styles
    (``heading{n}``). The Word outline level is intentionally NOT used as a fallback: in
    the FNN template the term/definition paragraphs (section 3, "Begriffe") also carry an
    outline level, so that would over-segment them. Style-name detection stays regression-free.
    """
    if not sid:
        return None
    m = _HEAD_NAME_RE.match(sid)
    return int(m.group(1)) if m else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _has_pandoc() -> bool:
    return shutil.which("pandoc") is not None


def _omml_to_latex(omml_xml: bytes) -> str | None:
    """OMML -> LaTeX via pandoc (if available), else None."""
    try:
        doc = (b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
               b'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
               b"<w:body><w:p>" + omml_xml + b"</w:p></w:body></w:document>")
        r = subprocess.run(["pandoc", "-f", "docx+empty_paragraphs", "-t", "latex"],
                           input=doc, capture_output=True, timeout=20)
        # pandoc expects a real docx zip -- otherwise the fallback below applies
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode("utf-8", "replace").strip()
    except Exception:
        pass
    return None


def _math_linear(el) -> str:
    """Linearized formula text from OMML (m:t contents with structural hints)."""
    parts = []
    for t in el.iter(M + "t"):
        parts.append(t.text or "")
    return "".join(parts)


class _State:
    def __init__(self):
        self.counters = [0] * 10          # main-body counter per level
        self.annex_letter = None          # current annex (letter)
        self.annex_counters = [0] * 8
        self.annex_norm = None            # "anhang_normativ" | "anhang_informativ"
        self.next_annex_ord = 0           # A, B, C, ...


def _accepted_text(p) -> str:
    """Text in the accepted state: normal runs + w:ins, without w:del/w:instrText."""
    out = []
    for node in p.iter():
        tag = node.tag
        if tag == W + "t":
            # inside a deleted region? (w:delText has its own tag, but just to be safe)
            anc = node.getparent()
            deleted = False
            while anc is not None and anc is not p:
                if anc.tag == W + "del":
                    deleted = True
                    break
                anc = anc.getparent()
            if not deleted:
                out.append(node.text or "")
        elif tag == W + "tab":
            out.append("\t")
        elif tag == W + "br":
            out.append("\n")
        elif tag == W + "noBreakHyphen":
            out.append("‑")            # U+2011 -- otherwise "Einheiten- und" would become "Einheitenund"
        elif tag == W + "softHyphen":
            pass                        # soft hyphen: no text content
    return "".join(out)


def _tc_counts(p) -> tuple[int, int, list]:
    ins_n = del_n = 0
    details = []
    for node in p.iter():
        if node.tag == W + "ins":
            txt = "".join(t.text or "" for t in node.iter(W + "t"))
            ins_n += 1
            details.append({"kind": "ins", "text": txt,
                            "author": node.get(W + "author"), "date": node.get(W + "date")})
        elif node.tag == W + "del":
            txt = "".join(t.text or "" for t in node.iter(W + "delText"))
            del_n += 1
            details.append({"kind": "del", "text": txt,
                            "author": node.get(W + "author"), "date": node.get(W + "date")})
    return ins_n, del_n, details


def _style_of(p) -> str | None:
    ps = p.find(f"{W}pPr/{W}pStyle")
    return ps.get(W + "val") if ps is not None else None


def _cell_text(tc) -> str:
    return "\n".join(_accepted_text(p).strip() for p in tc.iter(W + "p")).strip()


def read_docx(path: str | Path, out_dir: str | Path, doc_id: str, title: str,
              version_label: str) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    assets_img = out_dir / "assets" / "images"
    assets_img.mkdir(parents=True, exist_ok=True)

    z = zipfile.ZipFile(path)
    body = etree.fromstring(z.read("word/document.xml")).find(W + "body")
    styles = etree.fromstring(z.read("word/styles.xml"))
    # rels for images
    rels = {}
    try:
        relroot = etree.fromstring(z.read("word/_rels/document.xml.rels"))
        for rel in relroot:
            rels[rel.get("Id")] = rel.get("Target")
    except KeyError:
        pass

    # style -> outlineLvl (with basedOn resolution)
    raw = {}
    for st in styles.iter(W + "style"):
        sid = st.get(W + "styleId")
        out_el = st.find(f"{W}pPr/{W}outlineLvl")
        based = st.find(W + "basedOn")
        raw[sid] = (out_el.get(W + "val") if out_el is not None else None,
                    based.get(W + "val") if based is not None else None)

    st8 = _State()
    sections: list[dict] = []
    doc_changes: list[dict] = []
    footnote_count = len(list(etree.fromstring(z.read("word/footnotes.xml")).iter(W + "footnote"))) \
        if "word/footnotes.xml" in z.namelist() else 0

    term_state = {"top": 0, "sub": 0, "pending": None}   # Word auto-numbering of terms

    def new_section(sec_id, sec_title, level, part):
        sec = {"id": sec_id, "title": sec_title, "level": level, "part": part,
               "paragraphs": [], "tables": [], "figures": [], "formulas": []}
        sections.append(sec)
        term_state["top"] = term_state["sub"] = 0
        term_state["pending"] = None
        return sec

    cur = new_section("vorspann", "Vorspann", 0, "vorspann")
    img_n = tab_n = eq_n = 0
    pending_caption = None      # caption BEFORE the asset ("Tabelle N - ..." often precedes it)

    def para_id() -> str:
        return f"{cur['id']}.p{len(cur['paragraphs']) + 1}"

    for el in body:
        # ------------------------------------------------ Tables
        if el.tag == W + "tbl":
            tab_n += 1
            rows = []
            for tr in el.findall(W + "tr"):
                rows.append([_cell_text(tc) for tc in tr.findall(W + "tc")])
            tid = f"{doc_id}_tab_{tab_n:03d}"
            # capture formulas inside the table (often parameter tables)
            for mo in el.findall(f".//{M}oMath"):
                eq_n += 1
                cur["formulas"].append({"id": f"{doc_id}_eq_{eq_n:03d}",
                                        "linear": _math_linear(mo), "latex": None,
                                        "para_anchor": None, "table_anchor": tid})
            cur["tables"].append({
                "id": tid,
                "caption": pending_caption[1] if pending_caption and pending_caption[0] == "Tabelle" else None,
                "n_rows": len(rows), "n_cols": max((len(r) for r in rows), default=0),
                "cells": rows,
                "para_anchor": cur["paragraphs"][-1]["id"] if cur["paragraphs"] else None})
            if pending_caption and pending_caption[0] == "Tabelle":
                pending_caption = None
            continue
        if el.tag != W + "p":
            continue

        sid = _style_of(el)
        if sid and sid.startswith(_SKIP_STYLES_PREFIX):
            continue
        text = _accepted_text(el)
        t_strip = re.sub(r"\s+", " ", text).strip()

        # Reproduce term numbering (Word auto-numbering is not in the text):
        # TERM-number = 3.1.N, TERM-number3 = 3.1.N.M -- the number attaches to the next
        # term-name paragraph (Term0/Begriff) or to this paragraph itself.
        if sid == "TERM-number":
            term_state["top"] += 1
            term_state["sub"] = 0
            term_state["pending"] = f"{cur['id']}.{term_state['top']}"
        elif sid in ("TERM-number3", "TERM-number 3"):
            term_state["sub"] += 1
            term_state["pending"] = f"{cur['id']}.{term_state['top']}.{term_state['sub']}"

        # ------------------------------------------------ Headings
        main_lvl = _main_heading_level(sid)
        is_main = main_lvl is not None
        is_annex_t = sid in _HEAD_ANNEX_TITLE
        is_annex_s = sid in _HEAD_ANNEX_SUB
        if is_main or is_annex_t or is_annex_s:
            if not t_strip:
                continue        # deleted/empty heading -> no section
            low_title = t_strip.lower()
            if is_main and low_title in SKIP_TITLES:
                # "Inhalt", "Bilder", "Tabellen": navigation, not a chapter. They carry a
                # heading style, so without this they would take a number of their own.
                continue
            if is_main and low_title in VORSPANN_TITLES:
                # Front matter has no chapter number in the standard, so it must not touch
                # the counter -- otherwise every following chapter is shifted. Reading the
                # 2023 edition as DOCX put "Begriffe" at 7 instead of 3 for exactly this
                # reason. Same shape as the PDF reader, which files it under vorspann.
                cur = new_section(f"vorspann.{low_title}", t_strip, 1, "vorspann")
                continue
            if is_main:
                lvl = main_lvl
                st8.counters[lvl - 1] += 1
                for i in range(lvl, 10):
                    st8.counters[i] = 0
                sec_id = ".".join(str(c) for c in st8.counters[:lvl] if c > 0)
                cur = new_section(sec_id, t_strip, lvl, "hauptteil")
            elif is_annex_t:
                if "literatur" in t_strip.lower():
                    cur = new_section("Literatur", t_strip, 1, "literatur")
                    continue
                m = re.search(r"Anhang\s+([A-Z])", t_strip)
                if m:
                    st8.annex_letter = m.group(1)
                    st8.next_annex_ord = max(st8.next_annex_ord, ord(m.group(1)) - 64)
                else:
                    st8.next_annex_ord += 1
                    st8.annex_letter = chr(64 + st8.next_annex_ord)
                st8.annex_counters = [0] * 8
                low = t_strip.lower()
                st8.annex_norm = ("anhang_normativ" if "(normativ" in low
                                  else "anhang_informativ" if "(informativ" in low else "anhang_normativ")
                clean = re.sub(r"^Anhang\s+[A-Z]\s*", "", t_strip)
                clean = re.sub(r"\((?:normativ|informativ)\)\s*", "", clean).strip() or t_strip
                cur = new_section(st8.annex_letter, clean, 1, st8.annex_norm)
            else:
                lvl = _HEAD_ANNEX_SUB[sid]
                if st8.annex_letter is None:      # safety net
                    st8.next_annex_ord += 1
                    st8.annex_letter = chr(64 + st8.next_annex_ord)
                    st8.annex_norm = "anhang_normativ"
                st8.annex_counters[lvl - 1] += 1
                for i in range(lvl, 8):
                    st8.annex_counters[i] = 0
                sec_id = st8.annex_letter + "." + ".".join(
                    str(c) for c in st8.annex_counters[:lvl] if c > 0)
                cur = new_section(sec_id, t_strip, lvl + 1, st8.annex_norm)
            continue

        # ------------------------------------------------ Paragraph formulas
        maths = el.findall(f".//{M}oMath") + el.findall(f".//{M}oMathPara")
        formula_ids = []
        for mo in maths:
            if mo.tag == M + "oMathPara":
                continue  # contains oMath children that arrive separately
            eq_n += 1
            fid = f"{doc_id}_eq_{eq_n:03d}"
            cur["formulas"].append({"id": fid, "linear": _math_linear(mo),
                                    "latex": None,
                                    "para_anchor": para_id()})
            formula_ids.append(fid)

        # ------------------------------------------------ Paragraph images
        figure_ids = []
        # OLE formulas (Equation Editor) with a VML preview image -> capture as a formula image
        for vimg in el.iter(V_IMG):
            rid = vimg.get(R + "id")
            target = rels.get(rid)
            if not target:
                continue
            eq_n += 1
            fid = f"{doc_id}_eq_{eq_n:03d}"
            src = "word/" + target.lstrip("/") if not target.startswith("word/") else target
            ext = Path(target).suffix or ".bin"
            try:
                (assets_img / f"{fid}{ext}").write_bytes(z.read(src))
                ipath = f"assets/images/{fid}{ext}"
            except KeyError:
                ipath = None
            cur["formulas"].append({"id": fid, "linear": None, "latex": None,
                                    "image_path": ipath, "para_anchor": para_id()})
            formula_ids.append(fid)
        for blip in el.iter(A_BLIP):
            rid = blip.get(R + "embed")
            target = rels.get(rid)
            if not target:
                continue
            img_n += 1
            fid = f"{doc_id}_fig_{img_n:03d}"
            src = "word/" + target.lstrip("/") if not target.startswith("word/") else target
            ext = Path(target).suffix or ".bin"
            try:
                (assets_img / f"{fid}{ext}").write_bytes(z.read(src))
                ipath = f"assets/images/{fid}{ext}"
            except KeyError:
                ipath = None
            cur["figures"].append({"id": fid, "caption": None, "image_path": ipath,
                                   "para_anchor": para_id()})
            figure_ids.append(fid)

        # ------------------------------------------------ Captions
        cm = _CAPTION_RE.match(t_strip) if t_strip else None
        if (sid in _CAPTION_STYLES or cm) and t_strip:
            if cm:
                kind, num, rest = cm.group(1), cm.group(2), cm.group(3).strip()
                cap = f"{kind} {num} – {rest}" if rest else f"{kind} {num}"
                if kind == "Tabelle":
                    # caption usually precedes the table
                    pending_caption = ("Tabelle", cap)
                else:
                    # figure caption usually follows the image
                    for fig in reversed(cur["figures"]):
                        if fig["caption"] is None:
                            fig["caption"] = cap
                            break
                    else:
                        pending_caption = ("Bild", cap)
                continue
            # caption style without a pattern: assign to the last asset
            assigned = False
            for coll in (cur["figures"], cur["tables"]):
                for a in reversed(coll):
                    if a.get("caption") is None:
                        a["caption"] = t_strip
                        assigned = True
                        break
                if assigned:
                    break
            continue

        # ------------------------------------------------ Normal paragraphs
        ins_n, del_n, details = _tc_counts(el)
        if not t_strip and not formula_ids:
            continue
        kind = ("note" if re.match(r"^\s*(ANMERKUNG|BEISPIEL)", t_strip)
                else "term" if sid and (sid.upper().startswith("TERM") or sid.startswith("Begriff"))
                else "formula" if formula_ids and len(t_strip) < 5
                else "list" if el.find(f"{W}pPr/{W}numPr") is not None
                else "text")
        pid = para_id()
        rec_p = {"id": pid, "n0": text, "kind": kind,
                 "tc": {"ins": ins_n, "del": del_n},
                 "formulas": formula_ids, "figures": figure_ids}
        if kind == "term" and term_state["pending"]:
            rec_p["term_no"] = term_state["pending"]
            term_state["pending"] = None
        cur["paragraphs"].append(rec_p)
        for dtl in details:
            doc_changes.append({"section_id": cur["id"], "paragraph_id": pid, **dtl})

    # drop leftover pending figure captions; keep empty sections
    meta = {
        "doc_id": doc_id, "title": title, "version_label": version_label,
        "source": {"filename": path.name, "sha256": _sha256(path), "format": "docx",
                   "pages": None, "footnotes": footnote_count},
        "sections": sections,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "norm_doc.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    with open(out_dir / "track_changes.jsonl", "w", encoding="utf-8") as f:
        for c in doc_changes:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return meta
