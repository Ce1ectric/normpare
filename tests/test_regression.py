"""Tests for the regression harness (``tools/regression.py``).

Every test builds its own miniature run directory in ``tmp_path``. No test reads
``out/``, none needs real standard text and none touches the network.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import regression  # noqa: E402


# -- miniature run directories ---------------------------------------------------------

def _norm_doc(doc_id: str) -> dict:
    return {
        "doc_id": doc_id,
        "sections": [
            {"id": "1", "title": "Scope", "paragraphs": [{"text": "a"}, {"text": "b"}],
             "tables": [{"caption": "T"}], "figures": []},
            {"id": "2", "title": "Terms", "paragraphs": [{"text": "c"}],
             "tables": [], "figures": [{"caption": "F"}]},
        ],
    }


def _deutung(evidence_ok: int = 2) -> dict:
    return {
        "model": "none",
        "chapters": [{
            "section_id": "1",
            "interpretations": [{"change_index": i, "evidence_ok": i < evidence_ok}
                                for i in range(3)],
            "tables": [{"evidence_ok": True}],
            "figures": [{"evidence_ok": True}],
        }],
        "review_queue": [{"section_id": "1"}],
        "pipeline_feedback": [{"phase": "deutung"}],
    }


ARTIFACT_CONTENT = {
    "chapters.json": {"chapters": [{"id": "1", "title": "Scope"}]},
    "keywords.json": {"chapters": [{"id": "1", "keywords": ["grid"]}]},
    "mapping.json": {"pair": "old <-> new", "records": [{"old_id": "1", "new_id": "1"}]},
    "statistics.json": {"old": {"sections": 2}, "new": {"sections": 2}, "comparison": {}},
    "synopse.json": {"pair": "old <-> new", "chapters": [
        {"new_id": "1", "changes": [{"kind": "modified"}, {"kind": "added"}],
         "tables_diff": [{"caption": "T"}, {"caption": ""}]},
        {"new_id": "2", "changes": [{"kind": "modified"}], "tables_diff": []},
    ]},
    "alt/norm_doc.json": _norm_doc("alt"),
    "neu/norm_doc.json": _norm_doc("neu"),
}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def make_run(out_dir: Path, *, evidence_ok: int = 2, skip: tuple[str, ...] = ()) -> Path:
    """Create a miniature output directory with all compared artifacts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in ARTIFACT_CONTENT.items():
        if name in skip:
            continue
        _write_json(out_dir / name, data)
    _write_json(out_dir / "deutung.json", _deutung(evidence_ok))
    return out_dir


def _baseline_for(run: Path, baseline_path: Path) -> Path:
    regression.write_baseline(regression.build_baseline(run), baseline_path)
    return baseline_path


# -- 1..10 ------------------------------------------------------------------------------

def test_identisch_keine_regression(tmp_path, capsys):
    run = make_run(tmp_path / "run")
    baseline = _baseline_for(run, tmp_path / "baselines" / "run.json")
    rc = regression.check(run, baseline)
    out = capsys.readouterr().out
    assert rc == 0
    assert "identical" in out.lower()


def test_ein_byte_abweichung_wird_erkannt(tmp_path, capsys):
    ref = make_run(tmp_path / "ref")
    baseline = _baseline_for(ref, tmp_path / "baselines" / "run.json")
    run = make_run(tmp_path / "run")
    text = (run / "synopse.json").read_text(encoding="utf-8")
    (run / "synopse.json").write_text(text.replace("modified", "modifieX", 1), encoding="utf-8")
    rc = regression.check(run, baseline)
    out = capsys.readouterr().out
    assert rc != 0
    assert "synopse.json" in out


def test_fehlende_baseline_ist_kein_absturz(tmp_path, capsys):
    run = make_run(tmp_path / "run")
    rc = regression.check(run, tmp_path / "baselines" / "absent.json")
    out = capsys.readouterr().out
    assert rc == 0
    assert "--update" in out


def test_hard_min_sinkend_ist_regression(tmp_path, capsys):
    ref = make_run(tmp_path / "ref", evidence_ok=3)
    baseline = _baseline_for(ref, tmp_path / "baselines" / "run.json")
    run = make_run(tmp_path / "run", evidence_ok=1)
    rc = regression.check(run, baseline)
    out = capsys.readouterr().out
    assert rc != 0
    assert "deutung.paragraph_evidence_ok" in out


def test_hard_min_steigend_ist_keine_regression(tmp_path, capsys):
    ref = make_run(tmp_path / "ref", evidence_ok=1)
    baseline = _baseline_for(ref, tmp_path / "baselines" / "run.json")
    run = make_run(tmp_path / "run", evidence_ok=3)
    rc = regression.check(run, baseline)
    out = capsys.readouterr().out
    assert rc == 0
    assert "deutung.paragraph_evidence_ok" in out


def test_fehlendes_artefakt_ist_regression(tmp_path, capsys):
    ref = make_run(tmp_path / "ref")
    baseline = _baseline_for(ref, tmp_path / "baselines" / "run.json")
    run = make_run(tmp_path / "run", skip=("keywords.json",))
    rc = regression.check(run, baseline)
    out = capsys.readouterr().out
    assert rc != 0
    assert "keywords.json" in out


def test_zusaetzliches_artefakt_wird_gemeldet(tmp_path, capsys):
    ref = make_run(tmp_path / "ref", skip=("keywords.json",))
    baseline = _baseline_for(ref, tmp_path / "baselines" / "run.json")
    run = make_run(tmp_path / "run")
    rc = regression.check(run, baseline)
    out = capsys.readouterr().out
    assert rc == 0
    assert "keywords.json" in out


def test_hash_ist_stabil(tmp_path):
    p = tmp_path / "artifact.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    first = regression.sha256_file(p)
    second = regression.sha256_file(p)
    assert first == second
    assert first == hashlib.sha256(p.read_bytes()).hexdigest()


def test_baseline_round_trip(tmp_path):
    run = make_run(tmp_path / "run")
    built = regression.build_baseline(run)
    path = tmp_path / "baselines" / "run.json"
    regression.write_baseline(built, path)
    assert regression.read_baseline(path) == built


def test_harness_schreibt_nicht_nach_out(tmp_path, monkeypatch):
    """The harness must never open a path below ``out/`` for writing.

    Checked by inspecting the calls the harness makes, not by a real write attempt.
    """
    out_root = tmp_path / "out"
    run = make_run(out_root / "4110_hot")
    baseline_dir = tmp_path / "baselines"
    writes: list[Path] = []

    orig_write_text = pathlib.Path.write_text
    orig_write_bytes = pathlib.Path.write_bytes
    orig_mkdir = pathlib.Path.mkdir
    orig_path_open = pathlib.Path.open
    orig_open = builtins.open

    def rec_write_text(self, *a, **kw):
        writes.append(Path(self))
        return orig_write_text(self, *a, **kw)

    def rec_write_bytes(self, *a, **kw):
        writes.append(Path(self))
        return orig_write_bytes(self, *a, **kw)

    def rec_mkdir(self, *a, **kw):
        writes.append(Path(self))
        return orig_mkdir(self, *a, **kw)

    def rec_path_open(self, mode="r", *a, **kw):
        if any(c in mode for c in "wxa+"):
            writes.append(Path(self))
        return orig_path_open(self, mode, *a, **kw)

    def rec_open(file, mode="r", *a, **kw):
        if any(c in mode for c in "wxa+"):
            writes.append(Path(file))
        return orig_open(file, mode, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", rec_write_text)
    monkeypatch.setattr(pathlib.Path, "write_bytes", rec_write_bytes)
    monkeypatch.setattr(pathlib.Path, "mkdir", rec_mkdir)
    monkeypatch.setattr(pathlib.Path, "open", rec_path_open)
    monkeypatch.setattr(builtins, "open", rec_open)

    rc_update = regression.main(["--dir", str(run), "--baseline-dir", str(baseline_dir),
                                 "--update"])
    rc_check = regression.main(["--dir", str(run), "--baseline-dir", str(baseline_dir)])

    monkeypatch.undo()
    assert rc_update == 0 and rc_check == 0
    assert writes, "no writes recorded at all -- the recorder did not take effect"
    offenders = [p for p in writes if out_root in Path(p).resolve().parents
                 or Path(p).resolve() == out_root]
    assert offenders == [], f"harness opened paths below out/ for writing: {offenders}"
