"""Pipeline orchestrator: runs the stages of a comparison and writes the JSON artifacts.

Orchestrates the stages in order, operating on
the ``norm_doc.json`` dicts that the migrated stage functions expect. Heavy dependencies
(``lxml``/``PyMuPDF`` for ingest, ``numpy``/``scipy`` for alignment, ``python-docx``/
``python-pptx`` for reports) are imported lazily inside each stage, so importing this
module stays cheap and dependency-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import MissingInputError
from .manifest import write_manifest

STAGES = ["ingest", "enrich", "map", "align", "synopse", "keywords", "deutung", "report"]


@dataclass
class RunResult:
    """Paths of the artifacts produced by a run."""

    out_dir: str
    run_name: str
    synopse_json: str
    statistics_json: str
    chapters_json: str
    html: str
    synopse_det: str
    synopse_final: str
    pptx: str


def _load(path: Path, produced_by: str) -> dict:
    if not path.exists():
        raise MissingInputError(f"missing {path} -- run stage '{produced_by}' first")
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class Pipeline:
    """Runs the comparison stages for a :class:`Config` and writes artifacts to ``out_dir``."""

    def __init__(self, config: Config):
        self.cfg = config
        self.out = Path(config.out_dir)
        self.out.mkdir(parents=True, exist_ok=True)

    def run(self, stages: str | list[str] = "all", use_llm: bool = True) -> RunResult:
        """Run the given stage(s) in order. ``use_llm=False`` skips the interpretation stage."""
        names = STAGES if stages == "all" else ([stages] if isinstance(stages, str) else list(stages))
        for name in names:
            if name == "deutung" and not use_llm:
                continue
            if name not in STAGES:
                raise ValueError(f"unknown stage: {name}")
            getattr(self, name)()
        # every run says what produced it: version, inputs with hash, parameters,
        # schema version (ENT-29)
        write_manifest(self.cfg, self.out, stages=names, use_llm=use_llm)
        run = self.cfg.run_name
        return RunResult(
            out_dir=str(self.out), run_name=run,
            synopse_json=str(self.out / "synopse.json"),
            statistics_json=str(self.out / "statistics.json"),
            chapters_json=str(self.out / "chapters.json"),
            html=str(self.out / f"annotiert_{run}.html"),
            synopse_det=str(self.out / f"Synopse_deterministisch_{run}.docx"),
            synopse_final=str(self.out / f"Synopse_final_{run}.docx"),
            pptx=str(self.out / f"Aenderungen_{run}.pptx"),
        )

    # -- stages (heavy deps imported lazily, mirroring run.py) ------------------------

    def ingest(self) -> None:
        from .stages.ingest import read_document
        for side, spec in (("alt", self.cfg.old), ("neu", self.cfg.new)):
            src = Path(spec.path)
            if not src.exists():
                raise MissingInputError(f"source document missing: {src}")
            read_document(src, self.out / side, spec.doc_id, spec.title, spec.version_label)

    def enrich(self) -> None:
        from .stages.enrich import enrich_doc
        enrich_doc(self.out / "alt" / "norm_doc.json")
        enrich_doc(self.out / "neu" / "norm_doc.json")

    def map(self) -> None:
        from .stages.align.sections import build_section_mapping
        from .text.similarity import load_backend
        old = _load(self.out / "alt" / "norm_doc.json", "ingest")
        new = _load(self.out / "neu" / "norm_doc.json", "ingest")
        backend = load_backend(self.cfg.similarity)
        records = build_section_mapping(old, new, backend)
        _write(self.out / "mapping.json", {"pair": self.cfg.pair_label, "records": records})
        # ``build_section_mapping`` inserted the titles of absorbed sections as synthetic
        # paragraphs in place, so the file is written back. From here on the artifact is
        # no longer the pure enrich state: it is the state after ``map``, and anything
        # that recomputes from it has to remove those paragraphs first (tools/replay.py).
        _write(self.out / "alt" / "norm_doc.json", old)

    def align(self) -> None:
        from .stages.align.paras import align_document
        from .text.similarity import load_backend
        old = _load(self.out / "alt" / "norm_doc.json", "ingest")
        new = _load(self.out / "neu" / "norm_doc.json", "ingest")
        mapping = _load(self.out / "mapping.json", "map")
        backend = load_backend(self.cfg.similarity)
        align_stats: dict = {}
        mapping["moved"] = align_document(old, new, mapping["records"], backend,
                                          tau=self.cfg.tau, tau_moved=self.cfg.tau_moved,
                                          stats=align_stats)
        # AP-24: halves of split paragraphs taken into the existing pairing, and candidates
        # left lying because their old paragraph had already taken one (console only)
        print(f"  [align] Geteilte Absätze zusammengeführt: "
              f"{align_stats.get('split_absorbed', 0)}"
              f" (liegen geblieben: {align_stats.get('split_skipped', 0)})")
        if self.cfg.embed_fallback:
            from .stages.align.paras import embed_move_pass, embed_rescue_pass
            from .text.similarity import load_embed_backend
            embed = load_embed_backend(self.cfg.embed_model,
                                       cache_path=self.out / ".vec_cache.npz")
            mapping["embed_rescue"] = embed_rescue_pass(
                old, new, mapping["records"], embed, tau_embed=self.cfg.tau_embed)
            # AP-26: after the in-chapter rescue, the cross-chapter moves -- with the
            # check rule, so a move is only claimed where it is proven
            mapping["embed_moves"] = embed_move_pass(old, new, mapping["records"], embed)
            if getattr(embed, "cache", None) is not None:
                embed.cache.flush()
        _write(self.out / "mapping.json", mapping)

    def synopse(self) -> None:
        from .stages.diff import build_synopse
        from .stages.review_removed import write_review_removed
        from .text.similarity import load_backend
        old = _load(self.out / "alt" / "norm_doc.json", "ingest")
        new = _load(self.out / "neu" / "norm_doc.json", "ingest")
        mapping = _load(self.out / "mapping.json", "align")
        backend = load_backend(self.cfg.similarity)
        syn = build_synopse(old, new, mapping["records"], backend,
                            self.out / "synopse.json", self.cfg.pair_label)
        # the removals worth a second look -- deterministic, so it is complete with --no-llm
        write_review_removed(syn, self.out / "review_removed.json")

    def keywords(self) -> None:
        from .stages.keywords import assign_keywords
        old = _load(self.out / "alt" / "norm_doc.json", "ingest")
        new = _load(self.out / "neu" / "norm_doc.json", "ingest")
        assign_keywords(new, self.out / "keywords.json")
        assign_keywords(old)
        _write(self.out / "neu" / "norm_doc.json", new)
        _write(self.out / "alt" / "norm_doc.json", old)

    def deutung(self) -> None:
        from .stages.deutung import run_deutung
        old = _load(self.out / "alt" / "norm_doc.json", "ingest")
        new = _load(self.out / "neu" / "norm_doc.json", "ingest")
        syn = _load(self.out / "synopse.json", "synopse")
        llm = self.cfg.llm
        run_deutung(syn, old, new, Path.cwd(), self.out,
                    model=llm.model, scope=llm.scope, provider=llm.provider,
                    base_url=llm.base_url, key_env=llm.key_env, api_version=llm.api_version,
                    batch=llm.batch, language=self.cfg.language,
                    title=self.cfg.new.title or self.cfg.pair_label)

    def report(self) -> None:
        from .report.changes_csv import build_changes_csv
        from .report.component_view import build_component_view
        from .report.csv_export import export_tables_csv
        from .report.dossier_report import build_dossier
        from .report.html import build_annotated_html
        from .report.pptx import build_pptx
        from .report.review_list import build_review_list
        from .report.stats import build_statistics
        from .report.synopse_det import build_docx_synopse
        from .report.synopse_final import build_final_synopse
        run, pair = self.cfg.run_name, self.cfg.pair_label
        old = _load(self.out / "alt" / "norm_doc.json", "ingest")
        new = _load(self.out / "neu" / "norm_doc.json", "ingest")
        syn = _load(self.out / "synopse.json", "synopse")
        deut_path = self.out / "deutung.json"
        deut = json.loads(deut_path.read_text(encoding="utf-8")) if deut_path.exists() else None
        stats = build_statistics(old, new, syn, self.out / "statistics.json")
        build_dossier(new, old, syn, deut, self.out / "chapters.json")
        # tables as CSV assets (both versions)
        export_tables_csv(old, self.out / "alt")
        export_tables_csv(new, self.out / "neu")
        build_annotated_html(new, syn, deut, self.out / f"annotiert_{run}.html", pair,
                             old_doc=old, out_dir=self.out)
        # two synopses: deterministic (always) + final (LLM-interpreted, only when available)
        build_docx_synopse(syn, None, self.out / f"Synopse_deterministisch_{run}.docx", pair)
        if deut is not None:
            build_final_synopse(syn, deut, self.out / f"Synopse_final_{run}.docx", pair)
        build_pptx(syn, deut, stats, self.out / f"Aenderungen_{run}.pptx", pair)
        # the readable side of review_removed.json (AP-11); needs no interpretation
        build_review_list(syn, new, self.out / f"Pruefliste_entfallen_{run}.md", pair)
        # the four axes, made visible (AP-17): one row per interpretation for the
        # spreadsheet, and the same material grouped by affected component for a reader.
        # Both are written for a run without interpretation too -- then they are empty.
        build_changes_csv(syn, deut, self.out / f"Aenderungen_{run}.csv")
        build_component_view(syn, deut, self.out / f"Aenderungen_nach_Komponente_{run}.md",
                             pair)
