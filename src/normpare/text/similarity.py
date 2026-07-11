"""
similarity.py -- pluggable similarity backend for the alignment.

Backends:
  tfidf   -- deterministic, offline: word TF-IDF + character 3/4-gram TF-IDF
            (mixed), cosine. Default in environments without model access.
  jina    -- jinaai/jina-embeddings-v2-base-de via sentence-transformers
            (local, on a machine with model access; recommended for production).

Uniform API: sim_matrix(texts_a, texts_b) -> np.ndarray  (cosine, [0..1]).
All texts are normalized to N3 so that PDF/DOCX cosmetics do not matter.
"""
from __future__ import annotations
import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

from .textnorm import n3


class VectorCache:
    """On-disk cache of embedding vectors, keyed by ``SHA1(model + N3(text))``.

    Makes the optional embedding rescue pass (ADR-0001) reproducible and cheap on
    re-runs: identical text under an identical model always yields the identical vector,
    so a second run reuses the stored vectors instead of re-encoding.
    """

    def __init__(self, path, model_name: str):
        self.path = Path(path)
        self.model_name = model_name
        self._mem: dict[str, np.ndarray] = {}
        self._dirty = False
        if self.path.exists():
            data = np.load(self.path, allow_pickle=False)
            self._mem = {k: data[k] for k in data.files}

    def _key(self, text: str) -> str:
        return "h" + hashlib.sha1(f"{self.model_name}\x00{n3(text)}".encode("utf-8")).hexdigest()

    def get(self, text: str):
        return self._mem.get(self._key(text))

    def put(self, text: str, vec) -> None:
        self._mem[self._key(text)] = np.asarray(vec, dtype=np.float32)
        self._dirty = True

    def flush(self) -> None:
        if self._dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.path, **self._mem)
            self._dirty = False


def _char_ngrams(t: str, sizes=(3, 4)):
    t = " " + t + " "
    for n in sizes:
        for i in range(len(t) - n + 1):
            yield t[i:i + n]


class TfidfBackend:
    """Word + character n-gram TF-IDF, mixed 60/40, scipy-sparse vectorized.
    Deterministic, offline."""
    name = "tfidf"

    def _matrix(self, docs: list[Counter]):
        from scipy import sparse
        vocab: dict = {}
        rows, cols, vals = [], [], []
        df = Counter()
        for d in docs:
            df.update(d.keys())
        n = max(len(docs), 1)
        idf = {k: math.log((1 + n) / (1 + v)) + 1 for k, v in df.items()}
        for i, d in enumerate(docs):
            for k, tf in d.items():
                j = vocab.setdefault(k, len(vocab))
                rows.append(i)
                cols.append(j)
                vals.append(tf * idf[k])
        m = sparse.csr_matrix((vals, (rows, cols)), shape=(len(docs), max(len(vocab), 1)))
        norms = np.sqrt(m.multiply(m).sum(axis=1)).A.ravel()
        norms[norms == 0] = 1.0
        return sparse.diags(1.0 / norms) @ m

    def sim_matrix(self, texts_a: list[str], texts_b: list[str]) -> np.ndarray:
        na = [n3(t) for t in texts_a]
        nb = [n3(t) for t in texts_b]
        both = na + nb
        wdocs = [Counter(re.findall(r"\w+", t)) for t in both]
        cdocs = [Counter(_char_ngrams(t)) for t in both]
        W = self._matrix(wdocs)
        C = self._matrix(cdocs)
        k = len(na)
        mw = (W[:k] @ W[k:].T).toarray()
        mc = (C[:k] @ C[k:].T).toarray()
        return np.clip(0.6 * mw + 0.4 * mc, 0, 1)


class JinaBackend:
    """German-specialized embedding (requires sentence-transformers + model access)."""
    name = "jina"

    def __init__(self, model_name: str = "jinaai/jina-embeddings-v2-base-de",
                 cache: "VectorCache | None" = None):
        from sentence_transformers import SentenceTransformer  # lazy
        self.model_name = model_name
        self.cache = cache
        self.model = SentenceTransformer(model_name, trust_remote_code=True)
        try:
            self.model.max_seq_length = 8192
        except Exception:
            pass

    def _encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts, reusing cached vectors and only encoding the misses."""
        out: list = [None] * len(texts)
        todo, todo_i = [], []
        for i, t in enumerate(texts):
            v = self.cache.get(t) if self.cache else None
            if v is None:
                todo.append(t)
                todo_i.append(i)
            else:
                out[i] = v
        if todo:
            enc = np.asarray(self.model.encode([n3(t) for t in todo], normalize_embeddings=True))
            for k, i in enumerate(todo_i):
                out[i] = enc[k]
                if self.cache:
                    self.cache.put(todo[k], enc[k])
        return np.vstack(out) if out else np.zeros((0, 1), dtype=np.float32)

    def sim_matrix(self, texts_a: list[str], texts_b: list[str]) -> np.ndarray:
        ea = self._encode(texts_a)
        eb = self._encode(texts_b)
        return np.clip(ea @ eb.T, 0, 1)


def load_backend(name: str):
    if name == "jina":
        try:
            return JinaBackend()
        except Exception as e:
            raise SystemExit(
                f"Embedding backend 'jina' could not be loaded ({e!r}). Use an environment with "
                f"sentence-transformers/model access, or set config: similarity=tfidf.")
    if name == "tfidf":
        return TfidfBackend()
    raise SystemExit(f"Unknown similarity backend: {name}")


def load_embed_backend(model_name: str = "jinaai/jina-embeddings-v2-base-de",
                       cache_path=None) -> "JinaBackend":
    """Load the embedding backend used by the optional rescue pass (ADR-0001).

    Wraps :class:`JinaBackend` with a :class:`VectorCache` for reproducibility. Raises a
    clear error if ``sentence-transformers``/model access is unavailable.
    """
    cache = VectorCache(cache_path, model_name) if cache_path else None
    try:
        return JinaBackend(model_name, cache=cache)
    except Exception as e:  # pragma: no cover - depends on optional heavy dependency
        raise SystemExit(
            f"Embedding fallback needs the 'embeddings' extra and model access ({e!r}). "
            f"Install with: pip install 'normpare[embeddings]'  (or disable embed_fallback).")
