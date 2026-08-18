"""
html_annotated.py -- new version with colour-marked changes + chapter summaries.

Removed: red strikethrough; new: green; moved: blue. Per chapter a summary, the change
list in the order of the new version, real cell-diffed tables and embedded figures.
"""
from __future__ import annotations
import base64
import html
import json
from pathlib import Path

from ..text.textnorm import n3
from .axes import axis_marker

_CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;margin:0;color:#1a1a1a;line-height:1.5}
.layout{display:flex}
nav{width:280px;min-width:280px;height:100vh;overflow-y:auto;position:sticky;top:0;
    background:#f4f6f8;border-right:1px solid #d0d7de;padding:12px;box-sizing:border-box;font-size:13px}
nav a{display:block;color:#333;text-decoration:none;padding:2px 4px;border-radius:4px}
nav a:hover{background:#e2e8f0}
nav .l2{margin-left:12px}.l3{margin-left:24px}.l4{margin-left:36px}
main{flex:1;max-width:980px;padding:24px 40px;box-sizing:border-box}
h2.chap{border-bottom:2px solid #2a5d8f;color:#2a5d8f;margin-top:2.2em;padding-bottom:4px}
.badge{display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;margin-left:8px;vertical-align:middle}
.b-norm{background:#dbeafe;color:#1e40af}.b-info{background:#f3e8ff;color:#7e22ce}
.b-rel-high{background:#fee2e2;color:#b91c1c}.b-rel-medium{background:#fef9c3;color:#a16207}
.b-rel-low{background:#e5e7eb;color:#4b5563}
.b-new{background:#dcfce7;color:#15803d}.b-removed{background:#fee2e2;color:#b91c1c}
.summary{background:#f0f6ff;border-left:4px solid #2a5d8f;padding:10px 14px;margin:10px 0;font-size:14px}
.summary .lbl{font-weight:600;color:#2a5d8f}
.kw{font-size:12px;color:#555}
ins,.ins{background:#d3f9d8;color:#14532d;text-decoration:none;padding:0 1px}
del,.del{background:#ffe3e3;color:#b91c1c;padding:0 1px}
.para{margin:0.55em 0}
.p-new{background:#f0fdf4;border-left:3px solid #22c55e;padding:6px 10px}
.p-removed{background:#fef2f2;border-left:3px solid #ef4444;padding:6px 10px;color:#7f1d1d}
.p-removed .txt{text-decoration:line-through}
.p-moved{border-left:3px solid #3b82f6;padding:6px 10px;background:#eff6ff}
.meta{font-size:11px;color:#888}
.kennwert{background:#fff7ed;border:1px solid #fdba74;border-radius:4px;padding:1px 6px;font-size:12px;color:#9a3412;margin-left:6px}
.tbl-note,.fig-note{font-size:12px;color:#666;font-style:italic;margin:4px 0}
.deutung{font-size:13px;color:#374151;background:#fafaf9;border:1px dashed #d6d3d1;padding:6px 10px;margin:4px 0 10px}
.asset-deutung{font-size:13px;color:#374151;background:#fff7ed;border:1px solid #fdba74;border-radius:5px;padding:8px 12px;margin:6px 0}
.asset-deutung .kwlist{margin:4px 0 2px 0;padding-left:18px;color:#9a3412}
.asset-deutung .kwlist li{margin:1px 0}
.asset-deutung .asset-aus{color:#555;margin-top:3px}
.verb{display:inline-block;font-size:11px;font-weight:600;padding:0 7px;border-radius:9px;vertical-align:middle}
.v-hi{background:#fee2e2;color:#b91c1c}.v-lo{background:#dbeafe;color:#1e40af}
.axes{font-size:11px;color:#6b7280;font-family:ui-monospace,Menlo,Consolas,monospace}
.legend{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:8px 0;font-size:13px;z-index:5}
/* Echte Tabellen mit Zell-Diff */
table.ntab{border-collapse:collapse;margin:4px 0;font-size:12.5px}
table.ntab caption{caption-side:top;text-align:left;font-weight:600;color:#374151;padding:2px 0;font-size:12.5px}
table.ntab th,table.ntab td{border:1px solid #cbd5e1;padding:3px 7px;vertical-align:top;text-align:left;white-space:pre-wrap}
table.ntab tr:first-child td{background:#eef2f7;font-weight:600}
td.cell-chg{background:#fef08a}
.tabpair{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;margin:8px 0 4px}
.tabpair .side{flex:1 1 300px;min-width:0;overflow-x:auto}
.tabpair .side h5{margin:0 0 2px;font-size:11px;color:#6b7280;font-weight:700;letter-spacing:.03em}
.tbl-alt caption{color:#b91c1c}.tbl-neu caption{color:#15803d}
figure.nfig{margin:10px 0}
figure.nfig img{max-width:100%;height:auto;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
figure.nfig figcaption{font-size:12px;color:#666;font-style:italic;margin-top:3px}
/* Kennwert-Änderungsabschnitt */
#kennwerte{scroll-margin-top:40px}
#kwtab{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
#kwtab th,#kwtab td{border:1px solid #e5e7eb;padding:5px 8px;text-align:left;vertical-align:top}
#kwtab thead th{background:#2a5d8f;color:#fff;cursor:pointer;position:sticky;top:0;user-select:none}
#kwtab thead th:hover{background:#24507c}
#kwtab tbody tr:nth-child(even){background:#f8fafc}
#kwtab .kv-alt{color:#b91c1c;white-space:nowrap}#kwtab .kv-neu{color:#15803d;font-weight:600;white-space:nowrap}
#kwtab .src{font-size:10px;color:#94a3b8}
#kwfilter{padding:6px 10px;font-size:13px;width:min(340px,90%);border:1px solid #cbd5e1;border-radius:6px;margin-top:8px}
.kw-count{font-size:12px;color:#e0e7ef;font-weight:400;margin-left:8px}
"""


def _ops_html(ops) -> str:
    parts = []
    for op, seg in ops:
        seg = html.escape(seg)
        if op == "=":
            parts.append(seg)
        elif op == "+":
            parts.append(f"<ins>{seg}</ins>")
        else:
            parts.append(f"<del>{seg}</del>")
    return "".join(parts)


def _kennwerte_badges(c) -> str:
    kw = c.get("kennwerte") or {}
    out = []
    for ch in kw.get("changed", []):
        out.append(f'<span class="kennwert">{html.escape(ch["old"]["raw"])} → {html.escape(ch["new"]["raw"])}</span>')
    return "".join(out)


# ---------------------------------------------------------------------------
# real tables with cell diff
# ---------------------------------------------------------------------------
def _html_table(tbl: dict, changed: set | None = None, side: str = "") -> str:
    cells = tbl.get("cells") or []
    changed = changed or set()
    maxcols = max((len(r) for r in cells), default=1)
    rows = []
    for ri, row in enumerate(cells):
        tds = []
        if len(row) == 1 and maxcols > 1:
            cls = ' class="cell-chg"' if (ri, 0) in changed else ""
            tds.append(f'<td colspan="{maxcols}"{cls}>{html.escape(str(row[0]))}</td>')
        else:
            for ci, val in enumerate(row):
                cls = ' class="cell-chg"' if (ri, ci) in changed else ""
                tds.append(f"<td{cls}>{html.escape(str(val))}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    cap = html.escape(tbl.get("caption") or tbl.get("id") or "")
    return f'<table class="ntab tbl-{side}"><caption>{cap}</caption><tbody>{"".join(rows)}</tbody></table>'


def _cell_key(s) -> str:
    """Whitespace-insensitive comparison key -- PDF line breaks inside header cells must
    NOT count as a value change."""
    return "".join(n3(str(s)).split())


def _cell_changes(old_cells: list, new_cells: list) -> tuple[set, set]:
    """Positions (row, column) whose content differs materially."""
    chg_old, chg_new = set(), set()
    for ri in range(max(len(old_cells), len(new_cells))):
        orow = old_cells[ri] if ri < len(old_cells) else []
        nrow = new_cells[ri] if ri < len(new_cells) else []
        for ci in range(max(len(orow), len(nrow))):
            ov = orow[ci] if ci < len(orow) else None
            nv = nrow[ci] if ci < len(nrow) else None
            if ov is None and nv is not None and _cell_key(nv):
                chg_new.add((ri, ci))
            elif nv is None and ov is not None and _cell_key(ov):
                chg_old.add((ri, ci))
            elif ov is not None and nv is not None and _cell_key(ov) != _cell_key(nv):
                chg_old.add((ri, ci)); chg_new.add((ri, ci))
    return chg_old, chg_new


def _render_tablediff(entry: dict, old_map: dict, new_map: dict) -> str:
    kind = entry.get("kind")
    moved = ""
    if kind == "moved_in":
        # AP-23: at its new place a moved table is shown like a paired one, plus its origin
        moved = ('<div class="tbl-note">Tabelle aus Kapitel '
                 f'{html.escape(str(entry.get("moved_from_chapter")))} verschoben.</div>')
        kind = "matched"
    if kind == "matched":
        ot, nt = old_map.get(entry.get("old")), new_map.get(entry.get("new"))
        if not nt:
            return ""
        if entry.get("identical") or not ot:
            return (moved + '<div class="tabpair"><div class="side">'
                    '<h5>TABELLE (inhaltlich unverändert)</h5>'
                    + _html_table(nt, side="neu") + "</div></div>")
        # cell marking only for structurally equal tables (otherwise over-marking)
        same_grid = (ot.get("n_rows") == nt.get("n_rows") and ot.get("n_cols") == nt.get("n_cols"))
        note = ""
        if same_grid:
            chg_old, chg_new = _cell_changes(ot.get("cells") or [], nt.get("cells") or [])
        else:
            chg_old, chg_new = set(), set()
            note = ('<div class="tbl-note">Struktur geändert: '
                    f'{ot.get("n_rows")}×{ot.get("n_cols")} → {nt.get("n_rows")}×{nt.get("n_cols")} '
                    '(Zellen nicht einzeln markiert — siehe KI-Kennwertdeutung).</div>')
        return (moved + note + '<div class="tabpair">'
                '<div class="side"><h5>ALT</h5>' + _html_table(ot, chg_old, "alt") + "</div>"
                '<div class="side"><h5>NEU</h5>' + _html_table(nt, chg_new, "neu") + "</div></div>")
    if kind == "new":
        nt = new_map.get(entry.get("new"))
        return ('<div class="tabpair"><div class="side">'
                '<h5><span class="badge b-new">NEUE TABELLE</span></h5>'
                + _html_table(nt, side="neu") + "</div></div>") if nt else ""
    if kind == "removed":
        ot = old_map.get(entry.get("old"))
        return ('<div class="tabpair"><div class="side">'
                '<h5><span class="badge b-removed">ENTFALLENE TABELLE</span></h5>'
                + _html_table(ot, side="alt") + "</div></div>") if ot else ""
    return ""


_IMG_MAXW = 1200  # cap image width -> compact, offline-capable HTML


def _img_data_uri(fid: str, img_dir: Path) -> tuple[str | None, str | None]:
    """Pick the best raster variant, cap at _IMG_MAXW, embed as PNG. The order prefers
    already browser-capable formats; TIFF via Pillow."""
    _direct = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}
    for ext in ("png", "jpg", "jpeg", "gif", "tiff", "tif"):
        p = img_dir / f"{fid}.{ext}"
        if not p.exists():
            continue
        try:
            from io import BytesIO
            from PIL import Image
            im = Image.open(p)
            if getattr(im, "mode", "") not in ("RGB", "RGBA", "L", "P"):
                im = im.convert("RGBA")
            if im.width > _IMG_MAXW:
                im.thumbnail((_IMG_MAXW, 10 * _IMG_MAXW))
            buf = BytesIO()
            im.save(buf, "PNG", optimize=True)
            return "image/png", base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            if ext in _direct:               # Pillow error -> embed raw bytes
                try:
                    return _direct[ext], base64.b64encode(p.read_bytes()).decode("ascii")
                except Exception:
                    pass
    return None, None


def _fig_embed(fig: dict, img_dir: Path | None) -> str:
    cap = html.escape(fig.get("caption") or fig["id"])
    if img_dir:
        mime, b64 = _img_data_uri(fig["id"], img_dir)
        if b64:
            return (f'<figure class="nfig"><img alt="{cap}" '
                    f'src="data:{mime};base64,{b64}"><figcaption>{cap}</figcaption></figure>')
    return f'<div class="fig-note">🖼 {cap} <span class="meta">(Vektorgrafik — siehe Original)</span></div>'


# ---------------------------------------------------------------------------
# parameter-value change extract
# ---------------------------------------------------------------------------
_UNIT_LABEL = {"%": "Wert [%]", "Hz": "Frequenz [Hz]", "s": "Zeit [s]",
               "W": "Leistung [W]", "VA": "Scheinleistung [VA]", "V": "Spannung [V]",
               "A": "Strom [A]", "var": "Blindleistung [var]", "Wh": "Energie [Wh]"}


def _parse_kw_string(s: str) -> tuple[str, str, str]:
    """'Name: old -> new' or 'Name: value' -> (name, old, new)."""
    s = (s or "").strip()
    name, rest = "", s
    if ":" in s and s.index(":") < (s.index("→") if "→" in s else len(s)):
        name, rest = s.split(":", 1)
        name, rest = name.strip(), rest.strip()
    for arrow in ("→", "->"):
        if arrow in rest:
            alt, neu = rest.split(arrow, 1)
            return (name or rest[:48]), alt.strip(), neu.strip()
    return (name or s[:48]), "(neu)", rest


def _collect_kennwert_rows(synopse: dict, deutung: dict | None) -> list[dict]:
    rows = []
    for c in synopse["chapters"]:
        kap = c.get("new_id") or c.get("old_id") or ""
        for r in c.get("changes", []):
            for e in (r.get("kennwerte") or {}).get("changed", []):
                bu = (e.get("new") or {}).get("base_unit") or e.get("unit") or ""
                desc = (r.get("new_text") or r.get("old_text") or "").strip()[:140]
                rows.append({"k": _UNIT_LABEL.get(bu, bu or "Kennwert"), "kap": kap,
                             "desc": desc, "alt": (e.get("old") or {}).get("raw", ""),
                             "neu": (e.get("new") or {}).get("raw", ""), "src": "Absatz"})
    for c in (deutung.get("chapters", []) if deutung else []):
        kap = c.get("section_id") or ""
        for t in (c.get("tables") or []):
            cap = (t.get("table") or "").strip()
            for s in (t.get("value_changes") or []):
                name, alt, neu = _parse_kw_string(s)
                rows.append({"k": name, "kap": kap, "desc": cap,
                             "alt": alt, "neu": neu, "src": "Tabelle"})
    return rows


def _kennwert_section(rows: list[dict]) -> tuple[str, str]:
    if not rows:
        return "", ""
    head = "".join(f"<th>{h}</th>" for h in
                   ("Kennwert", "Kapitel", "Beschreibung", "alter Wert", "neuer Wert"))
    trs = []
    for r in rows:
        trs.append(
            "<tr>"
            f'<td>{html.escape(r["k"])} <span class="src">{r["src"]}</span></td>'
            f'<td>{html.escape(r["kap"])}</td>'
            f'<td>{html.escape(r["desc"])}</td>'
            f'<td class="kv-alt">{html.escape(r["alt"])}</td>'
            f'<td class="kv-neu">{html.escape(r["neu"])}</td>'
            "</tr>")
    sec = (f'<h2 class="chap" id="kennwerte">Kennwert-Änderungen '
           f'<span class="kw-count" id="kwcount">{len(rows)} Einträge</span></h2>'
           '<p class="meta">Zusammenfassung aller erkannten Kennwert-/Grenzwertänderungen '
           '(Absatz-Wertänderungen mit alt/neu sowie Tabellen-Kennwerte aus der KI-Deutung). '
           'Spaltenkopf anklicken = sortieren, Filterfeld = Volltextsuche.</p>'
           '<input id="kwfilter" placeholder="filtern … z. B. Hz, 10.2, Schutz, MVA">'
           f'<table id="kwtab"><thead><tr>{head}</tr></thead><tbody>{"".join(trs)}</tbody></table>')
    nav = '<a href="#kennwerte" style="color:#2a5d8f;font-weight:600">▤ Kennwert-Änderungen</a>'
    return sec, nav


_KW_JS = """
<script>
(function(){
 var f=document.getElementById('kwfilter'), tab=document.getElementById('kwtab');
 if(!tab) return;
 if(f){f.addEventListener('input',function(){
   var q=this.value.toLowerCase(), n=0;
   tab.tBodies[0].querySelectorAll('tr').forEach(function(r){
     var show=r.textContent.toLowerCase().indexOf(q)>=0; r.style.display=show?'':'none'; if(show)n++;});
   var c=document.getElementById('kwcount'); if(c)c.textContent=n+' Einträge';});}
 tab.tHead.querySelectorAll('th').forEach(function(th,ci){
   th.addEventListener('click',function(){
     var tb=tab.tBodies[0], rows=[].slice.call(tb.rows), asc=th.dataset.asc!=='1';
     th.dataset.asc=asc?'1':'0';
     rows.sort(function(a,b){var x=a.cells[ci].textContent.trim(),y=b.cells[ci].textContent.trim();
       return x.localeCompare(y,'de',{numeric:true})*(asc?1:-1);});
     rows.forEach(function(r){tb.appendChild(r);});});
 });
})();
</script>"""


def build_annotated_html(new_doc: dict, synopse: dict, deutung: dict | None,
                         out_path: str | Path, pair_label: str,
                         old_doc: dict | None = None, out_dir: str | Path | None = None):
    from ..stages.deutung import label as _lbl   # renders the neutral enums in the doc language
    lang = (deutung or {}).get("language", "de")
    ch_by_new = {}
    ch_by_old_removed = []
    for ch in synopse["chapters"]:
        for nid in ch.get("new_ids") or []:
            ch_by_new[nid] = ch
        if ch["mode"] == "removed":
            ch_by_old_removed.append(ch)
    # keyed by mapping id (ENT-24), with section_id as the fallback for older runs and
    # for a section that has no chapter record at all
    deut_by_mapping, deut_by_section = {}, {}
    if deutung:
        for d in deutung.get("chapters", []):
            if d.get("mapping_id"):
                deut_by_mapping[d["mapping_id"]] = d
            deut_by_section[d.get("section_id")] = d

    # --- table maps for real cell diff + figure index ---------------
    old_tab_map, new_tab_map = {}, {}
    if old_doc:
        for s in old_doc.get("sections", []):
            for t in s.get("tables") or []:
                old_tab_map[t["id"]] = t
    for s in new_doc.get("sections", []):
        for t in s.get("tables") or []:
            new_tab_map[t["id"]] = t
    tdiff_by_newid, tdiff_removed_by_sid = {}, {}
    for ch in synopse["chapters"]:
        for e in ch.get("tables_diff") or []:
            # a moved_away record names the new table too, but it belongs to the chapter
            # the table left -- the entry to render at the table itself is the moved_in one
            if e.get("new") and e.get("kind") != "moved_away":
                tdiff_by_newid[e["new"]] = e
            elif e.get("kind") == "removed" and ch.get("new_id"):
                tdiff_removed_by_sid.setdefault(ch["new_id"], []).append(e)
    img_dir = (Path(out_dir) / "neu" / "assets" / "images") if out_dir else None

    nav, body = [], []
    part_badge = {"anhang_normativ": '<span class="badge b-norm">normativ</span>',
                  "anhang_informativ": '<span class="badge b-info">informativ</span>'}

    for sec in new_doc["sections"]:
        if sec["part"] == "vorspann":
            continue
        sid = sec["id"]
        ch = ch_by_new.get(sid)
        primary = ch and (ch.get("new_id") == sid)
        lvl = min(sec.get("level", 1), 4)
        nav.append(f'<a class="l{lvl}" href="#sec-{html.escape(sid)}">{html.escape(sid)} '
                   f'{html.escape(sec["title"][:40])}</a>')
        hdr = f'<h2 class="chap" id="sec-{html.escape(sid)}">{html.escape(sid)} — ' \
              f'{html.escape(sec["title"])}{part_badge.get(sec["part"], "")}'
        d = ((deut_by_mapping.get(ch.get("mapping_id")) or deut_by_section.get(sid))
             if primary else (deut_by_section.get(sid) if not ch else None))
        if d and d.get("training_relevance"):
            hdr += (f'<span class="badge b-rel-{d["training_relevance"]}">'
                    f'Relevanz: {_lbl(d["training_relevance"], lang)}</span>')
        if ch and ch["mode"] == "new":
            hdr += '<span class="badge b-new">NEUES KAPITEL</span>'
        hdr += "</h2>"
        body.append(hdr)

        kws = list(dict.fromkeys(((d.get("keywords") or []) if d and primary else [])
                                 + sec.get("keywords", [])))
        if kws:
            body.append('<div class="kw">Schlagworte: ' +
                        ", ".join(html.escape(k) for k in kws) + "</div>")
        if d and primary:
            s = []
            if d.get("summary_new"):
                s.append(f'<span class="lbl">Inhalt:</span> {html.escape(d["summary_new"])}')
            if d.get("change_overview"):
                s.append(f'<span class="lbl">Änderungen:</span> {html.escape(d["change_overview"])}')
            if d.get("practical_note"):
                s.append(f'<span class="lbl">Praxis:</span> {html.escape(d["practical_note"])}')
            if s:
                body.append('<div class="summary">' + "<br>".join(s) + "</div>")

        # change records of this (sub)chapter, in chronological order:
        # records with a new reference by new_id prefix; pure removal records at the
        # primary chapter (that is where the content stood in the old version)
        changes = []
        if ch:
            prefix = sid + ".p"
            for c in ch["changes"]:
                nids = c.get("new_ids") or []
                if nids:
                    if any(i.startswith(prefix) for i in nids):
                        changes.append(c)
                elif primary:
                    changes.append(c)
        deut_map = {}
        if d:
            for dd in d.get("interpretations", []):
                i = dd.get("change_index")
                if isinstance(i, int) and ch and 0 <= i < len(ch["changes"]):
                    for nid2 in ch["changes"][i].get("new_ids") or ch["changes"][i].get("old_ids") or []:
                        deut_map[nid2] = dd

        # ---- render strictly in the document order of the NEW VERSION ----------
        # (otherwise e.g. a term name and its new explanation get torn apart)
        kind_by_id = {p["id"]: p.get("kind", "text") for p in sec["paragraphs"]}
        no_by_id = {p["id"]: p.get("term_no") for p in sec["paragraphs"]}
        id2change: dict = {}
        for c in changes:
            for nid in c.get("new_ids") or []:
                id2change.setdefault(nid, id(c))
        change_by_objid = {id(c): c for c in changes}

        def _term_wrap(txt_html: str, pids: list | None) -> str:
            """Term-name-only paragraphs: prepend the number + bold. Explanation text stays normal."""
            if pids and len(pids) == 1 and kind_by_id.get(pids[0]) == "term":
                no = no_by_id.get(pids[0])
                pre = f'<span class="meta">{html.escape(no)}</span>&ensp;' if no else ""
                return pre + f"<b>{txt_html}</b>"
            return txt_html

        def _render_change(c) -> None:
            k = c["kind"]
            kwb = _kennwerte_badges(c)
            nids = c.get("new_ids") or []
            dd = None
            for i2 in (c.get("new_ids") or []) + (c.get("old_ids") or []):
                if i2 in deut_map:
                    dd = deut_map[i2]
                    break
            deut_html = ""
            if dd and dd.get("change"):
                qv = dd.get("cross_reference_note") or ""
                verb = dd.get("obligation")
                vtag = ""
                if verb in ("tightened", "relaxed"):
                    vtag = (f' <span class="verb v-{"hi" if verb == "tightened" else "lo"}">'
                            f'{html.escape(_lbl(verb, lang))}</span>')
                # AP-17: the three model axes as a short marker behind the label; the
                # abstention reason (ENT-02) rides along as the tooltip, where it costs
                # no line. Empty for a run written before AP-14.
                marker = axis_marker(dd)
                mtag = ""
                if marker:
                    reason = dd.get("indeterminate_reason") or ""
                    mtag = (f' <span class="axes"'
                            + (f' title="{html.escape(reason)}"' if reason else "")
                            + f'>{html.escape(marker)}</span>')
                deut_html = (f'<div class="deutung">'
                             f'<b>{html.escape(_lbl(dd.get("semantic_label") or "", lang))}</b>'
                             f'{vtag}{mtag} — {html.escape(dd["change"])}'
                             + (f' <i>{html.escape(dd.get("impact") or "")}</i>' if dd.get("impact") else "")
                             + (f'<br>↪ Querverweis: {html.escape(qv)}'
                                if qv and qv not in ("renumbered", "renummeriert") else "")
                             + "</div>")
            if k == "cosmetic":
                body.append(f'<div class="para">{_term_wrap(html.escape(c.get("new_text") or ""), nids)}</div>')
            elif k in ("similar", "split", "merged") and (c.get("display_ops") or c.get("syntactic")):
                ops = c.get("display_ops") or c["syntactic"]["ops"]
                body.append(f'<div class="para">{_term_wrap(_ops_html(ops), nids)}{kwb}</div>{deut_html}')
            elif k in ("new", "moved_in"):
                cls = "p-new" if k == "new" else "p-moved"
                lab = "NEU" if k == "new" else f'hierher verschoben (aus {html.escape(str(c.get("moved_from")))})'
                body.append(f'<div class="para {cls}"><span class="meta">[{lab}]</span> '
                            f'{_term_wrap(html.escape(c.get("new_text") or ""), nids)}{kwb}</div>{deut_html}')
            elif k in ("removed", "moved_away"):
                lab = "ENTFALLEN" if k == "removed" else f'verschoben nach {html.escape(str(c.get("moved_to")))}'
                no = f'<span class="meta">{html.escape(c["term_no"])}</span>&ensp;' if c.get("term_no") else ""
                body.append(f'<div class="para p-removed"><span class="meta">[{lab}]</span> {no}'
                            f'<span class="txt">{html.escape(c.get("old_text") or "")}</span></div>{deut_html}')

        rendered_objs = set()
        for p in sec["paragraphs"]:
            pid = p["id"]
            oid_ = id2change.get(pid)
            if oid_ is not None:
                if oid_ in rendered_objs:
                    continue
                rendered_objs.add(oid_)
                _render_change(change_by_objid[oid_])
            else:
                t = p.get("n1", p.get("n0", "")).strip()
                if t and p.get("kind") != "formula":
                    body.append(f'<div class="para">{_term_wrap(html.escape(t), [pid])}</div>')
        # records without a new reference (removed/moved away) at the chapter end
        for c in changes:
            if not (c.get("new_ids")) and id(c) not in rendered_objs:
                rendered_objs.add(id(c))
                _render_change(c)

        for tbl in sec.get("tables", []):
            entry = tdiff_by_newid.get(tbl["id"])
            if entry:
                body.append(_render_tablediff(entry, old_tab_map, new_tab_map))
            else:
                body.append('<div class="tabpair"><div class="side">'
                            + _html_table(tbl, side="neu") + "</div></div>")
        if primary:
            for e in tdiff_removed_by_sid.get(sid, []):
                body.append(_render_tablediff(e, old_tab_map, new_tab_map))
        for fig in sec.get("figures", []):
            body.append(_fig_embed(fig, img_dir))
        if ch and primary:
            fd = ch.get("formulas_diff") or {}
            if fd.get("added") or fd.get("removed"):
                body.append(f'<div class="tbl-note">ƒ Formeln: {len(fd.get("added", []))} neu/geändert, '
                            f'{len(fd.get("removed", []))} entfallen/geändert '
                            f'(Formeln werden separat verglichen — siehe assets/formulas bzw. Original)</div>')
            # subject-matter table/figure interpretation (parameter/limit values)
            if d:
                _sb = {"changed": "b-rel-medium", "new": "b-new", "removed": "b-removed"}
                for a in d.get("tables") or []:
                    st = a.get("status") or ""
                    kwl = "".join(f'<li>{html.escape(k)}</li>' for k in (a.get("value_changes") or []))
                    body.append(
                        '<div class="asset-deutung"><b>▦ Tabelle</b> '
                        f'<span class="badge {_sb.get(st,"b-rel-low")}">{html.escape(_lbl(st, lang))}</span> '
                        f'<b>{html.escape(a.get("table") or "")}</b><br>{html.escape(a.get("change") or "")}'
                        + (f'<ul class="kwlist">{kwl}</ul>' if kwl else "")
                        + (f'<div class="asset-aus"><i>{html.escape(a.get("impact") or "")}</i></div>'
                           if a.get("impact") else "")
                        + "</div>")
                for a in d.get("figures") or []:
                    st = a.get("status") or ""
                    body.append(
                        '<div class="asset-deutung"><b>▣ Bild</b> '
                        f'<span class="badge {_sb.get(st,"b-rel-low")}">{html.escape(_lbl(st, lang))}</span> '
                        f'<b>{html.escape(a.get("figure") or "")}</b><br>{html.escape(a.get("change") or "")}'
                        + (f'<div class="asset-aus"><i>{html.escape(a.get("impact") or "")}</i></div>'
                           if a.get("impact") else "")
                        + "</div>")

    # entirely removed chapters at the end
    if ch_by_old_removed:
        body.append('<h2 class="chap" id="sec-entfallen">Entfallene Kapitel (alte Fassung)</h2>')
        nav.append('<a href="#sec-entfallen" style="color:#b91c1c">— Entfallene Kapitel —</a>')
        for ch in ch_by_old_removed:
            body.append(f'<h3><span class="badge b-removed">ENTFALLEN</span> {html.escape(ch["old_id"] or "")} — '
                        f'{html.escape(ch["title"] or "")}</h3>')
            for c in ch["changes"][:40]:
                body.append(f'<div class="para p-removed"><span class="txt">'
                            f'{html.escape((c.get("old_text") or "")[:600])}</span></div>')

    kw_sec, kw_nav = _kennwert_section(_collect_kennwert_rows(synopse, deutung))

    doc = f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>{html.escape(pair_label)} — annotierte Fassung</title><style>{_CSS}</style></head>
<body><div class="layout"><nav><b>{html.escape(pair_label)}</b>{kw_nav}{"".join(nav)}</nav>
<main><div class="legend"><ins>neu/eingefügt</ins> · <del>entfallen/gestrichen</del> ·
<span class="badge b-norm">normativer Anhang</span> <span class="badge b-info">informativer Anhang</span> ·
<span class="kennwert">Kennwert-Änderung</span> · <span style="background:#fef08a;padding:0 4px">Zelländerung</span> ·
<span class="verb v-hi">verschärft</span> <span class="verb v-lo">gelockert</span> (Verbindlichkeit lt. KI-Deutung)</div>
<h1>{html.escape(new_doc["title"])}</h1>
<p class="meta">Annotierte Neufassung mit Änderungen gegenüber {html.escape(synopse["old_doc"])}.
Automatisch erzeugt mit normpare. Tabellen mit Zell-Diff, Abbildungen der Neufassung eingebettet; Formeln: siehe Original.</p>
{kw_sec}
{"".join(body)}</main></div>{_KW_JS}</body></html>"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
