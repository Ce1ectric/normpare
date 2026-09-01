"""
enrich.py -- stage S2: enrichment of norm_doc.json.

Per paragraph: N1/N2/N3 normal forms (only N1 is stored, N2/N3 on the fly), modality
per sentence (max + counts), internal/external references, parameter values.
Per section: aggregated modality counts, reference and value collection.
Per table: the values of every cell that carries one (AP-31).
"""
from __future__ import annotations
import json
from pathlib import Path

from . import modality, refs, values
from ...text.textnorm import n1


def enrich_doc(norm_doc_path: str | Path) -> dict:
    p = Path(norm_doc_path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    for sec in doc["sections"]:
        sec_mod: dict[str, int] = {}
        sec_refs_i, sec_refs_e = set(), set()
        for par in sec["paragraphs"]:
            t1 = n1(par["n0"])
            par["n1"] = t1
            mod = modality.classify_paragraph(t1)
            par["modality"] = mod["max"]
            par["modality_counts"] = mod["counts"]
            par["refs_internal"] = refs.internal(t1)
            par["refs_external"] = refs.external(t1)
            par["values"] = values.extract_values(t1)
            for k, v in mod["counts"].items():
                sec_mod[k] = sec_mod.get(k, 0) + v
            sec_refs_i.update(par["refs_internal"])
            sec_refs_e.update(par["refs_external"])
        # AP-31: the cells carry their values too. Until then the value detection ran over
        # paragraph text only, and a limit value in a table cell was invisible to the
        # deterministic stage in every run -- in the new 4110 edition that is 532 of 1461
        # values. Additive: nothing above this line reads the field.
        for tab in sec.get("tables") or []:
            tab["cell_values"] = values.cell_values(tab.get("cells"))
        sec["modality_counts"] = sec_mod
        sec["refs_internal"] = sorted(sec_refs_i)
        sec["refs_external"] = sorted(sec_refs_e)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc
