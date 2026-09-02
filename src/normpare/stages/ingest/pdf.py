"""
ingest_pdf.py -- PDF -> norm_doc.json (greenfield, TOC-first).

Strategy:
1. The embedded table of contents (get_toc) is the structural anchor: section ids,
   titles, level, start page.
2. Headers/footers are detected via repetition patterns and removed.
3. Paragraphs are reconstructed from layout lines (gap/bullet/indent heuristics);
   hyphenation is undone during normalization (N1).
4. Tables via page.find_tables(); figures via image rects; captions via
   "Tabelle/Bild N -" lines. Formula lines are marked by a symbol-density heuristic.
Every paragraph carries a page number and provenance "pdf" (more conservative alignment
confidence).
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

from . import SKIP_TITLES, VORSPANN_TITLES
from ...text.captions import is_caption_rest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_TOC_MAIN = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")
_TOC_ANNEX = re.compile(r"^Anhang\s+([A-Z])\s*(?:\((normativ|informativ)\))?\s*[–—\-:]?\s*(.*)$")
_TOC_ANNEX_SUB = re.compile(r"^([A-Z])\.(\d+(?:\.\d+)*)\s+(.*)$")
# Front matter and the navigation lists live in the package: the DOCX reader has to agree
# with this one. Running headers (the standard's own designation repeated on every page)
# are filtered generically in `_is_skip_toc` by matching the document's own title, so no
# standard-specific names are hard-coded here.
_VORSPANN = VORSPANN_TITLES
_SKIP_TOC = SKIP_TITLES


def _is_skip_toc(line: str, doc_title: str = "") -> bool:
    """True for TOC lines that are not chapters: fixed boilerplate, or a running header
    repeating the document's own designation (e.g. the standard's number)."""
    s = (line or "").strip().lower()
    if not s or s in _SKIP_TOC:
        return True
    t = (doc_title or "").strip().lower()
    return bool(t) and (s == t or (len(s) > 3 and s in t))

_CAPTION_LINE = re.compile(r"^\s*(Tabelle|Bild|Abbildung)\s+([A-Z]?\.?\d+(?:\.\d+)*[a-z]?)(.*)$")
_BULLET = re.compile(r"^\s*(?:[–—•\-]\s+|[a-z]\)\s+|\d+\)\s+|[ivx]+\.\s+)")
_FORMULA_CHARS = set("=≤≥<>±×⋅·∙√∑∏∫Δφϕαβγητωπ∞≈")
#: A line that ends here ends a sentence -- used both for the continuation of a paragraph
#: and for the continuation of a caption.
_SENTENCE_END = re.compile(r"[.!?:;]\s*$")
#: A line that consists of nothing but this is a page number, not a statement.
_PAGE_NUMBER = re.compile(r"\d{1,4}")
#: A caption may run over at most this many layout lines. Beyond that the join is given
#: up: a caption of four lines is more likely a caption followed by body text than a
#: caption (AP-39).
MAX_CAPTION_LINES = 3


def _caption_line(text: str):
    """The caption match of a layout line, or ``None`` if the line is not a caption.

    The keyword and the number alone do not make a caption: without a separator behind the
    number the line is the tail of a sentence ("... nach Tabelle 10 empfohlen."), which
    used to displace the real caption of the table next to it. The rule is
    :func:`normpare.text.captions.is_caption_rest`, the same one the table comparison
    applies.
    """
    m = _CAPTION_LINE.match(text)
    return m if m and is_caption_rest(m.group(3)) else None


def _large_gap(prev: dict, ln: dict) -> bool:
    """True if the line spacing is too large for the two lines to belong together."""
    return ln["y"] - prev["y"] > ln["size"] * 1.7


def _join_caption(lines: list[dict], i: int, in_table=None) -> tuple[str, int, bool]:
    """A caption over several layout lines, starting at ``lines[i]``.

    The caption branch used to take exactly one line, so the rest of a caption fell
    through into the ordinary paragraph logic and became body text ("Bild 21 -
    Schutzkonzept bei Anschluss von Erzeugungsanlagen an" + a paragraph "die
    Sammelschiene eines Umspannwerks"). A following line is read on while it is
    recognizably part of the caption -- same page, small gap, no sentence end so far, no
    bullet, no number of its own, and not the beginning of the next caption.

    Returns the caption text, the index of the first line that does *not* belong to it
    any more, and whether the join was given up at :data:`MAX_CAPTION_LINES`.
    """
    text = re.sub(r"\s+", " ", lines[i]["text"]).strip()
    j = i + 1
    while j < len(lines) and not _SENTENCE_END.search(text):
        ln, prev = lines[j], lines[j - 1]
        t = ln["text"].strip()
        if (in_table is not None and in_table(ln)) or ln["page"] != prev["page"] \
                or _large_gap(prev, ln) or not t or _BULLET.match(t) \
                or _PAGE_NUMBER.fullmatch(t) or _caption_line(t):
            break
        if j - i >= MAX_CAPTION_LINES:
            return text, j, True
        text = (text + " " + re.sub(r"\s+", " ", t)).strip()
        j += 1
    return text, j, False


def _formula_like(text: str, fonts: set[str]) -> bool:
    if not text:
        return False
    from ...text.textnorm import garbage_ratio
    t = text.strip()
    n_vis = sum(1 for c in t if not c.isspace())
    gr = garbage_ratio(t)
    if gr > 0.15 or (gr > 0 and gr * n_vis >= 2 and gr > 0.06):
        return True                     # broken ToUnicode map (formula glyphs)
    sym = sum(1 for c in t if c in _FORMULA_CHARS)
    # equation number "(N)" at line end + short/symbol-heavy line => formula line
    if re.search(r"\(\d{1,3}\)\s*$", t) and (sym >= 1 or gr > 0 or len(t) < 80):
        return True
    alpha = sum(1 for c in t if c.isalpha())
    italic_math = any("Italic" in f or "Symbol" in f or "Math" in f for f in fonts)
    return (sym >= 2 and sym >= 0.04 * max(len(t), 1)) or (italic_math and sym >= 1 and alpha < 40)


def _parse_toc(doc, doc_title: str = "") -> list[dict]:
    """TOC -> section skeleton [{id,title,level,part,page}] in document order."""
    out, seen = [], set()
    annex_letter = None
    annex_part = "anhang_normativ"
    for lvl, title, page in doc.get_toc():
        t = re.sub(r"\s+", " ", title).strip()
        low = t.lower()
        if page <= 0 or _is_skip_toc(t, doc_title):
            continue
        rec = None
        m = _TOC_MAIN.match(t)
        ma = _TOC_ANNEX.match(t)
        ms = _TOC_ANNEX_SUB.match(t)
        if ma:
            annex_letter = ma.group(1)
            annex_part = f"anhang_{ma.group(2)}" if ma.group(2) else "anhang_normativ"
            rec = {"id": annex_letter, "title": ma.group(3) or t, "level": 1,
                   "part": annex_part, "page": page}
        elif ms and annex_letter and ms.group(1) == annex_letter:
            rec = {"id": f"{ms.group(1)}.{ms.group(2)}", "title": ms.group(3),
                   "level": 1 + ms.group(2).count(".") + 1, "part": annex_part, "page": page}
        elif m and annex_letter is None:
            sid = m.group(1)
            rec = {"id": sid, "title": m.group(2), "level": sid.count(".") + 1,
                   "part": "hauptteil", "page": page}
        elif low.startswith("literatur"):
            rec = {"id": "Literatur", "title": t, "level": 1, "part": "literatur", "page": page}
        elif low in _VORSPANN:
            rec = {"id": f"vorspann.{low}", "title": t, "level": 1, "part": "vorspann", "page": page}
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def _line_text(spans) -> str:
    """A line's text in reading order.

    PyMuPDF returns the spans of a line in content-stream order, which is the order the
    glyphs are drawn in, not the order they are read in: in DIN EN 60909-0:2016 the
    subscript of i_p is drawn before its base glyph, so a plain join yields "pi"
    (likewise "Gf R" for R_Gf, "kI" for I_k). Sorting by x restores the reading order.
    The sort is stable, so spans at the same x keep the order PyMuPDF delivered them in.
    """
    return "".join(s["text"] for s in sorted(spans, key=lambda s: s["bbox"][0]))


def _collect_lines(doc) -> tuple[list[dict], set[str]]:
    """All text lines with position/font; detects headers/footers via repetition."""
    lines = []
    freq = Counter()
    for pno in range(doc.page_count):
        page = doc[pno]
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                text = _line_text(ln["spans"])
                if not text.strip():
                    continue
                y0 = ln["bbox"][1]
                sizes = [s["size"] for s in ln["spans"]]
                fonts = {s["font"] for s in ln["spans"]}
                bold = any(s["flags"] & 16 for s in ln["spans"])
                rec = {"page": pno + 1, "y": y0, "x": ln["bbox"][0], "x1": ln["bbox"][2],
                       "size": max(sizes), "bold": bold, "fonts": fonts, "text": text}
                lines.append(rec)
                if y0 < 0.07 * h or y0 > 0.93 * h:
                    key = re.sub(r"\d+", "#", text.strip())[:60]
                    freq[key] += 1
    repeat = {k for k, n in freq.items() if n >= doc.page_count * 0.3}
    return lines, repeat


def _is_headfoot(line: dict, repeat: set[str], page_h: float) -> bool:
    t = line["text"].strip()
    if not t:
        return True
    edge = line["y"] < 0.07 * page_h or line["y"] > 0.93 * page_h
    if edge and (re.sub(r"\d+", "#", t)[:60] in repeat or re.fullmatch(r"[–\-\s]*\d+[–\-\s]*", t)):
        return True
    return False


def _find_heading_pos(lines, start_idx, sec, body_size) -> int | None:
    """Index of the heading line for section sec (from start_idx, on the start page +/- 1)."""
    tid = sec["id"]
    title_head = re.sub(r"\s+", " ", sec["title"]).strip()[:24].lower()
    pat_num = None
    if sec["part"] == "hauptteil" or re.match(r"^[A-Z](\.|$)", tid):
        pat_num = re.compile(r"^\s*" + re.escape(tid) + r"(\s+|$)")
    for i in range(start_idx, len(lines)):
        ln = lines[i]
        if ln["page"] > sec["page"] + 1:
            break
        if ln["page"] < sec["page"]:
            continue
        t = re.sub(r"\s+", " ", ln["text"]).strip()
        low = t.lower()
        if pat_num and pat_num.match(t):
            rest = pat_num.sub("", t).strip().lower()
            if not title_head or rest.startswith(title_head[: max(4, len(rest) or 4)]) \
               or title_head.startswith(rest[:8]) or ln["bold"] or ln["size"] > body_size:
                return i
        elif tid.startswith("vorspann") or tid == "Literatur":
            if low.startswith(title_head[:12]) and (ln["bold"] or ln["size"] > body_size):
                return i
        elif tid.isalpha() and len(tid) == 1:   # annex title page
            if low.startswith("anhang " + tid.lower()):
                return i
    return None


def read_pdf(path: str | Path, out_dir: str | Path, doc_id: str, title: str,
             version_label: str) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    (out_dir / "assets" / "images").mkdir(parents=True, exist_ok=True)
    doc = fitz.open(path)
    page_h = doc[0].rect.height

    skeleton = _parse_toc(doc, title)
    lines, repeat = _collect_lines(doc)
    lines = [l for l in lines if not _is_headfoot(l, repeat, page_h)]
    body_size = Counter(round(l["size"]) for l in lines).most_common(1)[0][0]
    # first and last line of a page: only there is a page number printed
    for i, l in enumerate(lines):
        l["page_edge"] = (i == 0 or lines[i - 1]["page"] != l["page"]
                          or i == len(lines) - 1 or lines[i + 1]["page"] != l["page"])

    # determine heading positions (monotonically increasing)
    idx = 0
    for sec in skeleton:
        pos = _find_heading_pos(lines, idx, sec, body_size)
        sec["_pos"] = pos
        if pos is not None:
            idx = pos + 1
    # sections without a detected heading: start = first line of their start page
    for k, sec in enumerate(skeleton):
        if sec["_pos"] is None:
            for i, ln in enumerate(lines):
                if ln["page"] >= sec["page"]:
                    sec["_pos"] = i
                    break
            else:
                sec["_pos"] = len(lines)

    # collect tables & figures per page (table regions are removed from the
    # paragraph text stream -- otherwise cell lines flood the paragraphs)
    tables_by_page: dict[int, list] = {}
    figures_by_page: dict[int, list] = {}
    table_bboxes: dict[int, list] = {}
    tab_n = fig_n = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        try:
            tf = page.find_tables()
            for t in tf.tables:
                cells = t.extract()
                n_rows = len(cells)
                n_cols = max((len(r) for r in cells), default=0)
                if n_rows < 2 or n_cols < 2:
                    continue
                tab_n += 1
                tables_by_page.setdefault(pno + 1, []).append(
                    {"id": f"{doc_id}_tab_{tab_n:03d}", "caption": None,
                     "n_rows": n_rows, "n_cols": n_cols,
                     "cells": [[(c or "").strip() for c in row] for row in cells],
                     "y": t.bbox[1], "page": pno + 1, "para_anchor": None})
                table_bboxes.setdefault(pno + 1, []).append(t.bbox)
        except Exception:
            pass
        for img in page.get_image_info(xrefs=True):
            if img.get("width", 0) < 40 or img.get("height", 0) < 40:
                continue
            fig_n += 1
            figures_by_page.setdefault(pno + 1, []).append(
                {"id": f"{doc_id}_fig_{fig_n:03d}", "caption": None, "image_path": None,
                 "y": img["bbox"][1], "page": pno + 1, "para_anchor": None})

    # reconstruct paragraphs per section
    sections = []
    #: (index in `sections`, "tables"|"figures", asset) -- who gets which asset. The
    #: copies are only made when every section has been read: a caption is found in the
    #: paragraph pass and written into the asset dictionary, so on a page that straddles
    #: a section boundary a copy taken during the loop would still carry `caption: None`
    #: and the caption would never reach the artefact (AP-39a, finding B-1).
    asset_claims: list[tuple[int, str, dict]] = []
    captions_capped = 0     # captions whose join was given up at MAX_CAPTION_LINES
    # Term contexts: sections under a "Begriffe" chapter -- there the structure is
    # number + name(s) + explanation; name/symbol/synonym lines ("PAV, B",
    # "(FRT-Fähigkeit)") must become their own paragraphs.
    begriffe_roots = [s["id"] for s in skeleton if "begriff" in s["title"].lower()]

    def _term_ctx(sec_id: str) -> bool:
        return any(sec_id == r or sec_id.startswith(r + ".") for r in begriffe_roots)

    order = sorted(range(len(skeleton)), key=lambda k: skeleton[k]["_pos"])
    for oi, k in enumerate(order):
        sec = skeleton[k]
        start = sec["_pos"] + (1 if sec["_pos"] < len(lines) else 0)
        end = skeleton[order[oi + 1]]["_pos"] if oi + 1 < len(order) else len(lines)
        sec_lines = lines[start:end]
        out_sec = {"id": sec["id"], "title": sec["title"], "level": sec["level"],
                   "part": sec["part"], "paragraphs": [], "tables": [], "figures": [],
                   "formulas": []}

        paras: list[dict] = []
        buf, buf_meta = [], None
        prev = None
        pending_term = False
        pending_bullet = False      # "-" often appears in the PDF as its OWN line before the item text
        # Embedded sub-headings (not in the TOC): "3.1.11.2 vorübergehende
        # Betriebserlaubnis" -> drop the number, name becomes its own kind='term'
        # paragraph, the definition starts a new one. Hits term sub-items (structure:
        # number + heading + explanation) and intermediate headings.
        sub_head_re = None
        if re.match(r"^[0-9]+(\.[0-9]+)*$", sec["id"]):
            sub_head_re = re.compile(r"^(" + re.escape(sec["id"]) + r"(?:\.\d+)+)\s*(.*)$")

        def flush():
            nonlocal buf, buf_meta
            if buf:
                text = " ".join(buf).strip()
                if text:
                    meta = dict(buf_meta)
                    # formula detection on the WHOLE paragraph (equation lines often
                    # start with clean glyphs, garbage/"(N)" follows later)
                    if meta.get("kind") == "text" and _formula_like(text, set()):
                        meta["kind"] = "formula"
                    paras.append({"text": text, **meta})
            buf, buf_meta = [], None

        def _in_table(ln: dict) -> bool:
            return any(bb[1] - 2 <= ln["y"] <= bb[3] + 2
                       for bb in table_bboxes.get(ln["page"], []))

        li = 0
        while li < len(sec_lines):
            ln = sec_lines[li]
            li += 1
            # skip lines inside detected tables
            if _in_table(ln):
                continue
            t = ln["text"].rstrip()
            # bullet as its own layout line ("- "): treat as a prefix of the
            # next line -- otherwise whole lists merge into one paragraph
            if re.fullmatch(r"[–—\-•▪]\s*", t.strip()):
                pending_bullet = True
                prev = ln
                continue
            if pending_bullet:
                t = "– " + t.lstrip()
                pending_bullet = False
            if pending_term:                     # the name was on the following line
                flush()
                rec_t = {"text": t.strip(), "kind": "term", "page": ln["page"]}
                if isinstance(pending_term, str):
                    rec_t["term_no"] = pending_term
                paras.append(rec_t)
                pending_term = False
                prev = ln
                continue
            shm = sub_head_re.match(t.strip()) if sub_head_re else None
            if shm and (ln["bold"] or len(shm.group(2)) <= 90):
                flush()
                rest = shm.group(2).strip(" –—-:")
                if rest:
                    paras.append({"text": rest, "kind": "term", "page": ln["page"],
                                  "term_no": shm.group(1)})
                else:
                    pending_term = shm.group(1) or True
                prev = ln
                continue
            cm = _caption_line(t.strip())
            if cm and (ln["bold"] or len(t.strip()) < 120):
                flush()
                kind = cm.group(1)
                cap, li, capped = _join_caption(sec_lines, li - 1, _in_table)
                captions_capped += capped
                target_list = tables_by_page if kind == "Tabelle" else figures_by_page
                # find an asset on the same page near y
                best, bd = None, 1e9
                for a in target_list.get(ln["page"], []):
                    d = abs(a["y"] - ln["y"])
                    if a["caption"] is None and d < bd:
                        best, bd = a, d
                if best is not None:
                    best["caption"] = cap
                else:
                    paras.append({"text": cap, "kind": "caption", "page": ln["page"]})
                prev = sec_lines[li - 1]
                continue
            new_para = False
            if prev is None:
                new_para = True
            else:
                buf_text = " ".join(buf)
                continues = (buf_text and not _SENTENCE_END.search(buf_text)
                             and (t.strip()[:1].islower() or t.strip()[:1] in "(–-0123456789"))
                page_break = ln["page"] != prev["page"]
                gap = (not page_break) and _large_gap(prev, ln)
                if _BULLET.match(t) or re.match(r"^\s*(ANMERKUNG|BEISPIEL)", t):
                    new_para = True
                elif page_break:
                    new_para = not continues     # join the continuation across the page break
                elif gap:
                    new_para = True
            # page-number lines in the middle of content (a paragraph runs across the page
            # break and the header/footer filter misses narrow margins) -> drop them.
            # While a paragraph is open the line is a page number in any case; standing
            # between two paragraphs it is one when it is the first or the last line of
            # its page -- there and only there the page number is printed. A number in the
            # middle of a page belongs to the content: the legends of the annexes number
            # their entries that way ("1" + "Netzanschlusspunkt", AP-39).
            if (buf or ln.get("page_edge")) and _PAGE_NUMBER.fullmatch(t.strip()):
                prev = ln
                continue
            if buf_meta is None:
                new_para = True
            # term context: a short name/symbol/synonym line at the paragraph start
            # (no sentence end, no bullet) -> its own term paragraph
            if (new_para and _term_ctx(sec["id"])):
                t_s = t.strip()
                if (0 < len(t_s) <= 60 and not re.search(r"[.!?;:]\s*$", t_s)
                        and not _BULLET.match(t_s)
                        and not re.match(r"^\s*(ANMERKUNG|BEISPIEL|Anmerkung \d)", t_s)):
                    flush()
                    paras.append({"text": t_s, "kind": "term", "page": ln["page"]})
                    prev = ln
                    continue
            if new_para:
                flush()
                buf_meta = {"kind": ("note" if re.match(r"^\s*(ANMERKUNG|BEISPIEL)", t)
                                     else "list" if _BULLET.match(t)
                                     else "formula" if _formula_like(t, ln["fonts"])
                                     else "text"),
                            "page": ln["page"]}
            elif buf_meta and buf_meta["kind"] == "text" and _formula_like(t, ln["fonts"]):
                pass  # formula line inside a paragraph: keep the text, do not split
            buf.append(t.strip())
            prev = ln
        flush()

        # remove leftover heading: the first "paragraph" == section title (the number
        # was on its own line and detected as a heading, the title line remained)
        if paras:
            t_norm = re.sub(r"\s+", " ", paras[0]["text"]).strip().lower()
            title_norm = re.sub(r"\s+", " ", sec["title"]).strip().lower()
            if t_norm and (t_norm == title_norm or title_norm.startswith(t_norm[:40])
                           or t_norm.startswith(title_norm[:40])) and len(t_norm) <= len(title_norm) + 12:
                paras.pop(0)
            elif t_norm.startswith(title_norm) and len(title_norm) >= 8:
                # the title line merged with the first paragraph (common for term
                # sections): strip the title prefix
                raw = re.sub(r"\s+", " ", paras[0]["text"]).strip()
                paras[0]["text"] = raw[len(title_norm):].lstrip(" –—-:")

        eq_local = 0
        for i, p in enumerate(paras, 1):
            pid = f"{sec['id']}.p{i}"
            rec = {"id": pid, "n0": p["text"], "kind": p["kind"], "page": p["page"],
                   "tc": {"ins": 0, "del": 0}, "formulas": [], "figures": []}
            if p["kind"] == "formula":
                eq_local += 1
                fid = f"{doc_id}_eq_{sec['id']}_{eq_local}"
                out_sec["formulas"].append({"id": fid, "linear": p["text"], "latex": None,
                                            "para_anchor": pid})
                rec["formulas"] = [fid]
            out_sec["paragraphs"].append(rec)

        # claim the assets of this section's pages -- each asset for the first section
        # that touches its page, and for exactly one. Only the emission waits.
        pages_in_sec = {l["page"] for l in sec_lines}
        for pg in sorted(pages_in_sec):
            for key, by_page in (("tables", tables_by_page), ("figures", figures_by_page)):
                for a in by_page.get(pg, []):
                    if not a.get("_used"):
                        a["_used"] = True
                        asset_claims.append((len(sections), key, a))
        sections.append(out_sec)

    # every caption has been written by now: emit the assets in the order they were
    # claimed in (section, then page, then collection order)
    for si, key, a in asset_claims:
        sections[si][key].append({k2: v for k2, v in a.items()
                                  if k2 not in ("y", "_used")})

    if captions_capped:
        logging.getLogger(__name__).info(
            "%s: %d caption(s) run over more than %d lines and were cut there",
            path.name, captions_capped, MAX_CAPTION_LINES)

    meta = {"doc_id": doc_id, "title": title, "version_label": version_label,
            "source": {"filename": path.name, "sha256": _sha256(path), "format": "pdf",
                       "pages": doc.page_count},
            "sections": sections}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "norm_doc.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    return meta
