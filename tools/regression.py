"""
regression.py -- regression harness for normpare.

Two independent guards against unintended change:

* **byte-exact** SHA-256 comparison of every artifact that is produced without an
  LLM (see :data:`ARTIFACTS`). Any difference is a regression -- there are no
  tolerances, no normalization and no exempt fields.
* a **metric snapshot** (same structure as ``tools/regression.py`` in Pipeline_v2)
  for ``deutung.json``, which depends on the LLM and is therefore not reproducible
  offline. Metrics listed in :data:`HARD_MIN` must not fall.

Usage::

    python tools/regression.py --dir out/4110_hot --update     # write the baseline
    python tools/regression.py --dir out/4110_hot              # check against it
    python tools/regression.py --dir runs/.../replay --name 4110_hot --explain

A baseline may declare ``known_deviations`` (ENT-25): an artifact that is allowed to
differ from the reference as long as its hash equals the documented
``expected_sha256``. Every such entry needs a reason and evidence, otherwise the
harness refuses to run -- an undocumented exception would be a silencer.

The baseline is a JSON file under ``baselines/``; the reference artifacts themselves
are *not* copied, the baseline only points at the directory they live in. The
harness never opens a path below ``out/`` for writing (:func:`guard_write`) -- the
reference runs are irreplaceable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = ROOT / "baselines"
SCHEMA = 1

#: Artifacts compared byte for byte -- everything that is produced without an LLM.
#: ``deutung.json`` is deliberately absent (LLM-dependent), as are ``.docx``/``.pptx``/
#: ``.html`` (timestamps and binary structures).
ARTIFACTS = (
    "chapters.json",
    "keywords.json",
    "mapping.json",
    "statistics.json",
    "synopse.json",
    "alt/norm_doc.json",
    "neu/norm_doc.json",
)

# Metrics whose FALL is a real regression (hard, exit != 0). Taken over unchanged
# from Pipeline_v2 tools/regression.py:104-105.
HARD_MIN = ["deutung.paragraph_evidence_ok", "deutung.tabellen_evidence_ok",
            "deutung.bilder_evidence_ok", "deutung.chapters_deuted"]


#: Fields every ``known_deviations`` entry must carry (ENT-25). An exception without
#: a reason and without evidence would be a silencer, not a documented deviation.
DEVIATION_FIELDS = ("expected_sha256", "grund", "belegt_durch")


class WriteToOutError(RuntimeError):
    """Raised when something tries to write below an ``out/`` directory."""


class BaselineError(RuntimeError):
    """Raised when a baseline is malformed -- e.g. an undocumented known deviation."""


def guard_write(path) -> Path:
    """Return ``path``, or raise if it lies below a directory named ``out``.

    The reference runs under ``out/`` are irreplaceable; every write of this harness
    and of the replay driver goes through here.
    """
    p = Path(path).resolve()
    if "out" in p.parts[:-1]:
        raise WriteToOutError(f"refusing to write below out/: {p}")
    return Path(path)


def sha256_file(path) -> str:
    """SHA-256 of a file's bytes, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _version() -> str:
    """Version of the *source tree*, not of the installed distribution."""
    try:
        from normpare import __version__
        return __version__
    except ImportError:  # pragma: no cover - depends on the installation
        try:
            return version("normpare")
        except PackageNotFoundError:
            return "unknown"


# -- part 1: byte-exact artifacts ------------------------------------------------------

def artifact_digests(out_dir) -> dict:
    """SHA-256 and size for every artifact of :data:`ARTIFACTS` present in ``out_dir``."""
    out = Path(out_dir)
    digests = {}
    for name in ARTIFACTS:
        p = out / name
        if p.exists():
            digests[name] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
    return digests


# -- part 2: metric snapshot (structure as in the Pipeline_v2 template) ----------------

def snapshot(out_dir) -> dict:
    """Metric snapshot of a run directory.

    Mirrors ``snapshot()`` of the Pipeline_v2 harness; the ``deutung`` block reads both
    the English normpare keys and the German v2 keys, so v2 runs stay measurable.
    """
    out = Path(out_dir)
    snap: dict = {"present": {}}
    syn = _load(out / "synopse.json")
    old = _load(out / "alt" / "norm_doc.json")
    new = _load(out / "neu" / "norm_doc.json")
    deut = _load(out / "deutung.json")
    for name, obj in (("synopse", syn), ("norm_alt", old), ("norm_neu", new),
                      ("deutung", deut)):
        snap["present"][name] = obj is not None

    if syn:
        kinds: Counter = Counter()
        tab_real = tab_frag = 0
        for ch in syn.get("chapters") or []:
            for c in ch.get("changes") or []:
                kinds[c.get("kind")] += 1
            for t in ch.get("tables_diff") or []:
                if (t.get("caption") or "").strip():
                    tab_real += 1
                else:
                    tab_frag += 1
        snap["synopse"] = {"n_chapters": len(syn.get("chapters") or []),
                           "change_kinds": dict(sorted(kinds.items())),
                           "tables_real": tab_real, "tables_fragment": tab_frag}
    for tag, doc in (("alt", old), ("neu", new)):
        if doc:
            sections = doc.get("sections") or []
            snap[f"norm_{tag}"] = {
                "sections": len(sections),
                "paragraphs": sum(len(s.get("paragraphs") or []) for s in sections),
                "tables": sum(len(s.get("tables") or []) for s in sections),
                "figures": sum(len(s.get("figures") or []) for s in sections),
            }
    if deut:
        pt = pok = nt = tok = nb = bok = 0
        chdeut = 0
        for c in deut.get("chapters") or []:
            du = c.get("interpretations") or c.get("deutungen") or []
            if du:
                chdeut += 1
            for x in du:
                pt += 1
                pok += 1 if x.get("evidence_ok") else 0
            for t in c.get("tables") or c.get("tabellen") or []:
                nt += 1
                tok += 1 if t.get("evidence_ok") else 0
            for b in c.get("figures") or c.get("bilder") or []:
                nb += 1
                bok += 1 if b.get("evidence_ok") else 0
        fb = Counter(f.get("phase") for f in deut.get("pipeline_feedback") or [])
        snap["deutung"] = {
            "chapters_deuted": chdeut,
            "paragraph_deutungen": pt, "paragraph_evidence_ok": pok,
            "tabellen": nt, "tabellen_evidence_ok": tok,
            "bilder": nb, "bilder_evidence_ok": bok,
            "review_queue": len(deut.get("review_queue") or []),
            "feedback_by_phase": dict(sorted(fb.items())),
        }
    return snap


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


# -- baseline --------------------------------------------------------------------------

def build_baseline(out_dir, name: str | None = None) -> dict:
    """Build a baseline from an existing output directory (artifacts stay where they are)."""
    out = Path(out_dir)
    return {
        "schema": SCHEMA,
        "name": name or out.name,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_dir": str(out.resolve()),
        "normpare_version": _version(),
        "artifacts": artifact_digests(out),
        "snapshot": snapshot(out),
    }


def write_baseline(baseline: dict, path) -> Path:
    """Write a baseline as JSON. Refuses any target below ``out/``."""
    p = guard_write(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(baseline, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def read_baseline(path):
    """Read a baseline, or ``None`` if the file does not exist."""
    return _load(Path(path))


def known_deviations(base: dict) -> dict:
    """Validated ``known_deviations`` block of a baseline (ENT-25).

    An artifact listed here may differ from the reference *as long as* its hash equals
    the recorded ``expected_sha256``; the exception is pinned to one concrete state.
    Every entry needs :data:`DEVIATION_FIELDS`, otherwise the harness refuses to run.
    """
    known = (base or {}).get("known_deviations") or {}
    for art in sorted(known):
        entry = known[art]
        missing = ([f for f in DEVIATION_FIELDS if not (entry or {}).get(f)]
                   if isinstance(entry, dict) else list(DEVIATION_FIELDS))
        if missing:
            raise BaselineError(
                f"known_deviations[{art}] is missing: {', '.join(missing)} -- "
                "an exception without a documented reason and evidence is not allowed")
    return known


# -- explain ---------------------------------------------------------------------------

def _first_byte_diff(a: Path, b: Path) -> int | None:
    """Offset of the first differing byte, or ``None`` if one file is a prefix of the other."""
    ba, bb = a.read_bytes(), b.read_bytes()
    for i in range(min(len(ba), len(bb))):
        if ba[i] != bb[i]:
            return i
    return None


def _json_diffs(a, b, path: str = "", limit: int = 5) -> list[str]:
    """Up to ``limit`` differing JSON paths between two decoded documents."""
    out: list[str] = []
    if type(a) is not type(b):
        return [f"{path or '$'}: type {type(a).__name__} -> {type(b).__name__}"]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if len(out) >= limit:
                break
            if k not in a:
                out.append(f"{path}.{k}: added")
            elif k not in b:
                out.append(f"{path}.{k}: removed")
            else:
                out.extend(_json_diffs(a[k], b[k], f"{path}.{k}", limit - len(out)))
        return out[:limit]
    if isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} -> {len(b)}")
        for i in range(min(len(a), len(b))):
            if len(out) >= limit:
                break
            out.extend(_json_diffs(a[i], b[i], f"{path}[{i}]", limit - len(out)))
        return out[:limit]
    if a != b:
        out.append(f"{path or '$'}: {_short(a)} -> {_short(b)}")
    return out


def _short(v, width: int = 60) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= width else s[:width] + "..."


def explain_artifact(ref_path: Path, cur_path: Path) -> list[str]:
    """Describe *where* two artifacts differ: byte offset, sizes and JSON paths."""
    lines = [f"      reference: {ref_path}", f"      current:   {cur_path}"]
    if not ref_path.exists():
        lines.append("      reference file no longer present -- byte position unknown")
        return lines
    ra, rb = ref_path.stat().st_size, cur_path.stat().st_size
    off = _first_byte_diff(ref_path, cur_path)
    lines.append(f"      size {ra} -> {rb} bytes, first differing byte: "
                 f"{off if off is not None else 'none (one is a prefix of the other)'}")
    if off is not None:
        head = ref_path.read_bytes()[:off]
        lines.append(f"      at line {head.count(b'\n') + 1}")
    try:
        diffs = _json_diffs(json.loads(ref_path.read_text(encoding="utf-8")),
                            json.loads(cur_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return lines
    if diffs:
        lines.extend(f"      {d}" for d in diffs)
    else:
        lines.append("      JSON content is equal -- the difference is formatting or key order")
    return lines


# -- check -----------------------------------------------------------------------------

def check(out_dir, baseline_path, explain: bool = False) -> int:
    """Compare a run directory against a baseline. Returns the exit code."""
    out = Path(out_dir)
    base = read_baseline(baseline_path)
    name = out.name
    if base is None:
        print(f"[{name}] no baseline at {baseline_path} -- create it with --update.")
        return 0
    name = base.get("name", name)

    known = known_deviations(base)
    cur_art = artifact_digests(out)
    base_art = base.get("artifacts") or {}
    ref_dir = Path(base.get("reference_dir", ""))
    missing, differing, added, accepted, resolved = [], [], [], [], []
    for art in sorted(set(base_art) | set(cur_art)):
        a, b = base_art.get(art), cur_art.get(art)
        entry = known.get(art)
        if b is None:
            missing.append(art)
        elif a is None:
            added.append(art)
        elif a["sha256"] != b["sha256"]:
            if entry and entry["expected_sha256"] == b["sha256"]:
                accepted.append((art, entry))
            else:
                differing.append((art, a, b, entry))
        elif entry and out.resolve() != ref_dir.resolve():
            # equality with the reference is only news for a *run*; the reference
            # directory itself trivially equals itself
            resolved.append(art)

    cur_snap = _flatten(snapshot(out))
    base_snap = _flatten(base.get("snapshot") or {})
    changed, dropped = [], []
    for k in sorted(set(cur_snap) | set(base_snap)):
        a, b = base_snap.get(k), cur_snap.get(k)
        if a != b:
            changed.append((k, a, b))
            if (k in HARD_MIN and isinstance(a, (int, float))
                    and isinstance(b, (int, float)) and b < a):
                dropped.append(k)

    if not (missing or differing or added or changed or accepted or resolved):
        print(f"[{name}] OK -- identical to baseline "
              f"({len(cur_art)} artifacts byte for byte).")
        return 0

    if missing or differing or accepted or resolved:
        print(f"[{name}] artifacts:")
    for art in missing:
        print(f"   {art}: MISSING in the run  -- REGRESSION")
    for art, a, b, entry in differing:
        print(f"   {art}: differs  {a['bytes']} -> {b['bytes']} bytes  -- REGRESSION")
        print(f"      sha256 {a['sha256'][:16]}... -> {b['sha256'][:16]}...")
        if entry:
            print(f"      matches neither the reference nor the known deviation "
                  f"({entry['expected_sha256'][:16]}...)")
        if explain:
            for line in explain_artifact(ref_dir / art, out / art):
                print(line)
    for art, entry in accepted:
        print(f"   {art}: known deviation, hash as documented (no regression)")
        print(f"      reason: {entry['grund'].strip()}")
        print(f"      evidence: {entry['belegt_durch']}"
              + (f", recorded {entry['aufgenommen']}" if entry.get("aufgenommen") else ""))
    for art in resolved:
        print(f"   {art}: matches the reference again -- the known deviation "
              f"can be removed from the baseline")
    for art in added:
        print(f"   {art}: new, not in the baseline (no regression)")
    if changed:
        print(f"[{name}] {len(changed)} metric(s) differ:")
        for k, a, b in changed:
            flag = "  REGRESSION" if k in dropped else ""
            print(f"   {k}: {a} -> {b}{flag}")

    if missing or differing or dropped:
        n = len(missing) + len(differing) + len(dropped)
        print(f"[{name}] {n} regression(s).")
        return 1
    if accepted and not (added or changed or resolved):
        print(f"[{name}] OK -- 0 regressions, {len(accepted)} known deviation(s).")
        return 0
    print(f"[{name}] differences, but no regression (use --update to accept).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="run output directory to check")
    ap.add_argument("--name", default=None, help="baseline name (default: directory name)")
    ap.add_argument("--baseline-dir", default=str(BASE_DIR))
    ap.add_argument("--update", action="store_true", help="write/refresh the baseline")
    ap.add_argument("--explain", action="store_true", help="show where artifacts differ")
    args = ap.parse_args(argv)

    out = Path(args.dir)
    name = args.name or out.name
    path = Path(args.baseline_dir) / f"{name}.json"
    try:
        if args.update:
            baseline = build_baseline(out, name)
            # carry documented deviations over -- dropping them silently would lose
            # the reason they were recorded for
            previous = known_deviations(read_baseline(path))
            if previous:
                baseline["known_deviations"] = previous
                print(f"[{name}] kept {len(previous)} known deviation(s) from the "
                      "previous baseline -- check whether they still apply.")
            write_baseline(baseline, path)
            print(f"[{name}] baseline written: {path}")
            return 0
        return check(out, path, explain=args.explain)
    except BaselineError as exc:
        print(f"[{name}] invalid baseline: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
