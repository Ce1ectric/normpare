"""
llm.py -- stage S5: AI interpretation (Anthropic API), batched per chapter.

One call per chapter: chapter context (title, summaries, change list) goes in, a
schema-bound JSON interpretation comes out. Without a valid key the same prompts are
exported as files (chat workflow) and the pipeline finishes deterministically with the
AI fields left empty. Answers are cached by hash over (model, prompt).
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

from ..text.textnorm import n1, n2, split_sentences
from .enrich import values

SEMANTIC_LABELS = ["equivalent", "clarified", "extended", "restricted",
                   "new_obligation", "removed_obligation", "moved", "optional",
                   "informative", "contradictory"]

# Human-readable rendering of the schema enums, per output language. The JSON stays in
# neutral English; only the rendered documents are localized.
LABELS = {
    "de": {
        "equivalent": "gleichbedeutend", "clarified": "präzisiert", "extended": "erweitert",
        "restricted": "eingeschränkt", "new_obligation": "neue Pflicht",
        "removed_obligation": "entfallene Pflicht", "moved": "verschoben",
        "optional": "optional", "informative": "informativ",
        "contradictory": "widersprüchlich",
        "tightened": "verschärft", "relaxed": "gelockert", "unchanged": "unverändert",
        "high": "hoch", "medium": "mittel", "low": "gering",
        "changed": "geändert", "new": "neu", "removed": "entfallen",
    },
}

LANGUAGE_NAMES = {"de": "German", "en": "English", "fr": "French", "es": "Spanish",
                  "it": "Italian", "nl": "Dutch"}

#: Token budget for one chapter answer. AP-18: at 16000 the answer to chapter 10.2.2 of
#: the 4110 run ran into the limit and stopped inside a JSON string -- that chapter
#: carries roughly 90 changes, and its truncated answer alone was 35 kB. 13 of the 18
#: chapters that two production runs lost were truncated this way. 48000 gives a good
#: three times the room the largest chapter needed; the remaining case is now *reported*
#: (see :data:`ANSWER_OUTCOMES`) instead of disappearing, and can be handled by hand.
#: The budget stays per *request*, and a request asks about at most
#: :data:`MAX_CHANGES_PER_REQUEST` changes -- a chapter with more of them is split
#: (AP-19), so the answer length stays structurally bounded however large a chapter is.
MAX_ANSWER_TOKENS = 48000

#: How many changes one request asks about. The selection is by priority (value change,
#: modality shift, new/removed obligation), so block 1 holds the most relevant ones.
#: Until AP-19 everything beyond this number was silently dropped: 714 of 2506 changes in
#: the 4110 run, 552 of 2065 in 4120, in the professionally most important chapters.
MAX_CHANGES_PER_REQUEST = 40

#: Character budget for the cell text of **one** table in the prompt. Until AP-20 this was
#: 1100 and cut without a word: 115 of 233 tables in the 4110 run, 89 of 171 in 4120 --
#: half of the tables, and tables are where the limit values live. Measured over both
#: corpora the largest rendered table is 6752 characters (4110) and 6645 (4120), so 8000
#: covers every single one of them and no splitting into row blocks is needed. A table
#: that still exceeds it says so, see :func:`_render_cells`.
TABLE_CHARS = 8000

#: Character budget for the whole table and figure block of one chapter. 6500 before, and
#: with tables arriving complete it would bite at once (18 of 48 blocks in 4110, 13 of 42
#: in 4120). Largest measured block under :data:`TABLE_CHARS`: 26 445 (4110) and 29 694
#: (4120) -- 32000 covers both, and the cut is named when it happens.
ASSET_BLOCK_CHARS = 32000

#: Why a chapter has an interpretation, or has none. ``api_error`` never stands alone: it
#: carries the API's own error type (``api_error:overloaded_error``), because "error" says
#: nothing about whether a repeat run would help.
OUTCOME_OK = "ok"
OUTCOME_REPAIRED = "repaired"          # only readable with strict=False: a raw control
                                       # character in a string. A usable answer, but not
                                       # a clean one -- how often the model delivers
                                       # invalid JSON has to stay countable (AP-19).
OUTCOME_TRUNCATED = "truncated"        # stop_reason == max_tokens: the answer was cut off
OUTCOME_UNPARSABLE = "unparsable"      # complete answer, no valid JSON in it
OUTCOME_API_ERROR = "api_error:"       # prefix, completed by the type the API reported
OUTCOME_EXPORT = "export"              # no key: the prompt was written out, nothing asked
OUTCOME_NO_ANSWER = "no_answer"        # a provider returned None without saying why

#: Order the reasons are reported in -- fixed, so two runs read the same way.
ANSWER_OUTCOMES = (OUTCOME_TRUNCATED, OUTCOME_API_ERROR.rstrip(":"), OUTCOME_UNPARSABLE,
                   OUTCOME_EXPORT, OUTCOME_NO_ANSWER)

#: How many chapter names one summary line shows before it counts the rest.
SUMMARY_NAMES = 8


# ------------------------------------------------------------------ four-axis taxonomy
# ENT-01. One flat label cannot carry three statements at once, and ``semantic_label``
# tries to. Measured on ``out/4110_2026-08b`` (1749 interpretations), ``restricted``
# splits 14 / 15 / 13 over ``tightened`` / ``relaxed`` / ``unchanged``: the value means
# "the scope was restricted" (fewer cases covered -- a relaxation for whoever is bound)
# and "the requirement was restricted" (a tightening) at the same time. Another 84
# interpretations claim a duty appeared or fell away and report ``unchanged`` with it.
# Together roughly 7 % contradict themselves, not for want of model quality but because
# the schema does not keep the statements apart.
#
# Four axes keep them apart. They are introduced *additively*: ``semantic_label`` and
# ``obligation`` keep running unchanged, so both readings are collected on the same
# chapters and the replacement can be decided on measurements rather than on intent.

#: Axis A -- the structural operation, keyed by the ``kind`` of the change record.
#: **Pipeline-owned** (ENT-51): deterministic, never asked of the model. ``cosmetic``
#: is a modification like any other here; that its wording is semantically equal is a
#: statement about axis B, and mixing it in is the very defect this taxonomy removes.
#: ``identical`` never reaches a change record (the diff counts it as ``n_identical``);
#: it is listed because the paragraph aligner emits it and a future caller may pass it.
STRUCTURAL_OPERATIONS = {
    "new": "added",
    "removed": "removed",
    "changed": "modified",
    "similar": "modified",
    "cosmetic": "modified",
    "merged": "merged",
    "split": "split",
    "moved_in": "moved",
    "moved_away": "moved",
    "identical": "unchanged",
}

#: Axis B -- what happens to the *statement*, without judging its effect. ``narrowed``
#: replaces ``restricted`` and is a statement about SCOPE only; whoever means strictness
#: uses axis C.
SEMANTIC_STATUS = ["equivalent", "clarified", "extended", "narrowed", "replaced",
                   "contradictory", "indeterminate"]

#: Axis C -- the direction of the duty. ``not_applicable`` is for non-normative text,
#: which today is forced into ``unchanged`` and mixed with genuine non-changes (168 of
#: them in the 4110 run).
NORMATIVE_DIRECTIONS = ["tightened", "relaxed", "unchanged", "not_applicable",
                        "indeterminate"]

#: Axis D -- which components of the standard a change touches. Multi-valued, most
#: important first (the ``keywords`` convention). The vocabulary is a proposal and not
#: yet confirmed domain knowledge, which is what :data:`OTHER_COMPONENT` is for.
#: Several values are told apart by their description rather than by the subject
#: matter (AP-14, F-2); the separating rules therefore live in the field description in
#: :data:`CHAPTER_SCHEMA_DOC`, where the model reads them, not in a comment.
#:
#: AP-16 added the last five, in front of ``none``: ``other:`` carried 13.9 % (4110) and
#: 20.5 % (60909) of the first two runs, and 60 % of those 428 free labels fell into
#: exactly these five clusters. Terminology and notation formed a sixth cluster and did
#: **not** become a value -- they overlap ``definition`` and would have built in the next
#: ambiguity; a separation rule does the same work without a vocabulary change. The
#: ten older values keep their relative order, so every earlier run stays comparable.
AFFECTED_COMPONENTS = ["proof_obligation", "limit_value", "procedure", "deadline",
                       "responsibility", "documentation", "scope", "definition",
                       "reference", "formula", "note", "heading", "caption", "example",
                       "none"]

#: Prefix of the escape hatch on axis D: ``other:<short label>``. Every use is reported
#: with its free text, so the vocabulary above can be corrected from what was needed
#: rather than from what was imagined.
OTHER_COMPONENT = "other:"

#: ENT-02 -- why an axis was left undecided. Closed vocabulary; ``ambiguous_scope`` is
#: the code for exactly the cases that appear as ``restricted`` today.
INDETERMINATE_REASONS = ["no_evidence", "ambiguous_scope", "conflicting_signals",
                         "outside_text"]

#: The value that triggers the reason requirement.
INDETERMINATE = "indeterminate"

#: Abstention lives on axes B and C, never on D: "which component is affected" is a
#: question about the text, not a judgement that can be left open.
ABSTENTION_AXES = ("semantic_status", "normative_direction")

#: Why a supplied axis value was discarded -- the phrasing used in ``pipeline_feedback``.
AXIS_VIOLATIONS = {
    "missing": "is missing",
    "outside_vocabulary": "lies outside the closed vocabulary",
    "not_a_list": "is not a list",
    "reason_missing": "abstains without a reason code (ENT-02)",
    "reason_without_abstention": "carries a reason code without an abstention",
}


def structural_operation(change: dict | None) -> str | None:
    """Axis A of a change record -- ``None`` for an unknown or absent ``kind``.

    Fails closed on purpose: a kind the mapping does not know is reported as "no
    operation" rather than as the nearest one, so a new kind shows up as a gap instead
    of quietly joining an existing bucket.
    """
    return STRUCTURAL_OPERATIONS.get((change or {}).get("kind"))


def _valid_component(value) -> bool:
    """Whether a single axis-D entry is admissible -- vocabulary or labelled ``other:``."""
    if not isinstance(value, str):
        return False
    if value.startswith(OTHER_COMPONENT):
        return bool(value[len(OTHER_COMPONENT):].strip())
    return value in AFFECTED_COMPONENTS


def check_axes(deutung: dict, change: dict | None = None) -> tuple[dict, list[dict]]:
    """The four axes of one interpretation, checked against their vocabularies.

    Returns the fields to write into the interpretation and the violations found. A
    value outside its vocabulary is **discarded and reported, never corrected**:
    reading ``restricted`` as ``narrowed`` or ``verschärft`` as ``tightened`` would
    measure the correction instead of the model, and the whole point of the additive
    introduction is to measure.

    Axis A comes from ``change`` and is not read from the answer at all -- the model
    never sees the field (see :data:`PIPELINE_OWNED_INTERPRETATION`).

    Axis D keeps the order it was delivered in ("most important first") and is filtered
    entry by entry; an empty result is ``None``, so "nothing usable was said" stays
    distinguishable from the explicit ``none``.

    ENT-02: ``indeterminate`` on axis B or C requires a code from
    :data:`INDETERMINATE_REASONS`. An abstention without one is not an abstention but a
    second way of saying nothing, so the axis is emptied and counted.
    """
    bad: list[dict] = []

    def reject(field: str, value, reason: str) -> None:
        bad.append({"field": field, "value": value, "reason": reason})

    fields: dict = {"structural_operation": structural_operation(change)}

    for field, vocabulary in (("semantic_status", SEMANTIC_STATUS),
                              ("normative_direction", NORMATIVE_DIRECTIONS)):
        value = deutung.get(field)
        if value in vocabulary:
            fields[field] = value
        else:
            fields[field] = None
            reject(field, value, "missing" if not value else "outside_vocabulary")

    supplied = deutung.get("affected_components")
    if not isinstance(supplied, list):
        fields["affected_components"] = None
        reject("affected_components", supplied,
               "missing" if supplied is None else "not_a_list")
    elif not supplied:
        fields["affected_components"] = None
        reject("affected_components", supplied, "missing")
    else:
        for c in supplied:
            if not _valid_component(c):
                reject("affected_components", c, "outside_vocabulary")
        fields["affected_components"] = [c for c in supplied if _valid_component(c)] or None

    reason = deutung.get("indeterminate_reason")
    reason = reason.strip() if isinstance(reason, str) else None
    if reason and reason not in INDETERMINATE_REASONS:
        reject("indeterminate_reason", reason, "outside_vocabulary")
        reason = None
    abstained = [f for f in ABSTENTION_AXES if fields[f] == INDETERMINATE]
    if abstained and not reason:
        for f in abstained:
            fields[f] = None
            reject(f, INDETERMINATE, "reason_missing")
    elif reason and not abstained:
        reject("indeterminate_reason", reason, "reason_without_abstention")
        reason = None
    fields["indeterminate_reason"] = reason or None
    return fields, bad

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a domain expert analysing two editions of a technical standard{title}. You are "
    "preparing training material on what changed between the old and the new edition. Work "
    "precisely and soberly; never speculate. Strictly distinguish editorial rewording "
    "(equivalent) from a substantive change. The deterministic findings supplied with each "
    "change (parameter-value diff, modality shift, reference diff) are computed mechanically: "
    "use them, but if a finding contradicts the text, set contradiction_flag=true and briefly "
    "explain why. Pay close attention to CROSS-REFERENCES: for changed internal references "
    "(sections, annexes, tables, figures) distinguish mere renumbering caused by the new "
    "chapter structure from a genuinely NEW reference target (then fill cross_reference_note); "
    "changed references to other standards are almost always substantive. For `evidence`, "
    "quote a short passage VERBATIM from the old or the new text: the literal span only, no "
    "paraphrase, no added labels or prefixes. Also report in pipeline_feedback anything that "
    "looks like a PREPROCESSING artefact rather than a real change: stray page numbers, torn "
    "or merged paragraphs, swallowed hyphens, formula or encoding residue, obviously wrong "
    "alignments, or gaps in the source document. "
    "The interpretation axes are INDEPENDENT and are decided separately: semantic_status "
    "says what happens to the statement, normative_direction what it means for whoever is "
    "bound by it -- a smaller scope (narrowed) usually relaxes the burden, a narrower "
    "permission tightens it. Where the text itself does not settle one of the two, answer "
    "'indeterminate' with a reason code instead of guessing; an honest abstention is worth "
    "more than a forced label. "
    "Write every free-text value in {language}; keep the enum values exactly as given in the "
    "schema (English). Answer with the required JSON only, no markdown fences."
)


def build_system_prompt(language: str = "de", title: str = "") -> str:
    """System prompt for the interpretation: neutral and English, with the free-text output
    language taken from the configuration (the compared standard stays German by default)."""
    lang = LANGUAGE_NAMES.get((language or "de").lower(), language or "German")
    return _SYSTEM_PROMPT_TEMPLATE.format(
        title=f" ({title})" if title else "", language=lang)


CHAPTER_SCHEMA_DOC = """{
 "section_id": "<id>",
 "summary_old": "2-4 sentences: what this chapter says in the OLD edition ('' if the chapter is new)",
 "summary_new": "2-4 sentences: what this chapter says in the NEW edition ('' if it was removed)",
 "change_overview": "1-3 sentences: what changes in this chapter overall",
 "training_relevance": "high|medium|low",
 "keywords": ["1-4 keywords from the supplied list, most relevant first"],
 "practical_note": "1-2 sentences: a concrete practical example that highlights the change(s) ('' if none)",
 "pipeline_feedback": [
   {"phase": "ingest_pdf|ingest_docx|alignment|diff|source",
    "finding": "observed preprocessing artefact (e.g. stray page number, swallowed hyphen, formula residue, wrongly split paragraph, gap in the source document)",
    "change_indices": [<int>]}
 ],
 "interpretations": [
   {"change_index": <int, index from the change list>,
    "semantic_label": "equivalent|clarified|extended|restricted|new_obligation|removed_obligation|moved|optional|informative|contradictory",
    "obligation": "tightened|relaxed|unchanged",
    "semantic_status": "equivalent|clarified|extended|narrowed|replaced|contradictory|indeterminate -- what happens to the STATEMENT itself; narrowed = the scope now covers fewer cases, never 'stricter' (strictness is normative_direction). For a change without a counterpart the axis describes what happens to the BODY OF STATEMENTS of the standard: newly added text is extended, text dropped without replacement is narrowed. If a specific successor or predecessor is recognisable elsewhere, answer replaced instead. A moved change is the same statement in a new place: the axis describes the TEXT, never the place (the place is structural). Unchanged moved text is equivalent; text reworded on the way takes the value that describes the rewording (clarified|extended|narrowed). Keep replaced for a change whose counterpart is not connected by a move.",
    "normative_direction": "tightened|relaxed|unchanged|not_applicable|indeterminate -- what it means for whoever is bound by the requirement; not_applicable for non-normative text",
    "affected_components": ["which parts of the standard the change touches, most important first: proof_obligation|limit_value|procedure|deadline|responsibility|documentation|scope|definition|reference|formula|note|heading|caption|example|none; use 'other:<short label>' if none of them fits. formula for an equation or its symbols; note for a note or an explanatory remark; heading for a heading or the numbering of the outline; caption for the caption or legend of a figure or a table; example for a worked example or a sample calculation. Separation rules: proof_obligation when what changes is whether or to whom something must be proven, procedure when what changes is how (both may apply, then proof_obligation first); documentation for producing, keeping or presenting records with no body accepting them, proof_obligation as soon as a body accepts the proof; definition only for a change in the terms chapter or to a legal definition, with scope behind it if that shifts the scope of application indirectly; reference only when the change is nothing but the reference, otherwise the substantive component first and reference behind it; definition also for a changed designation, spelling or symbol notation of a term, formula only when the equation itself changes and not its name. Recognisable preprocessing artefacts (a torn sentence, formula residue, a corrected typo) belong in pipeline_feedback and not on this axis."],
    "indeterminate_reason": "no_evidence|ambiguous_scope|conflicting_signals|outside_text -- required when semantic_status or normative_direction is indeterminate, '' otherwise",
    "change": "1-2 sentences describing the substance of the change",
    "impact": "1 sentence on the practical impact ('' if none)",
    "cross_reference_note": "meaning of changed internal/external references: 'renumbered' for a pure structural follow-on, otherwise a short explanation of the new target ('' if no reference change)",
    "evidence": "verbatim short quote from the old or the new text",
    "confidence": "high|medium|low",
    "contradiction_flag": false}
 ]
}"""

# additional schema, only appended when the chapter contains tables/figures
ASSET_SCHEMA_DOC = """,
 "tables": [
   {"table": "<short caption/name of the table>",
    "table_ref": "<the table id exactly as shown above, e.g. ..._tab_017 -- the id, not the caption>",
    "status": "changed|new|removed",
    "change": "1-2 sentences: which rows/values/limits change (be concrete)",
    "value_changes": ["concrete value change(s) old->new, e.g. 'droop: 5 % -> 4 %'; [] if none"],
    "impact": "1 sentence on the practical impact ('' if none)",
    "evidence": "verbatim short quote from a table cell or caption (old or new)",
    "confidence": "high|medium|low"}
 ],
 "figures": [
   {"figure": "<short caption/name of the figure>",
    "status": "changed|new|removed",
    "change": "1-2 sentences: what changes in the figure's content/message",
    "impact": "1 sentence on the practical impact ('' if none)",
    "evidence": "verbatim short quote from the caption (old or new)",
    "confidence": "high|medium|low"}
 ]
}"""


# ------------------------------------------------------------------ field ownership
# Every field the pipeline knows itself is set by the pipeline (AP-07, ENT-51). A model
# that invents a key produces an error nobody downstream can recognize as one -- it looks
# like a missing chapter, which is how 165 interpretations of the 4110 run came to be
# uncheckable. So a pipeline-owned value that arrives in an answer is discarded and
# counted, never silently overwritten: the count is the only place that shows how often
# the model supplies something it was never asked for.
#
# Only ``section_id`` is actually part of the schema; everything else the model can
# supply unprompted alone. ``evidence_unique`` on a table or figure is the one value
# that used to survive, because check_asset_evidence does not compute it.

#: Pipeline-owned fields of a chapter answer.
PIPELINE_OWNED_CHAPTER = ("section_id", "mapping_id", "_source",
                          "_changes_total", "_changes_interpreted")

#: Pipeline-owned fields of a single interpretation.
PIPELINE_OWNED_INTERPRETATION = ("evidence_ok", "evidence_strict", "evidence_match_chars",
                                 "evidence_fragments", "evidence_unique",
                                 "evidence_span", "evidence_extraction",
                                 "change_index_best", "change_index_disputed",
                                 "structural_operation")

#: Pipeline-owned fields of a table or figure interpretation. ``table_id`` is the join
#: the pipeline resolves from what the answer names (AP-31): the model says which table
#: it means, the pipeline decides which table that is. A model-supplied id would be an
#: assertion nothing checks -- exactly the class of error ``section_id`` was.
PIPELINE_OWNED_ASSET = ("evidence_ok", "evidence_strict", "evidence_match_chars",
                        "evidence_fragments", "evidence_unique",
                        "evidence_span", "evidence_extraction",
                        "table_id", "values_checked", "values_found", "values_ok")


def drop_pipeline_owned(obj: dict, fields=PIPELINE_OWNED_INTERPRETATION) -> dict:
    """Remove every pipeline-owned field from a model answer; return what was removed.

    The return value is what makes the discarding visible -- see
    :func:`run_deutung`, which turns it into ``pipeline_feedback`` entries.
    """
    return {f: obj.pop(f) for f in fields if f in obj}


def label(value: str, language: str = "de") -> str:
    """Render a schema enum value in the output language (identity for English)."""
    return LABELS.get((language or "de").lower(), {}).get(value, value or "")


# ------------------------------------------------------------------ Key & Client
# Provider -> (default env var for the key, default base URL). All except
# "anthropic" are addressed via the OpenAI-compatible /chat/completions API
# (works for OpenAI, Azure, the Google Gemini OpenAI endpoint, Mistral, Groq,
# Together, OpenRouter, Ollama and many more).
PROVIDERS = {
    "anthropic":         ("ANTHROPIC_API_KEY",  "https://api.anthropic.com"),
    "openai":            ("OPENAI_API_KEY",      "https://api.openai.com/v1"),
    "azure_openai":      ("AZURE_OPENAI_API_KEY", None),   # base_url mandatory from config
    "google":            ("GOOGLE_API_KEY",      "https://generativelanguage.googleapis.com/v1beta/openai"),
    "mistral":           ("MISTRAL_API_KEY",     "https://api.mistral.ai/v1"),
    "groq":              ("GROQ_API_KEY",         "https://api.groq.com/openai/v1"),
    "together":          ("TOGETHER_API_KEY",     "https://api.together.xyz/v1"),
    "openrouter":        ("OPENROUTER_API_KEY",   "https://openrouter.ai/api/v1"),
    "ollama":            ("OLLAMA_API_KEY",        "http://localhost:11434/v1"),
    "openai_compatible": ("LLM_API_KEY",           None),   # generic, base_url from config
}


def resolve_key(root: Path, provider: str = "anthropic", key_env: str | None = None) -> str | None:
    """Key lookup: explicit env name -> provider default env -> generic LLM_API_KEY
    -> file .api_key. Ollama may run without a key (local server)."""
    default_env = PROVIDERS.get(provider, (None, None))[0]
    for var in (key_env, default_env, "LLM_API_KEY"):
        if var and os.environ.get(var):
            return os.environ[var].strip()
    for cand in (root / ".api_key", root.parent / "Pipeline" / ".api_key"):
        if cand.exists():
            return cand.read_text().strip()
    if provider == "ollama":
        return "ollama"       # local server, key often irrelevant
    return None


def cache_key(model: str, system: str, user: str) -> str:
    """The answer-cache key: SHA1 over model, system prompt and user prompt.

    Deliberately independent of the provider, so an already-cached answer stays valid
    when the backend changes. :class:`FixtureProvider` uses the same recipe -- a frozen
    answer is therefore bound to the exact prompt it was produced for.
    """
    return hashlib.sha1((model + "\x00" + system + "\x00" + user).encode()).hexdigest()


def load_json_answer(txt: str) -> tuple[dict, str]:
    """Parse an answer strictly, then leniently; report which of the two it took.

    ``json.loads(txt, strict=False)`` accepts a raw control character inside a string --
    3 of the 18 chapters the two production runs lost fail for exactly that reason and
    for no other (AP-18, question 6.2). The lenient pass is the **only** second attempt:
    cutting the text, balancing brackets or patching it with a regular expression would
    silently produce content nobody wrote.

    Returns:
        ``(data, OUTCOME_OK)`` or ``(data, OUTCOME_REPAIRED)``.
    Raises:
        json.JSONDecodeError: if neither pass reads the text.
    """
    try:
        return json.loads(txt), OUTCOME_OK
    except json.JSONDecodeError:
        return json.loads(txt, strict=False), OUTCOME_REPAIRED


def api_error_type(err: BaseException | None) -> str:
    """The error type the API itself reported, the exception class as a fallback.

    ``overloaded_error`` is worth a repeat run, ``invalid_request_error`` is not -- a
    generic "error" cannot tell the two apart (AP-18).
    """
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        reported = (body.get("error") or {}).get("type")
        if reported:
            return str(reported)
    return type(err).__name__ if err is not None else "unknown"


class LlmClient:
    def __init__(self, root: Path, model: str, cache_dir: Path, export_dir: Path,
                 provider: str = "anthropic", base_url: str | None = None,
                 key_env: str | None = None, api_version: str | None = None,
                 max_retries: int = 3, system: str | None = None):
        self.model = model
        self.system = system or build_system_prompt()
        self.provider = (provider or "anthropic").lower()
        self.cache_dir = cache_dir
        self.export_dir = export_dir
        self.max_retries = max_retries
        #: tag -> why this chapter has an answer, or has none (see :data:`ANSWER_OUTCOMES`)
        self.outcomes: dict[str, str] = {}
        #: How many answers arrived with a chain of thought although thinking was switched
        #: off (AP-27). Counted per *request*, retries included: every one of them is
        #: billed. See :meth:`_complete_openai_compatible`.
        self.n_reasoning = 0
        self.api_version = api_version         # Azure only
        cache_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url or PROVIDERS.get(self.provider, (None, None))[1]
        self.key = resolve_key(root, self.provider, key_env)
        self._anth = None
        self.live = False
        if not self.key:
            print(f"  [llm] Kein Key für Provider '{self.provider}' — Prompt-Export-Modus.")
            return
        if self.provider == "anthropic":
            try:
                import anthropic
                self._anth = anthropic.Anthropic(api_key=self.key)
                self.live = True
            except Exception as e:
                print(f"  [llm] anthropic-SDK nicht nutzbar ({type(e).__name__}: {e}) — Export-Modus.")
                return
        else:
            if not self.base_url:
                print(f"  [llm] Provider '{self.provider}': base_url fehlt (config: llm_base_url) — Export-Modus.")
                return
            self.live = True

    def _cache_key(self, user: str) -> Path:
        # IMPORTANT: the cache key is deliberately built only from model + system prompt +
        # user prompt (NOT the provider), so already-injected interpretations are
        # provider-independent. See :func:`cache_key`.
        return self.cache_dir / f"{cache_key(self.model, self.system, user)}.json"

    # ---- provider calls -------------------------------------------------
    def _complete_anthropic(self, user: str, max_tokens: int) -> tuple[str, str | None]:
        # NOTE: no `temperature` here -- newer Anthropic models (e.g. claude-sonnet-5)
        # reject it as deprecated (HTTP 400). Determinism is not guaranteed at the API level
        # anyway; the response cache keeps re-runs stable.
        # Disable extended thinking: newer models (e.g. claude-sonnet-5) think by default,
        # which for a structured JSON-extraction task only burns the token budget (the whole
        # `max_tokens` can be spent on a thinking block, leaving no answer text). We want the
        # answer directly. Older/cheaper models may not accept the `thinking` parameter, so
        # we fall back to a plain call if it is rejected.
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=self.system,
                      messages=[{"role": "user", "content": user}])
        try:
            m = self._anth.messages.create(thinking={"type": "disabled"}, **kwargs)
        except Exception as e:
            if "thinking" in str(e).lower():
                m = self._anth.messages.create(**kwargs)
            else:
                raise
        # collect all text blocks (a model may still prepend non-text blocks, so indexing
        # content[0] blindly can raise AttributeError); join their text robustly
        parts = [b.text for b in m.content if getattr(b, "type", None) == "text"]
        # the stop reason travels with the text: "max_tokens" means the answer was cut off,
        # which is a different defect from a malformed one (AP-18)
        return "".join(parts).strip(), getattr(m, "stop_reason", None)

    def _complete_openai_compatible(self, user: str, max_tokens: int) -> tuple[str, str | None]:
        import urllib.request
        url = self.base_url.rstrip("/") + "/chat/completions"
        if self.provider == "azure_openai":
            # Azure: base_url is the deployment path; api-version as a query param
            ver = self.api_version or "2024-06-01"
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api-version={ver}"
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            # Disable extended thinking, with the same value the Anthropic path sends.
            # DeepSeek thinks by default (effort "high"), which costs twice for a
            # structured extraction task: the chain of thought is billed as output, and
            # `temperature` -- 0 above, for reproducibility -- is ignored while thinking is
            # on. Providers that do not know the field ignore it; no per-provider special
            # case is made until one is observed to complain (AP-27).
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.provider == "azure_openai":
            headers["api-key"] = self.key
        else:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read().decode("utf-8"))
        choice = resp["choices"][0]
        message = choice["message"]
        # The provider thought anyway: the field is the chain of thought next to the answer,
        # and it is billed as output. Only counted -- never parsed, never cached: it holds
        # what the model discarded, not what it answered.
        if message.get("reasoning_content"):
            self.n_reasoning += 1
        # OpenAI calls the same condition "length"; translated here so the stage sees one
        # vocabulary regardless of the backend
        stop = "max_tokens" if choice.get("finish_reason") == "length" else choice.get("finish_reason")
        return message["content"].strip(), stop

    def _complete(self, user: str, max_tokens: int) -> tuple[str, str | None]:
        if self.provider == "anthropic":
            return self._complete_anthropic(user, max_tokens)
        return self._complete_openai_compatible(user, max_tokens)

    def _failed(self, tag: str, user: str, txt: str, outcome: str) -> None:
        """Record why a chapter has no answer and keep prompt *and* raw answer on disk.

        Both belong in the file: the truncated answer to 10.2.2 was only recognisable as
        truncated next to the prompt it belonged to (AP-18).
        """
        self.outcomes[tag] = outcome
        (self.export_dir / f"{tag}.FAILED.txt").write_text(
            f"OUTCOME: {outcome}\n\nSYSTEM:\n{self.system}\n\nUSER:\n{user}\n\nANSWER:\n{txt}",
            encoding="utf-8")

    def _parse_json_answer(self, txt: str, user: str, tag: str,
                           stop_reason: str | None = None) -> dict | None:
        """Strip code fences, parse JSON, cache on success; export the raw text on failure.

        A failure that follows ``stop_reason == "max_tokens"`` is reported as truncation,
        not as a parse error: only one of the two is cured by a larger budget.
        """
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", (txt or "").strip())
        try:
            data, outcome = load_json_answer(txt)
        except json.JSONDecodeError:
            self._failed(tag, user, txt,
                         OUTCOME_TRUNCATED if stop_reason == "max_tokens" else OUTCOME_UNPARSABLE)
            return None
        self.outcomes[tag] = outcome
        self._cache_key(user).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def ask_json_batch(self, items: list[tuple[str, str]],
                       max_tokens: int = MAX_ANSWER_TOKENS,
                       poll_s: int = 15) -> dict[str, dict | None]:
        """Resolve many prompts at once via the Anthropic Message Batches API (~50% cheaper).

        Cached prompts are served from the cache; only the remainder is submitted. Batches are
        asynchronous, so this polls until the batch has ended. Falls back to sequential calls
        for non-Anthropic providers or in export mode.

        Args:
            items: ``(tag, prompt)`` pairs, one per chapter.
        Returns:
            Mapping ``tag -> parsed JSON`` (``None`` where the answer was unusable).
        """
        out: dict[str, dict | None] = {}
        todo: list[tuple[str, str]] = []
        for tag, user in items:
            cp = self._cache_key(user)
            if cp.exists():
                out[tag] = json.loads(cp.read_text(encoding="utf-8"))
                self.outcomes[tag] = OUTCOME_OK
            else:
                todo.append((tag, user))
        if not todo:
            return out
        if not self.live or self.provider != "anthropic":
            for tag, user in todo:            # export mode / other providers: sequential
                out[tag] = self.ask_json(user, tag, max_tokens)
            return out

        # index-based custom_ids: the API only allows [a-zA-Z0-9_-]
        by_cid = {f"c{i}": (tag, user) for i, (tag, user) in enumerate(todo)}
        requests = [{"custom_id": cid,
                     "params": {"model": self.model, "max_tokens": max_tokens,
                                "system": self.system, "thinking": {"type": "disabled"},
                                "messages": [{"role": "user", "content": user}]}}
                    for cid, (_, user) in by_cid.items()]
        batch = self._anth.messages.batches.create(requests=requests)
        print(f"  [llm] Batch {batch.id}: {len(requests)} Kapitel eingereicht (~50 % günstiger). "
              f"Warte auf Verarbeitung …")
        while True:
            b = self._anth.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(poll_s)
        for r in self._anth.messages.batches.results(batch.id):
            tag, user = by_cid.get(r.custom_id, (None, None))
            if tag is None:
                continue
            if getattr(r.result, "type", None) != "succeeded":
                err = getattr(getattr(r.result, "error", None), "type", r.result.type)
                print(f"  [llm] Batch {tag}: {err} — extraktiver Fallback.")
                self.outcomes[tag] = OUTCOME_API_ERROR + str(err)
                out[tag] = None
                continue
            txt = "".join(bl.text for bl in r.result.message.content
                          if getattr(bl, "type", None) == "text")
            out[tag] = self._parse_json_answer(
                txt, user, tag, getattr(r.result.message, "stop_reason", None))
        for tag, _ in todo:                   # anything the API never returned
            if out.setdefault(tag, None) is None:
                self.outcomes.setdefault(tag, OUTCOME_API_ERROR + "no_result")
        return out

    def ask_json(self, user: str, tag: str,
                 max_tokens: int = MAX_ANSWER_TOKENS) -> dict | None:
        cp = self._cache_key(user)
        if cp.exists():
            self.outcomes[tag] = OUTCOME_OK
            return json.loads(cp.read_text(encoding="utf-8"))
        if not self.live:
            (self.export_dir / f"{tag}.txt").write_text(
                "SYSTEM:\n" + self.system + "\n\nUSER:\n" + user, encoding="utf-8")
            self.outcomes[tag] = OUTCOME_EXPORT
            return None
        txt = ""
        stop_reason = None
        last_error = None
        for attempt in range(self.max_retries):
            try:
                txt, stop_reason = self._complete(user, max_tokens)
                txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
                data, outcome = load_json_answer(txt)
                cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.outcomes[tag] = outcome
                return data
            except json.JSONDecodeError:
                if attempt == self.max_retries - 1:
                    self._failed(tag, user, txt,
                                 OUTCOME_TRUNCATED if stop_reason == "max_tokens"
                                 else OUTCOME_UNPARSABLE)
                    return None
            except Exception as e:
                last_error = e
                wait = 2 ** attempt * 5
                print(f"  [llm] {tag} [{self.provider}]: {type(e).__name__}, retry in {wait}s")
                time.sleep(wait)
        # every attempt raised: keep the API's own error type, not the word "error"
        self.outcomes[tag] = OUTCOME_API_ERROR + api_error_type(last_error)
        return None


# ------------------------------------------------------------------ provider seam
# The interpretation stage asks one question: "here are prompts, give me the answers".
# Everything else -- keys, retries, caching, batching -- is the provider's business.
# One method is therefore enough; ``live`` is the single flag the stage needs on top of
# it, because a run without answers reports itself as export mode.

@runtime_checkable
class DeutungProvider(Protocol):
    """Source of the per-chapter interpretations."""

    #: True when the provider actually produces answers (as opposed to prompt export).
    live: bool

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict | None]:
        """Answer every ``(tag, prompt)`` pair; ``None`` where no answer is available."""

    #: Optional: ``tag -> outcome`` (:data:`ANSWER_OUTCOMES`). A provider that does not
    #: keep them is read as :data:`OUTCOME_NO_ANSWER` for every missing answer -- the
    #: stage reports "no answer, reason unknown" rather than inventing one.


class MissingFixtureError(KeyError):
    """Raised when a :class:`FixtureProvider` has no frozen answer for a prompt."""

    def __str__(self) -> str:                 # KeyError would quote the message
        return self.args[0]


class LiveProvider:
    """The existing path: an :class:`LlmClient`, in single or batch mode.

    Behaviour is unchanged in every respect -- same prompts, same cache, same batch
    logic. The class only moves the call site behind an interface.
    """

    def __init__(self, root: Path, model: str, cache_dir: Path, export_dir: Path,
                 provider: str = "anthropic", base_url: str | None = None,
                 key_env: str | None = None, api_version: str | None = None,
                 batch: bool = False, system: str | None = None):
        self.client = LlmClient(root, model, cache_dir, export_dir, provider=provider,
                                base_url=base_url, key_env=key_env,
                                api_version=api_version, system=system)
        self.batch = batch

    @property
    def live(self) -> bool:
        return self.client.live

    @property
    def outcomes(self) -> dict[str, str]:
        return self.client.outcomes

    @property
    def n_reasoning(self) -> int:
        """Answers that carried a chain of thought despite thinking being off (AP-27)."""
        return self.client.n_reasoning

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict | None]:
        if self.batch:
            return self.client.ask_json_batch(items)
        return {tag: self.client.ask_json(prompt, tag=tag) for tag, prompt in items}


class FixtureProvider:
    """Frozen answers from a JSON file, keyed exactly like the ``llm_cache``.

    For tests only. A prompt without a frozen answer raises
    :class:`MissingFixtureError` naming the missing key: the provider invents nothing
    and never falls back silently to the extractive summary, which would let a test
    pass while measuring nothing.
    """

    live = True

    def __init__(self, path, model: str = "", system: str = ""):
        self.path = Path(path)
        self.model = model
        self.system = system
        self.answers: dict = json.loads(self.path.read_text(encoding="utf-8"))

    def key(self, prompt: str) -> str:
        """The ``llm_cache`` key of a prompt under this provider's model and system."""
        return cache_key(self.model, self.system, prompt)

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict | None]:
        out: dict[str, dict | None] = {}
        for tag, prompt in items:
            k = self.key(prompt)
            if k not in self.answers:
                raise MissingFixtureError(
                    f"no frozen answer for chapter {tag!r} (key {k}) in {self.path} -- "
                    "regenerate the fixture, the prompt has changed")
            out[tag] = self.answers[k]
        return out


# ------------------------------------------------------------------ prompt building
def _clip(t: str | None, n: int, mark: bool = False) -> str:
    """``t`` shortened to ``n`` characters, with an ellipsis where it was cut.

    ``mark=True`` also says *how much* is missing -- ``… (gekürzt, +2057 Zeichen)``, the
    shape the row cap of :func:`_render_cells` has always used. Only the caps of AP-20
    use it; the other callers (change text, chapter excerpt) keep the bare ellipsis until
    their own package raises them.
    """
    t = (t or "").strip()
    if len(t) <= n:
        return t
    return t[:n] + (f" … (gekürzt, +{len(t) - n} Zeichen)" if mark else " …")


def _change_block(i: int, c: dict) -> str:
    L = [f"[{i}] TYP={c['kind']}"]
    if c.get("modality"):
        mod = c["modality"]
        if "shift" in mod:
            L.append(f"    Modalität: {mod.get('old')} → {mod.get('new')} ({mod['shift']})")
        elif mod.get("new"):
            L.append(f"    Modalität (neu): {mod['new']}")
        elif mod.get("old"):
            L.append(f"    Modalität (alt): {mod['old']}")
    kw = c.get("kennwerte") or {}
    parts = [f"{ch['old']['raw']} → {ch['new']['raw']}" for ch in kw.get("changed", [])]
    parts += [f"+{a['raw']}" for a in kw.get("added", [])[:6]]
    parts += [f"−{r['raw']}" for r in kw.get("removed", [])[:6]]
    if parts:
        L.append(f"    Kennwerte: {'; '.join(parts)}")
    refs = c.get("refs") or {}
    ri = [f"+{r}" for r in refs.get("internal_added", [])] + \
         [f"−{r}" for r in refs.get("internal_removed", [])]
    if ri:
        L.append(f"    Querverweise (intern): {'; '.join(ri[:10])}")
    rparts = [f"+{r}" for r in refs.get("external_added", [])] + \
             [f"−{r}" for r in refs.get("external_removed", [])]
    if rparts:
        L.append(f"    Referenzen: {'; '.join(rparts[:8])}")
    if c.get("moved_from"):
        L.append(f"    (hierher verschoben aus {c['moved_from']})")
    if c.get("moved_to"):
        L.append(f"    (verschoben nach {c['moved_to']})")
    elif c.get("moved_to_chapter"):
        # AP-26 block continuation: the chapter is proven, the paragraph is not
        L.append(f"    (verschoben nach Kapitel {c['moved_to_chapter']})")
    if c.get("old_text"):
        L.append(f"    ALT: {_clip(c['old_text'], 900)}")
    if c.get("new_text"):
        L.append(f"    NEU: {_clip(c['new_text'], 900)}")
    return "\n".join(L)


def _tabmap(secs: dict) -> dict:
    """id -> table object across all sections (secs: {section_id: section})."""
    m = {}
    for s in secs.values():
        for t in s.get("tables") or []:
            m[t["id"]] = t
    return m


def _render_cells(t: dict, max_rows: int = 40, max_chars: int = TABLE_CHARS,
                  notes: list | None = None) -> str:
    """The cell text of one table for the prompt, capped at ``max_chars``.

    ``notes`` collects one record per rendered table -- clipped or not, so the report can
    say "0 of 233" and mean a measured zero (AP-20).
    """
    rows = t.get("cells") or []
    buf = [" | ".join((c or "").strip() for c in r) for r in rows[:max_rows]]
    body = " ‖ ".join(buf)
    if len(rows) > max_rows:
        body += f" … (+{len(rows) - max_rows} Zeilen)"
    body = body.strip()
    if notes is not None:
        notes.append({"kind": "table", "table_id": t.get("id"), "n_chars": len(body),
                      "n_dropped": max(len(body) - max_chars, 0)})
    return _clip(body, max_chars, mark=True)


def _figs(secs: dict, ids) -> list[dict]:
    out = []
    for sid in ids or []:
        s = secs.get(sid)
        if s:
            out += s.get("figures") or []
    return out


def _paras(secs: dict, ids) -> list[str]:
    """Paragraph text of the given sections -- the body a figure caption refers to."""
    out = []
    for sid in ids or []:
        s = secs.get(sid)
        if s:
            out += [p.get("n1") or p.get("n0") or "" for p in s.get("paragraphs") or []]
    return out


def chapter_assets_block(ch: dict, o_secs: dict, n_secs: dict,
                         max_chars: int = ASSET_BLOCK_CHARS,
                         notes: list | None = None) -> str:
    """Text extract of the chapter tables (with cell contents) and figure captions,
    paired via ch['tables_diff']. Empty string if the chapter has no assets.

    ``notes`` collects a record per rendered table and one for the block itself; see
    :func:`truncation_report` for what is made of them.
    """
    om, nm = _tabmap(o_secs), _tabmap(n_secs)
    tlines = []
    for td in ch.get("tables_diff") or []:
        cap = (td.get("caption") or "").strip() or "(ohne Caption)"
        # AP-31: the id leads, the caption follows. A caption is free text and no key --
        # measured over the three reference runs it joins 70 % / 68 % / 0 % of the table
        # interpretations, and what the remainder measures is the text rule, not the data.
        tid = table_key(td) or "(ohne Id)"
        cap = f"{tid} — {cap}"
        kind = td.get("kind")
        if kind == "matched":
            if td.get("identical"):
                continue
            ot, nt = om.get(td.get("old")), nm.get(td.get("new"))
            tlines.append(f"  [GEÄNDERT] {cap}  (geänderte Zeilen: {td.get('rows_changed', '?')})")
            if ot:
                tlines.append("    ALT: " + _render_cells(ot, notes=notes))
            if nt:
                tlines.append("    NEU: " + _render_cells(nt, notes=notes))
        elif kind == "new":
            nt = nm.get(td.get("new"))
            tlines.append(f"  [NEU] {cap}")
            if nt:
                tlines.append("    NEU: " + _render_cells(nt, notes=notes))
        elif kind == "removed":
            ot = om.get(td.get("old"))
            tlines.append(f"  [ENTFALLEN] {cap}")
            if ot:
                tlines.append("    ALT: " + _render_cells(ot, notes=notes))
        elif kind == "moved_in":
            # AP-23: the table came from another chapter -- its cells belong in the prompt
            # here, where it now stands, as they did when it was reported as an addition
            nt = nm.get(td.get("new"))
            tlines.append(f"  [VERSCHOBEN AUS {td.get('moved_from_chapter')}] {cap}"
                          f"  (geänderte Zeilen: {td.get('rows_changed', '?')})")
            if nt:
                tlines.append("    NEU: " + _render_cells(nt, notes=notes))
        elif kind == "moved_away":
            ot = om.get(td.get("old"))
            tlines.append(f"  [VERSCHOBEN NACH {td.get('moved_to_chapter')}] {cap}")
            if ot:
                tlines.append("    ALT: " + _render_cells(ot, notes=notes))
    fo, fn = _figs(o_secs, ch.get("old_ids")), _figs(n_secs, ch.get("new_ids"))
    flines = []
    if fo:
        flines.append("  BILDER ALT: " + " ‖ ".join(_clip(f.get("caption") or "", 160) for f in fo))
    if fn:
        flines.append("  BILDER NEU: " + " ‖ ".join(_clip(f.get("caption") or "", 160) for f in fn))
    if not tlines and not flines:
        return ""
    block = ["", "TABELLEN & BILDER (Struktur-Extrakt — enthält Kennwerte/Grenzwerte; "
             "für die Deutung berücksichtigen):"]
    if tlines:
        block += [" TABELLEN (je Zeile: Id — Beschriftung; in 'table' die Id nennen):"] + tlines
    if flines:
        block += [" BILDER:"] + flines
    s = "\n".join(block)
    dropped = max(len(s) - max_chars, 0)
    if notes is not None:
        notes.append({"kind": "asset_block", "table_id": None, "n_chars": len(s),
                      "n_dropped": dropped})
    return s if not dropped else s[:max_chars] + f"\n  … (Block gekürzt, +{dropped} Zeichen)"


def asset_haystack(ch: dict, o_secs: dict, n_secs: dict) -> str:
    """Text a table or figure interpretation may quote from.

    Captions and cells, **and the chapter's paragraph text**. The body belongs in here:
    a figure interpretation legitimately quotes the sentence that references the figure
    ("innerhalb der FRT-Grenzkurven nach Bild 17 aktiv"), and that sentence lives in the
    running text, not in a caption. Reviewing the 4110 run showed ten such quotes present
    verbatim in the document and rejected only because the haystack stopped at the
    caption.

    The scope stays the chapter -- wide enough for the referencing sentence, narrow
    enough that a quote from an unrelated chapter still fails.
    """
    om, nm = _tabmap(o_secs), _tabmap(n_secs)
    parts = []
    for td in ch.get("tables_diff") or []:
        for m, k in ((om, "old"), (nm, "new")):
            t = m.get(td.get(k))
            if t:
                parts.append(t.get("caption") or "")
                for r in t.get("cells") or []:
                    parts.append(" ".join(r))
    for f in _figs(o_secs, ch.get("old_ids")) + _figs(n_secs, ch.get("new_ids")):
        parts.append(f.get("caption") or "")
    parts += _paras(o_secs, ch.get("old_ids")) + _paras(n_secs, ch.get("new_ids"))
    return n2(" ".join(parts)).lower()


def check_asset_evidence(item: dict, hay: str) -> dict:
    """Evidence guard for table/figure interpretations, see :func:`check_evidence`."""
    return _guarded(item.get("evidence") or "", hay, _asset_evidence_ok)


# ------------------------------------------------------------------ values in cells
# AP-31. The evidence guard checks the *quote* of a table interpretation; the values in
# its ``value_changes`` were checked by nobody -- 138 / 97 / 6 statements over the three
# reference runs against 9 / 8 / 0 the deterministic stage knows. They are checked here
# against the cells of the table the entry names, and the result is written on the entry.
# Marked, never corrected (AP-07, AP-29): the text stays as delivered.

def table_key(td: dict) -> str | None:
    """The id a table diff entry is addressed by: the new side, else the old one.

    One identity per table, so an entry can name it with a single string. For a pair
    both cells sets are reachable from the entry (see :func:`check_asset_values`).
    """
    return td.get("new") or td.get("old")


def _norm_caption(text) -> str:
    """A caption reduced to case and whitespace -- the pre-AP-31 join, nothing more."""
    return " ".join(str(text or "").split()).lower()


def resolve_table(item: dict, ch: dict) -> dict | None:
    """The ``tables_diff`` entry a table interpretation names, ``None`` if none.

    Since AP-31 the prompt names the table by its id and the schema asks for that id back
    in ``table_ref``, so the join is an identity comparison and not a text rule. ``table``
    keeps the caption: it is what the deliverables print, and an id is not a name.

    The caption remains a **fallback**, and only as an exact match after normalizing case
    and whitespace, and only when it is unique in the chapter: answers cached before
    AP-31 carry no ``table_ref``, and dropping them would make every earlier run
    unmeasurable. A caption that fits two tables resolves to neither -- an ambiguous join
    would put the check on a table the entry may not mean.
    """
    named = str(item.get("table_ref") or item.get("table") or "").strip()
    tds = ch.get("tables_diff") or []
    if not named or not tds:
        return None
    for td in tds:
        key = table_key(td)
        if key and (named == key or key in named):
            return td
    norm = _norm_caption(named)
    hits = [td for td in tds if norm and _norm_caption(td.get("caption")) == norm]
    return hits[0] if len(hits) == 1 else None


#: What a model writes between the old and the new value of a change.
_VALUE_ARROW = re.compile(r"\s*(?:-+>|=+>|→|➔|➝|»)\s*")

#: Sides a value in a ``value_changes`` entry is looked for on.
SIDE_OLD, SIDE_NEW, SIDE_BOTH = "old", "new", "both"


def value_change_sides(text: str) -> list[tuple[str, str]]:
    """Split one ``value_changes`` entry into ``(side, text)`` pairs.

    ``"droop: 5 % -> 4 %"`` yields the old and the new side, and each is then looked for
    in the cells of *its* edition. Without an arrow the entry says nothing about which
    edition it describes ("Zeile für 0,85 Un entfernt"), so the value is looked for on
    both sides: charging it to one of them would fail a value that is demonstrably there.
    """
    parts = _VALUE_ARROW.split(text or "", maxsplit=1)
    if len(parts) == 2:
        return [(SIDE_OLD, parts[0]), (SIDE_NEW, parts[1])]
    return [(SIDE_BOTH, text or "")]


def _cell_keys(table: dict | None) -> set[tuple[str, str]]:
    """``(base_unit, base_value)`` of every value in a table's cells.

    Reads the ``cell_values`` written by the enrichment since AP-31 and computes them on
    the fly where the field is absent, so a ``norm_doc.json`` from an earlier run is
    checked exactly like a current one instead of counting as unchecked.
    """
    if not table:
        return set()
    recs = table.get("cell_values")
    if recs is None:
        recs = values.cell_values(table.get("cells"))
    return values.value_keys(recs)


def check_asset_values(item: dict, td: dict | None, o_tabs: dict, n_tabs: dict) -> dict:
    """Check the values of a table interpretation against the cells of its table.

    Returns the four fields written onto the entry:

    ``table_id``
        the table the entry was joined to, ``None`` when it names none.
    ``values_checked``
        how many values were recognized in ``value_changes``. An entry without a
        recognizable number ("Zeile entfällt") has nothing to check, and that is no
        error -- it is counted as 0, not as a failure.
    ``values_found``
        how many of them appear in the cells of their edition.
    ``values_ok``
        whether all recognized values were found.

    All three counts are ``None`` when no table could be joined: such an entry is
    reported as **unchecked**, never as wrong. Counting it as a failure would measure the
    join instead of the statement.
    """
    if td is None:
        return {"table_id": None, "values_checked": None, "values_found": None,
                "values_ok": None}
    old_keys = _cell_keys(o_tabs.get(td.get("old")))
    new_keys = _cell_keys(n_tabs.get(td.get("new")))
    haystacks = {SIDE_OLD: old_keys, SIDE_NEW: new_keys, SIDE_BOTH: old_keys | new_keys}
    checked = found = 0
    for entry in item.get("value_changes") or []:
        for side, part in value_change_sides(str(entry)):
            for v in values.extract_values(n1(part)):
                checked += 1
                if (v["base_unit"], v["base_value"]) in haystacks[side]:
                    found += 1
    return {"table_id": table_key(td), "values_checked": checked,
            "values_found": found, "values_ok": found == checked}


#: Why a table or figure interpretation needs a human look. ``evidence_ok`` is the
#: historical trigger and keeps its meaning; ``values_ok`` is added by AP-31 and fires
#: only on a **checked** entry -- an unchecked one is not a failure.
ASSET_REVIEW_REASONS = ("evidence_ok", "values_ok")


def asset_review_reasons(item: dict) -> list[str]:
    """The reasons a table or figure interpretation is in the review queue."""
    return [r for r in ASSET_REVIEW_REASONS if item.get(r) is False]


#: Separators a rendered table row is built from -- see :func:`_render_cells`.
_ZELLTRENNER = re.compile(r"\s*[|‖]\s*")

#: Below this length a cell says nothing: "V", "1", "kW" match almost any table.
_ZELLE_MIN = 3


def _asset_evidence_ok(ev: str, hay: str) -> bool:
    """Whether a table or figure quote is covered by ``hay``.

    Two shapes have to pass, and they need different treatment:

    *Prose* -- a sentence from a caption or from the body. Checked verbatim, as before
    (whole quote, or its first 40 characters).

    *A rendered table row* -- ``"Punkt | Zeit | Schritt | V"``. This never occurs
    verbatim anywhere: the separators are added when the table is rendered for the
    prompt, the source only has the cells. Such a quote is split again and every cell
    of at least three characters has to be present. Shorter cells are dropped rather
    than counted -- "V" or "1" matches nearly any table and would let a wrong row pass.

    Deliberately **not** covered: a quote whose words all occur but in a different order.
    German moves the verb ("... reagieren müssen" versus "müssen ... reagieren"), and the
    model sometimes normalises it. Accepting that would mean accepting any permutation,
    including one that reverses the statement. Those stay a failed check and land in the
    review queue, where quote and source sit side by side.
    """
    if len(ev) < 8:
        return False
    if ev in hay or ev[:40] in hay:
        return True
    zellen = [z for z in (s.strip() for s in _ZELLTRENNER.split(ev)) if len(z) >= _ZELLE_MIN]
    if len(zellen) < 2:
        return False
    return all(z in hay for z in zellen)


def changes_by_priority(ch: dict) -> list[int]:
    """The chapter's change indices, most relevant first.

    Value change +4, modality shift +3, a new or dropped ``muss``/``darf nicht`` +3, a
    move +1, length up to +1, semantically equal -5. Unchanged since the stage exists:
    the order decides which changes a run asks about first, and splitting a chapter over
    several requests (AP-19) must not disturb it.
    """
    idx = list(range(len(ch["changes"])))

    def prio(i):
        c = ch["changes"][i]
        p = 0.0
        if c.get("kennwerte", {}).get("changed"):
            p += 4
        if (c.get("modality") or {}).get("shift") in ("verschaerft", "gelockert"):
            p += 3
        if c["kind"] in ("new", "removed") and (c.get("modality", {}).get("new") in ("muss", "darf_nicht")
                                                or c.get("modality", {}).get("old") in ("muss", "darf_nicht")):
            p += 3
        if c["kind"] in ("moved_in", "moved_away"):
            p += 1
        p += min(len(c.get("old_text") or "") + len(c.get("new_text") or ""), 2000) / 2000
        if c.get("semantic_equal"):
            p -= 5
        return -p
    idx.sort(key=prio)
    return idx


def render_chapter_prompt(ch: dict, old_excerpt: str, new_excerpt: str, sel: list[int],
                          o_secs: dict | None = None, n_secs: dict | None = None,
                          part: int = 1, n_parts: int = 1,
                          notes: list | None = None) -> str:
    """One request about the changes ``sel`` of this chapter.

    ``part``/``n_parts`` name the block in the counter line for a split chapter. Part 1
    renders **exactly** as an unsplit chapter does -- no part marker, and the assets --
    so that a chapter which is split today still hits the answer cache of a run made
    before the split (AP-19).
    """
    cid = ch.get("new_id") or ch.get("old_id")
    of_parts = f", Teil {part} von {n_parts}" if part > 1 else ""
    head = [f"KAPITEL {cid} — {ch['title']}  (Teil: {ch['part']}"
            + (", NORMATIV/INFORMATIV-STATUS GEÄNDERT!" if ch.get("part_changed") else "") + ")",
            f"Kapitel-Modus: {ch['mode']}; nicht geänderte Absätze: {ch.get('n_identical', 0)}",
            "", "AUSZUG ALTE FASSUNG:", _clip(old_excerpt, 1600) or "(Kapitel existiert in der alten Fassung nicht)",
            "", "AUSZUG NEUE FASSUNG:", _clip(new_excerpt, 1600) or "(Kapitel in der neuen Fassung entfallen)",
            "", (f"ÄNDERUNGEN ({len(sel)} von {len(ch['changes'])}{of_parts}, "
                 "deterministisch ermittelt):")]
    for i in sel:
        head.append(_change_block(i, ch["changes"][i]))
    # tables and figures travel with block 1 only: asked in every block they would be
    # interpreted n times, and the merge would have to undo that for no gain
    assets = (chapter_assets_block(ch, o_secs, n_secs, notes=notes)
              if (o_secs is not None and n_secs is not None and part == 1) else "")
    if assets:
        head.append(assets)
    from .keywords import TAXONOMY
    if assets:
        schema = CHAPTER_SCHEMA_DOC.rstrip()[:-1].rstrip() + ASSET_SCHEMA_DOC
        task = ("AUFGABE: Erzeuge das folgende JSON (deutungen für ALLE oben gelisteten Indizes; "
                "zusätzlich die Tabellen und Bilder oben in 'tables'/'figures' deuten):")
    else:
        schema = CHAPTER_SCHEMA_DOC
        task = "AUFGABE: Erzeuge das folgende JSON (deutungen für ALLE oben gelisteten Indizes):"
    head += ["", "VERFÜGBARE SCHLAGWORTE (wähle 1-4 passende): " + " | ".join(TAXONOMY.keys()),
             "", task, schema]
    return "\n".join(head)


def build_chapter_prompts(ch: dict, old_excerpt: str, new_excerpt: str,
                          o_secs: dict | None = None, n_secs: dict | None = None,
                          max_changes: int = MAX_CHANGES_PER_REQUEST,
                          notes: list | None = None) -> list[tuple[str, list[int]]]:
    """Every request this chapter needs: ``(prompt, included change indices)`` per block.

    The changes are ordered by priority and then cut into blocks of ``max_changes``, so
    block 1 holds the same most relevant changes as before and every further change ends
    up in exactly one later block instead of being dropped (AP-19). A chapter that fits
    into one request yields one block, character-identical to the previous prompt.
    """
    order = changes_by_priority(ch)
    blocks = [sorted(order[i:i + max_changes]) for i in range(0, len(order), max_changes)]
    blocks = blocks or [[]]              # a chapter without changes still asks once
    return [(render_chapter_prompt(ch, old_excerpt, new_excerpt, sel, o_secs, n_secs,
                                   part=n + 1, n_parts=len(blocks), notes=notes), sel)
            for n, sel in enumerate(blocks)]


def build_chapter_prompt(ch: dict, old_excerpt: str, new_excerpt: str,
                         o_secs: dict | None = None, n_secs: dict | None = None,
                         max_changes: int = MAX_CHANGES_PER_REQUEST) -> tuple[str, list[int]]:
    """Prompt + list of included change indices (priority: value/modality/new).

    The first block of :func:`build_chapter_prompts`, i.e. the whole chapter as long as it
    fits into one request. Kept for callers that ask one question per chapter.
    """
    return build_chapter_prompts(ch, old_excerpt, new_excerpt, o_secs, n_secs,
                                 max_changes)[0]


class ChapterJob(NamedTuple):
    """Everything one chapter asks for: its blocks, their tags and the excerpts."""

    ch: dict
    section_id: str
    mapping_id: str
    tags: list[str]
    parts: list[tuple[str, list[int]]]
    old_excerpt: str
    new_excerpt: str


def section_index(doc: dict) -> dict:
    """``{section id: section}`` of a ``norm_doc.json``."""
    return {s["id"]: s for s in doc["sections"]}


def section_excerpt(secs: dict, ids) -> str:
    """The paragraph text of the given sections, as it goes into a prompt."""
    txt = []
    for sid in ids or []:
        s = secs.get(sid)
        if s:
            txt.append(" ".join(p.get("n1", p.get("n0", "")) for p in s["paragraphs"]))
    return " ".join(txt)


def chapter_jobs(synopse: dict, o_secs: dict, n_secs: dict, scope: str = "core",
                 asset_notes: list | None = None) -> list[ChapterJob]:
    """Every request a run over this synopse would send, in the order it sends them.

    Phase 1 of :func:`run_deutung`, and the only place that decides it: a chapter with
    more changes than fit into one request is split into several (AP-19), and the tag
    routes the answer back to its chapter and names the exported prompt file, so it has
    to be unique -- it is built from the mapping id (ENT-24), a later block appends its
    number. ``asset_notes`` collects what the table budgets cut, per chapter.

    Separate from :func:`run_deutung` so that a tool outside the pipeline can ask what
    the pipeline would ask, character for character, instead of rebuilding it (AP-21).
    """
    jobs: list[ChapterJob] = []
    for ch in synopse["chapters"]:
        substantive = [c for c in ch["changes"] if not c.get("semantic_equal")]
        if scope == "core" and not substantive and not ch.get("part_changed"):
            continue
        old_x = section_excerpt(o_secs, ch.get("old_ids"))
        new_x = section_excerpt(n_secs, ch.get("new_ids"))
        notes: list[dict] = []
        parts = build_chapter_prompts(ch, old_x, new_x, o_secs, n_secs, notes=notes)
        cid = ch.get("new_id") or ch.get("old_id")
        mid = ch.get("mapping_id") or str(cid)
        tag = re.sub(r"[^\w.]", "_", mid)
        tags = [tag if n == 0 else f"{tag}.T{n + 1}" for n in range(len(parts))]
        # the chapter a clipped table belongs to is known here, not down in the renderer
        if asset_notes is not None:
            asset_notes += [{"section_id": cid, "mapping_id": mid, **note} for note in notes]
        jobs.append(ChapterJob(ch, cid, mid, tags, parts, old_x, new_x))
    return jobs


def answered_indices(data: dict | None, n_changes: int) -> list[int]:
    """The change indices this chapter answer actually carries an interpretation for.

    Counted **after** the merge and over the ``change_index`` values that are really
    there -- not over the selection the prompts were built from (AP-28). The two are
    equal only as long as a model answers about every change it is shown; in the 60909
    run of 2026-08-22, 77 of 1897 changes came back without one and the coverage still
    reported 100 %.

    An index outside ``range(n_changes)`` belongs to no change of this chapter and is
    dropped here; ``change_index_check`` reports what the model addressed.
    """
    if not data:
        return []
    return sorted({d.get("change_index") for d in (data.get("interpretations") or [])
                   if isinstance(d.get("change_index"), int)
                   and 0 <= d["change_index"] < n_changes})


def merge_chapter_answers(blocks: list[tuple[dict | None, list[int]]]
                          ) -> tuple[dict | None, list[int], int]:
    """Fold the answers of one chapter's blocks into one chapter interpretation.

    Merged by ``change_index``; if two blocks claim the same one, the **earlier** block
    wins -- it was shown the higher-priority changes -- and the collision is counted.
    Chapter-level fields (summaries, tables, figures) come from the first block that
    answered at all.

    Returns:
        ``(interpretation or None, change indices the answering blocks were shown,
        collisions)``. What of that was answered is :func:`answered_indices` -- the
        second element says what was *asked*, and asking is not answering.
    """
    merged: dict | None = None
    seen: dict = {}
    interpreted: list[int] = []
    collisions = 0
    for data, sel in blocks:
        if data is None:
            continue
        if merged is None:
            merged = dict(data)
            merged["interpretations"] = []
        interpreted += sel
        for d in data.get("interpretations") or []:
            key = d.get("change_index")
            if key is not None and key in seen:
                collisions += 1
                continue
            if key is not None:
                seen[key] = d
            merged["interpretations"].append(d)
    return merged, sorted(set(interpreted)), collisions


# ------------------------------------------------------------------ Fallback & Guard
def extractive_summary(text: str, n: int = 3) -> str:
    sents = [s for s in split_sentences(text) if len(s) > 40]
    return " ".join(sents[:n])


# Ellipsis marks a model writes into a supposedly verbatim quote: U+2026 and three
# dots, each also in brackets, with any surrounding whitespace.
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…|\.\.\.)\s*")


def evidence_fragments(ev: str) -> list[str]:
    """Split a quote at ellipsis marks; fragments empty after trimming are dropped."""
    return [f for f in (part.strip() for part in _ELLIPSIS.split(ev)) if f]


def _matched_chars(fragment: str, hay: str) -> int:
    """Length of the longest prefix of ``fragment`` that occurs verbatim in ``hay``.

    Binary search: prefix containment is monotone -- if ``fragment[:n]`` is present,
    every shorter prefix is present too.
    """
    lo, hi = 0, len(fragment)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fragment[:mid] in hay:
            lo = mid
        else:
            hi = mid - 1
    return lo


def evidence_metrics(ev: str, hay: str) -> dict:
    """The three diagnostic quantities next to ``evidence_ok`` (ENT-18, ENT-19).

    ``evidence_strict``
        every fragment of the quote occurs completely in ``hay``.
    ``evidence_match_chars``
        characters actually covered, summed over the fragments -- five fragments of
        eight characters are worth less than one contiguous span of forty.
    ``evidence_fragments``
        number of fragments after splitting at the ellipsis marks.

    The split affects these three values only; ``evidence_ok`` keeps its historical
    definition so that the baseline stays comparable.
    """
    frags = evidence_fragments(ev)
    matched = [_matched_chars(f, hay) for f in frags]
    return {
        "evidence_strict": bool(frags) and all(m == len(f) for m, f in zip(matched, frags)),
        "evidence_match_chars": sum(matched),
        "evidence_fragments": len(frags),
    }


# ------------------------------------------------------------------ quote extraction
# The guard used to check an answer as delivered, which measures formatting discipline
# instead of the ability to cite: on ``out/4110_haiku`` 21.7 % of the answers passed as
# delivered and 79.9 % once the quoted span was located -- 58 points lost to a label a
# model puts in front of a correct quote. Under quote-first [Gua26] the *system*
# localizes the span; it does not demand a pre-cleaned one from the model.
#
# Extraction runs on the n2()-normalized answer. n2 already unifies the typographic
# quotation marks („ “ ‚ ‘ » «) into their straight equivalents, so three patterns
# cover the six pairs -- including the mismatched ‚...' that Haiku writes in 300 of its
# 1612 answers.

#: Below this many characters a span is not drawn as a candidate. Without the floor a
#: quote around an arbitrary word would pass: the short-quote branch of
#: :func:`_evidence_ok` accepts a quote that covers a short record completely. The
#: floor bounds the *extraction*; the short-quote rule itself is untouched.
MIN_SPAN_CHARS = 15

#: Quotation mark pairs after n2(): straight double, straight single, and the single
#: guillemets, which n2 leaves alone. The character class excludes both marks, so a
#: match ends at the first closing mark.
_QUOTED = tuple(re.compile(re.escape(o) + "([^" + re.escape(o + c) + "]"
                           + "{" + str(MIN_SPAN_CHARS) + ",})" + re.escape(c))
                for o, c in (('"', '"'), ("'", "'"), ("›", "‹")))

#: A label a model puts in front of its quote. Closed list; ``:`` binds directly, a
#: dash needs spaces around it so that "Alt-Anlagen" stays a word (n2 has turned every
#: en dash into a hyphen by the time this runs).
_LABEL = re.compile(r"^(?:alte fassung|neue fassung|alt|neu|vorher|nachher)"
                    r"(?::\s*|\s+[-–]\s+)", re.IGNORECASE)


def evidence_candidates(answer: str) -> list[tuple[str, str]]:
    """Spans of a model answer that may carry the quote, with how each was won.

    In the order they are checked:

    ``roh``
        the whole answer -- what the guard did before AP-08. It stays the first
        candidate so that an answer passing today keeps passing, with the same span
        and the same numbers.
    ``anfuehrung``
        every span between quotation marks, in the order they appear. Several are
        possible and all are checked: an answer of the form ``ALT: '...'; NEU: '...'``
        quotes two versions, and either may be the one the record carries.
    ``etikett``
        the answer with a leading label removed -- only when there are no quotation
        marks, otherwise the label already sits in front of the first span.

    Spans shorter than :data:`MIN_SPAN_CHARS` are dropped; the whole answer never is.
    """
    text = n2(answer).strip()
    out = [(text, "roh")]
    quoted = sorted((m.start(), m.group(1).strip())
                    for p in _QUOTED for m in p.finditer(text))
    if quoted:
        out += [(s, "anfuehrung") for _, s in quoted if len(s) >= MIN_SPAN_CHARS]
    else:
        stripped = _LABEL.sub("", text, count=1).strip()
        if stripped != text and len(stripped) >= MIN_SPAN_CHARS:
            out.append((stripped, "etikett"))
    return out


def _guarded(answer: str, hay: str, ok) -> dict:
    """Run the guard over every candidate span; the first one that passes wins.

    ``evidence_span`` is the span the check passed with, ``evidence_extraction`` how
    it was won. The second field is what makes visible how often extraction was needed
    at all: for Sonnet it is nearly always ``roh``, and a rise reports a change in the
    model's or the prompt's shape rather than in its content.

    Where no candidate passes, the one covering the most characters is reported (ties
    to the earliest, so the whole answer wins over a span that covers no more). The
    verdict is the same either way; this only decides which span the diagnostics
    describe.
    """
    best = None
    for span, mode in evidence_candidates(answer):
        ev = span.lower()
        res = {"evidence_ok": ok(ev, hay), **evidence_metrics(ev, hay),
               "evidence_span": span, "evidence_extraction": mode}
        if res["evidence_ok"]:
            return res
        if best is None or res["evidence_match_chars"] > best["evidence_match_chars"]:
            best = res
    return best


def _record_haystack(c: dict) -> str:
    return " ".join(n2(c.get(k) or "").lower() for k in ("old_text", "new_text"))


def change_record(deutung: dict, ch: dict) -> dict | None:
    """The change record an interpretation addresses, ``None`` if its index names none.

    The one place that reads ``change_index``: an absent or out-of-range index yields
    no record, and everything built on it (the haystack of the evidence guard, axis A)
    fails closed instead of describing a foreign change.
    """
    i = deutung.get("change_index")
    if isinstance(i, int) and 0 <= i < len(ch["changes"]):
        return ch["changes"][i]
    return None


def change_haystack(deutung: dict, ch: dict) -> str:
    """Old and new text of the addressed change record, normalized and lower case."""
    c = change_record(deutung, ch)
    return _record_haystack(c) if c is not None else ""


def _evidence_unique(ev: str, deutung: dict, ch: dict) -> bool:
    """True when the quote is strict in the addressed record and in no other (ENT-30).

    A marker, not a gate: a quote that also fits a foreign record of the same chapter
    is correct as often as not, but it cannot localize the interpretation -- and that
    is the upper bound for undetectable ``change_index`` errors.
    """
    i = deutung.get("change_index")
    for j, c in enumerate(ch["changes"]):
        if j == i:
            continue
        if evidence_metrics(ev, _record_haystack(c))["evidence_strict"]:
            return False
    return True


def check_evidence(deutung: dict, ch: dict) -> dict:
    """Check an interpretation's quote against its change record.

    Returns the seven fields written into ``deutung.json``: ``evidence_ok`` (its rule
    unchanged), the diagnostics of :func:`evidence_metrics`, ``evidence_unique``
    (ENT-30) and the two of the extractor, ``evidence_span`` and
    ``evidence_extraction`` (AP-08).

    Every one of them is computed on the *extracted span*, not on the answer as
    delivered -- see :func:`evidence_candidates` for why.
    """
    hay = change_haystack(deutung, ch)
    res = _guarded(deutung.get("evidence") or "", hay, _evidence_ok)
    res["evidence_unique"] = (res["evidence_strict"]
                              and _evidence_unique(res["evidence_span"].lower(),
                                                   deutung, ch))
    return res


#: Why an interpretation is in the review queue, in a fixed order. The first two are the
#: historical triggers and keep their meaning exactly (ENT-18); ``change_index_disputed``
#: is added by AP-07, the last two by AP-29. The queue is extended, not rebuilt.
REVIEW_REASONS = ("contradiction_flag", "evidence_ok", "change_index_disputed",
                  "axis_partner_disagreement", "successor_named")


def _review_reasons(deutung: dict) -> list[str]:
    """The reasons this interpretation needs a human look; empty means it does not."""
    return [r for r in REVIEW_REASONS
            if (not deutung["evidence_ok"] if r == "evidence_ok" else deutung.get(r))]


def change_index_check(deutung: dict, ch: dict) -> dict:
    """Cross-check the chosen ``change_index`` against every record of the chapter (AP-07).

    ``evidence_match_chars`` is computed not only for the *chosen* record but for all of
    them. The strongest candidate is reported as ``change_index_best``; if it is
    *strictly* better than the chosen one, ``change_index_disputed`` is true.

    Nothing is corrected. The model chooses which change its interpretation is about,
    and it can be right for a reason text similarity cannot see -- it may interpret a
    connection instead of quoting it. An automatic correction would replace a right
    choice with a more similar one. The flag goes into the review queue, exactly as
    ``evidence_unique`` does (ENT-30): mark, do not decide.

    A tie is no contradiction: ``change_index_best`` then names the lowest of the tied
    indices (deterministic) and ``change_index_disputed`` stays false. Where no record
    supports the quote at all -- because there is no quote, or because it appears in
    none of them -- ``change_index_best`` is ``None``: naming record 0 would be an
    assertion without evidence, which is the error class this whole package closes.
    """
    # the span the guard settled on, not the answer as delivered: scoring the shell
    # against every record would measure the label, and the subset relation to
    # evidence_unique (both read the same quote) would quietly break (AP-08)
    ev = n2(deutung.get("evidence_span") or deutung.get("evidence") or "").lower()
    scores = [evidence_metrics(ev, _record_haystack(c))["evidence_match_chars"]
              for c in ch["changes"]]
    best = max(scores, default=0)
    if best == 0:
        return {"change_index_best": None, "change_index_disputed": False}
    i = deutung.get("change_index")
    # an absent or out-of-range index counts as covering nothing, so a record that does
    # cover something disputes it -- the same fail-closed rule as change_haystack()
    chosen = scores[i] if isinstance(i, int) and 0 <= i < len(scores) else -1
    return {"change_index_best": scores.index(best),
            "change_index_disputed": best > chosen}


def _evidence_ok(ev: str, hay: str) -> bool:
    """The historical rule, unchanged: 15-character probe plus short-quote branch.

    Deliberately blind to ellipsis marks -- it only ever sees the fragment before the
    first one. That weakness is what ``evidence_strict`` measures; changing this
    function would break parity with the baseline.
    """
    if not ev:
        return False
    if len(ev) < 15:
        # short quote only ok if it fully covers the (short) paragraph
        return bool(hay.strip()) and ev == hay.strip()
    probe = ev[:60]
    return probe[:15] in hay or ev in hay


# ------------------------------------------------------------------ axes against the facts
# AP-29. The pipeline knows three things about a change without asking anybody: which
# structural operation it is (axis A), which paragraph a move went to, and whether the
# changed sentences carry a modal verb. Measured over the three runs of 2026-08-27, the
# interpretation contradicts all three often enough to be a class of error rather than a
# handful of cases:
#
# * the two records of ONE move are interpreted differently in 66 % / 88 % / 54 % of the
#   pairs, and almost always with the same signature -- ``replaced`` seen from the old
#   place, ``equivalent`` seen from the new one;
# * 93 / 49 / 58 interpretations call a change ``equivalent`` that has no counterpart at
#   all (a pure addition or a pure deletion);
# * 78 / 88 / 39 changes carry a modal sentence and are declared non-normative;
# * 12 interpretations report a deletion while their own free text names where the rule
#   now lives.
#
# Every check MARKS, none corrects: the reported value stays readable and a flag joins it
# (the AP-07 pattern of ``change_index_disputed``, not the AP-14 one of discarding a value
# outside its vocabulary). Whoever corrects here measures the correction instead of the
# model. What the first finding needed was not a correction but a rule, and that rule is
# in the field description of ``semantic_status``.

#: Modality labels that carry a duty. ``informativ`` is the fifth value and the only one
#: outside; see :data:`~normpare.stages.enrich.modality.RANK`.
MODAL_LABELS = frozenset({"muss", "darf", "darf_nicht", "sollte", "kann"})

#: The axes compared between the two sides of one move. Axis A is equal by construction
#: (both sides map to ``moved``) and axis D is a list whose order carries meaning, so a
#: difference there is not a contradiction.
PARTNER_AXES = ("semantic_status", "normative_direction")

#: Axis A of a change that has no counterpart -- there is no earlier statement anything
#: could be ``equivalent`` to.
WITHOUT_COUNTERPART = ("added", "removed")

#: The narrow successor recogniser: a "now" followed straight away by a named place of
#: the outline. Measured over the three runs it finds 7 / 1 / 4 cases, every one of them
#: a real relocation. The wide form (:data:`_SUCCESSOR_HINT`) drops the place and takes
#: in "Die Planung muss **nun in** enger Abstimmung erfolgen", which is no relocation at
#: all -- so the narrow form is the one that writes on a record.
_SUCCESSOR_NAMED = re.compile(
    r"\b(?:nun|jetzt|nunmehr|k[uü]nftig|zuk[uü]nftig)\s+(?:in|nach)\s+"
    r"(?:Abschnitt|Kapitel|Anhang)\s+[A-Z]?\.?\d[\d.]*", re.IGNORECASE)

#: The wide form, measured only and never written on a record (AP-29, finding 4).
_SUCCESSOR_HINT = re.compile(
    r"\b(?:nun|jetzt|nunmehr|k[uü]nftig|zuk[uü]nftig)\s+(?:in|nach)\b", re.IGNORECASE)


def change_modality(change: dict | None) -> str | None:
    """The modality of a change as the text now stands: the new side, the old one only
    where there is no new one.

    A deletion has an old side and nothing else, so that is what it is judged by. A
    change whose duty became a note is judged by the note: axis C asks what the new text
    means for whoever is bound, and the old modality is the previous answer to that
    question, not the current one.
    """
    mod = (change or {}).get("modality") or {}
    return mod.get("new") or mod.get("old")


def free_text(deutung: dict) -> str:
    """``change`` and ``impact`` of one interpretation, joined -- what a reader reads."""
    return f"{deutung.get('change') or ''} {deutung.get('impact') or ''}"


def successor_named(deutung: dict) -> bool:
    """Whether the free text names where the rule now lives (the narrow form).

    Says nothing about the axes; the caller adds the condition it needs (``narrowed``
    for the flag, ``narrowed`` or ``extended`` for the wider measurement).
    """
    return bool(_SUCCESSOR_NAMED.search(free_text(deutung)))


def successor_hint(deutung: dict) -> bool:
    """The wide form of :func:`successor_named` -- for measurement, never for a record."""
    return bool(_SUCCESSOR_HINT.search(free_text(deutung)))


def move_pairs(synopse: dict) -> dict:
    """Join the two records of every move over the pointer the aligner left behind.

    A move is one event with two change records: ``moved_away`` in the old chapter and
    ``moved_in`` in the new one. ``moved_away.moved_to`` is the ``new_ids[0]`` of its
    ``moved_in``, so the pair is known deterministically -- no similarity measure and no
    model.

    Three results, and all three are reported:

    * ``pairs`` -- what the pointer proves.
    * ``unpaired`` -- a ``moved_away`` without a pointer. The block continuation of AP-26
      knows the chapter (``moved_to_chapter``, ``via: "block"``, confidence 0.0) and not
      the paragraph; pairing it would compare two interpretations that may be about
      different paragraphs. 15 / 35 / 3 of the moves over the three runs.
    * ``dangling`` -- a pointer into a record the pipeline does not call ``moved_in``.
      Seven times in the 60909 run the old side says "moved to X" while the new side
      calls X ``new``, and both are counted today. That is a finding of the
      deterministic stage, so it is reported rather than skipped.
    * ``orphans`` -- the mirror image (AP-30): a ``moved_in`` whose source is not reported
      as ``moved_away``. The new side says the text came from X, the old side says X is
      gone, and again one event is counted twice.

    Since AP-30 both defects must be zero, and both stay here as guards: a chapter without
    a counterpart gets ``para_links`` now, so neither side of a move can lose its record.
    """
    holders: dict[str, list[tuple[str, int, str]]] = {}
    sources: dict[str, list[tuple[str, int, str]]] = {}
    for ch in synopse.get("chapters") or []:
        for i, rec in enumerate(ch.get("changes") or []):
            for new_id in (rec.get("new_ids") or []):
                holders.setdefault(new_id, []).append(
                    (ch.get("mapping_id"), i, rec.get("kind")))
            for old_id in (rec.get("old_ids") or []):
                sources.setdefault(old_id, []).append(
                    (ch.get("mapping_id"), i, rec.get("kind")))
    pairs, unpaired, dangling, orphans = [], [], [], []
    for ch in synopse.get("chapters") or []:
        for i, rec in enumerate(ch.get("changes") or []):
            if rec.get("kind") == "moved_in":
                src = rec.get("moved_from")
                if src and not any(kind == "moved_away"
                                   for _m, _j, kind in sources.get(src, [])):
                    orphans.append({"mapping_id": ch.get("mapping_id"),
                                    "change_index": i, "moved_from": src,
                                    "kinds": [k for _m, _j, k in sources.get(src, [])]})
            if rec.get("kind") != "moved_away":
                continue
            here = {"mapping_id": ch.get("mapping_id"), "change_index": i}
            target = rec.get("moved_to")
            if not target:
                unpaired.append({**here, "moved_to_chapter": rec.get("moved_to_chapter"),
                                 "via": rec.get("via")})
                continue
            found = [(m, j) for m, j, kind in holders.get(target, [])
                     if kind == "moved_in"]
            if found:
                pairs.append({"new_id": target, "away": here,
                              "into": {"mapping_id": found[0][0],
                                       "change_index": found[0][1]}})
            else:
                dangling.append({**here, "moved_to": target,
                                 "kinds": [k for _m, _j, k in holders.get(target, [])]})
    return {"pairs": pairs, "unpaired": unpaired, "dangling": dangling,
            "orphans": orphans}


def _interpretation_index(chapters: list[dict]) -> dict:
    """``(mapping_id, change_index) -> interpretation``, in the order of the artefact."""
    rows: dict[tuple, dict] = {}
    for ch in chapters:
        for d in (ch.get("interpretations") or ch.get("deutungen") or []):
            i = d.get("change_index")
            if isinstance(i, int):
                rows[(ch.get("mapping_id"), i)] = d
    return rows


#: The flags the four checks write. Their overlap is the answer to "are these four
#: different kinds of error or four views of the same one".
CONSISTENCY_FLAGS = ("axis_partner_disagreement", "axis_b_contradicts_a",
                     "axis_c_contradicts_modality", "successor_named")


def check_consistency(chapters: list[dict], synopse: dict) -> dict:
    """The four checks of AP-29 over a finished interpretation -- offline, marking only.

    Reads the axes off the interpretations and the facts off the synopse, writes the
    flags of :data:`CONSISTENCY_FLAGS` onto the records that fail a check and returns the
    counts. A ``deutung.json`` from before the axes carries none of the fields the checks
    read, so it produces zero findings instead of an exception -- the same rule the axis
    report follows for the column of an older run.

    A flag is only ever written where a check fires: an artefact that passes all four is
    the artefact it was, field for field.
    """
    changes = {ch.get("mapping_id"): (ch.get("changes") or [])
               for ch in (synopse.get("chapters") or [])}
    rows = _interpretation_index(chapters)
    joined = move_pairs(synopse)

    by_axis = Counter()
    n_pairs_interpreted = n_partner = 0
    for pair in joined["pairs"]:
        away = rows.get((pair["away"]["mapping_id"], pair["away"]["change_index"]))
        into = rows.get((pair["into"]["mapping_id"], pair["into"]["change_index"]))
        if away is None or into is None:
            continue
        n_pairs_interpreted += 1
        disputed = [a for a in PARTNER_AXES if away.get(a) != into.get(a)]
        if not disputed:
            continue
        n_partner += 1
        by_axis.update(disputed)
        # both sides, never one: which of the two is right is a question for the schema
        # rule, not for a check that has no standard text in front of it
        for own, other, where in ((away, into, pair["into"]),
                                  (into, away, pair["away"])):
            own.setdefault("axis_partner_disagreement", []).extend(
                {"axis": a, "value": own.get(a), "partner_value": other.get(a),
                 "partner_mapping_id": where["mapping_id"],
                 "partner_change_index": where["change_index"]} for a in disputed)

    n_b = n_c = n_soft = n_successor = 0
    for (mid, i), d in rows.items():
        records = changes.get(mid) or []
        change = records[i] if 0 <= i < len(records) else None
        if (d.get("semantic_status") == "equivalent"
                and d.get("structural_operation") in WITHOUT_COUNTERPART):
            d["axis_b_contradicts_a"] = True
            n_b += 1
        modality, direction = change_modality(change), d.get("normative_direction")
        if modality in MODAL_LABELS and direction == "not_applicable":
            d["axis_c_contradicts_modality"] = True
            n_c += 1
        # ... and the other way round only as a number: the modality detection is known
        # to miss a list item under a "muss" stem sentence, and a duty can be phrased
        # without a modal verb. Marking the record would assert that the deterministic
        # side is right, which is the very thing this case leaves open.
        if modality == "informativ" and direction in ("tightened", "relaxed"):
            n_soft += 1
        if d.get("semantic_status") == "narrowed" and successor_named(d):
            d["successor_named"] = True
            n_successor += 1

    return {
        "n_pairs": len(joined["pairs"]), "n_pairs_interpreted": n_pairs_interpreted,
        "n_unpaired": len(joined["unpaired"]), "n_dangling": len(joined["dangling"]),
        "dangling": joined["dangling"],
        "n_orphan": len(joined["orphans"]), "orphans": joined["orphans"],
        "n_partner_disagreement": n_partner,
        "by_axis": {a: by_axis[a] for a in PARTNER_AXES},
        "n_axis_b_contradicts_a": n_b,
        "n_axis_c_contradicts_modality": n_c,
        "n_informative_with_direction": n_soft,
        "n_successor_named": n_successor,
        "n_multiple_flags": sum(1 for d in rows.values()
                                if sum(1 for f in CONSISTENCY_FLAGS if d.get(f)) > 1),
    }


def consistency_feedback(report: dict) -> list[dict]:
    """The numbers of :func:`check_consistency` as ``pipeline_feedback`` entries.

    Written at zero as well. A number that only appears when it is bad leaves the good
    case unmeasured, which is how the silent table cap of AP-20 survived two production
    runs -- and how the coverage of AP-28 reported 100 % over 77 missing answers.
    """
    r = report
    return [
        {"section_id": None, "phase": "deutung", "field": "axis_partner_disagreement",
         "count": r["n_partner_disagreement"], "by_axis": r["by_axis"],
         "n_pairs": r["n_pairs"], "n_pairs_interpreted": r["n_pairs_interpreted"],
         "finding": (f"{r['n_partner_disagreement']} of {r['n_pairs_interpreted']} "
                     f"interpreted move pair(s) disagree between their two sides "
                     f"(semantic_status: {r['by_axis']['semantic_status']}, "
                     f"normative_direction: {r['by_axis']['normative_direction']}); "
                     f"{r['n_pairs']} pair(s) joined over the pointer, "
                     f"{r['n_unpaired']} move(s) carry no pointer and were not checked")},
        {"section_id": None, "phase": "alignment", "field": "move_pointer",
         "count": r["n_dangling"], "dangling": r["dangling"],
         "finding": (f"{r['n_dangling']} moved_away record(s) point at a paragraph the "
                     f"comparison does not report as moved_in; the old side says the "
                     f"text moved, the new side reports it as new, and both are counted")},
        # AP-30: the mirror of the line above. Both must read zero since a chapter without
        # a counterpart carries para_links; they stay as guards, and a number that only
        # appears when it is bad leaves the good case unmeasured.
        {"section_id": None, "phase": "alignment", "field": "move_pointer_orphan",
         "count": r.get("n_orphan", 0), "orphans": r.get("orphans", []),
         "finding": (f"{r.get('n_orphan', 0)} moved_in record(s) name a source the "
                     f"comparison does not report as moved_away; the new side says the "
                     f"text came from there, the old side reports it as gone, and both "
                     f"are counted")},
        {"section_id": None, "phase": "deutung", "field": "axis_b_contradicts_a",
         "count": r["n_axis_b_contradicts_a"],
         "finding": (f"{r['n_axis_b_contradicts_a']} interpretation(s) answer "
                     f"semantic_status 'equivalent' for a change without a counterpart "
                     f"(structural_operation added or removed); marked, not corrected")},
        {"section_id": None, "phase": "deutung", "field": "axis_c_contradicts_modality",
         "count": r["n_axis_c_contradicts_modality"],
         "finding": (f"{r['n_axis_c_contradicts_modality']} interpretation(s) call a "
                     f"change with a modal sentence non-normative "
                     f"(normative_direction 'not_applicable'); marked, not corrected")},
        {"section_id": None, "phase": "deutung", "field": "informative_with_direction",
         "count": r["n_informative_with_direction"],
         "finding": (f"{r['n_informative_with_direction']} interpretation(s) report a "
                     f"direction for a change the modality detection reads as "
                     f"informative; counted only, no record marked -- the detection is "
                     f"not certain enough here to overrule the interpretation")},
        {"section_id": None, "phase": "deutung", "field": "successor_named",
         "count": r["n_successor_named"], "n_multiple_flags": r["n_multiple_flags"],
         "finding": (f"{r['n_successor_named']} interpretation(s) report semantic_status "
                     f"'narrowed' while their own free text names the section the rule "
                     f"moved to; {r['n_multiple_flags']} interpretation(s) carry more "
                     f"than one of the four flags")},
    ]


# ------------------------------------------------------------------ what the run lost
# AP-18. Two production runs lost 18 of 326 chapters (4,7 % and 6,5 %), 13 of them
# without a word: the answer was truncated, the JSON did not parse, the chapter fell back
# to the extractive summary and the run still ended with "Done." and a file list. The
# chapters hit were the ones with the most changes, which is to say the ones that matter.
# The defect is the silence, so the stage now reports what it has -- always, not only on
# failure: a report that appears only when something breaks says nothing when it is quiet.

def _outcome_bucket(reason: str) -> str:
    """``api_error:overloaded_error`` -> ``api_error``; everything else is its own bucket."""
    return reason.split(":", 1)[0]


def truncation_report(notes: list[dict]) -> dict:
    """What the two character budgets of the prompt cut, out of how much (AP-20).

    ``notes`` is what :func:`build_chapter_prompts` collected: one record per rendered
    table and one per asset block, clipped or not. Both totals are reported even when
    nothing was clipped -- a number that only appears when it is bad leaves the good case
    unmeasured, which is exactly how the old 1100-character cap stayed invisible.
    """
    tables = [n for n in notes if n["kind"] == "table"]
    blocks = [n for n in notes if n["kind"] == "asset_block"]
    return {
        "n_tables": len(tables),
        "n_tables_truncated": sum(1 for n in tables if n["n_dropped"]),
        "n_asset_blocks": len(blocks),
        "n_asset_blocks_truncated": sum(1 for n in blocks if n["n_dropped"]),
        "truncated": [n for n in notes if n["n_dropped"]],
    }


def coverage_report(results: list[dict], n_split_chapters: int, n_extra_requests: int,
                    n_collisions: int, n_repaired: int,
                    asset_notes: list[dict] | None = None,
                    n_changes_total: int = 0) -> dict:
    """How much of the compared material actually carries an interpretation.

    Three quantities, and they are three because they differ (AP-28):

    * ``n_changes_total`` -- every change of the comparison, including the chapters the
      scope leaves out (all their changes are semantically equal).
    * ``n_changes`` -- what a prompt was shown. Since AP-19 that is every change of every
      in-scope chapter; before it, at most 40 per chapter whatever its size.
    * ``n_interpreted`` -- what came back, counted over the ``change_index`` values that
      are really in the merged answer. ``n_unanswered`` is the difference: changes that
      lay in front of a model and got no interpretation.

    ``incomplete`` names every chapter that has changes without an interpretation, with
    both numbers; ``asset_notes`` adds the second kind of loss: material that reached the
    prompt only in part because a table or an asset block ran into its character budget
    (AP-20).
    """
    total = sum(r.get("_changes_total", 0) for r in results)
    done = sum(len(r.get("_changes_interpreted") or []) for r in results)
    return {
        "n_changes_total": n_changes_total or total,
        "n_changes": total, "n_interpreted": done, "n_unanswered": total - done,
        "n_split_chapters": n_split_chapters, "n_extra_requests": n_extra_requests,
        "n_collisions": n_collisions, "n_repaired": n_repaired,
        **truncation_report(asset_notes or []),
        "incomplete": [{"section_id": r.get("section_id"), "mapping_id": r.get("mapping_id"),
                        "n_changes": r.get("_changes_total", 0),
                        "n_interpreted": len(r.get("_changes_interpreted") or [])}
                       for r in results
                       if len(r.get("_changes_interpreted") or []) < r.get("_changes_total", 0)],
    }


def coverage_total(coverage: dict) -> int:
    """Every change of the comparison, the base the coverage is measured against.

    Falls back to ``n_changes`` for a ``deutung.json`` written before AP-28, which knew
    only the changes that reached a prompt.
    """
    return coverage.get("n_changes_total") or coverage.get("n_changes") or 0


def coverage_unanswered(coverage: dict) -> int:
    """Changes that lay in front of a model and got no interpretation back.

    Derived for an artefact from before AP-28: there ``n_interpreted`` counted the shown
    changes, so the difference is zero and an old file reports what it reported then.
    """
    return coverage.get("n_unanswered",
                        (coverage.get("n_changes") or 0) - coverage.get("n_interpreted", 0))


def coverage_percent(coverage: dict) -> float:
    """Share of interpreted changes in percent; a run without changes is complete."""
    total = coverage_total(coverage)
    return 100.0 if not total else coverage.get("n_interpreted", 0) / total * 100


def format_percent(value: float) -> str:
    """German decimal comma -- the console and the deliverables are read in German."""
    return f"{value:.1f}".replace(".", ",")


def coverage_lines(coverage: dict) -> list[str]:
    """The coverage part of the console summary: how much, split how, and what is missing."""
    lines = [(f"    Änderungen: {coverage_total(coverage)}, "
              f"vorgelegt {coverage['n_changes']}, "
              f"gedeutet {coverage['n_interpreted']} "
              f"({format_percent(coverage_percent(coverage))} %)")]
    # ... always, also at zero: how many of the shown changes came back without an
    # interpretation is the number the old count could not express at all (AP-28)
    unanswered = coverage_unanswered(coverage)
    line = f"      ohne Antwort: {unanswered}"
    if unanswered:
        names = [f"{c['mapping_id'] or c['section_id']} "
                 f"({c['n_changes'] - c['n_interpreted']})"
                 for c in coverage["incomplete"]]
        shown = ", ".join(names[:SUMMARY_NAMES])
        if len(names) > SUMMARY_NAMES:
            shown += f", … (+{len(names) - SUMMARY_NAMES} weitere)"
        line += f"   {shown}"
    lines.append(line)
    if coverage["n_split_chapters"]:
        lines.append(f"    Kapitel in Teilanfragen: {coverage['n_split_chapters']} "
                     f"({coverage['n_extra_requests']} Zusatzanfragen)")
    # ... always, also at zero: this is the line the old silent table cap never wrote
    lines.append(f"    Tabellen gekürzt: {coverage.get('n_tables_truncated', 0)} von "
                 f"{coverage.get('n_tables', 0)}")
    lines.append(f"    Asset-Blöcke gekürzt: {coverage.get('n_asset_blocks_truncated', 0)} "
                 f"von {coverage.get('n_asset_blocks', 0)}")
    if coverage["n_repaired"]:
        lines.append(f"    repariert: {coverage['n_repaired']}")
    if coverage["n_collisions"]:
        lines.append(f"    Index-Kollisionen: {coverage['n_collisions']} "
                     f"(der frühere Block gilt)")
    return lines


def coverage_feedback(coverage: dict) -> dict:
    """The coverage as one machine-readable ``pipeline_feedback`` entry of phase
    ``deutung`` -- the same numbers the console prints, for a later comparison."""
    return {"section_id": None, "phase": "deutung", "field": "coverage", **coverage,
            "count": coverage_unanswered(coverage),
            "finding": (f"{coverage['n_interpreted']} of {coverage_total(coverage)} changes "
                        f"were interpreted ({format_percent(coverage_percent(coverage))} %); "
                        f"{coverage['n_changes']} were shown to a prompt, of which "
                        f"{coverage_unanswered(coverage)} came back without an "
                        f"interpretation; "
                        f"{coverage['n_split_chapters']} chapter(s) were split into "
                        f"{coverage['n_extra_requests']} extra request(s), "
                        f"{coverage['n_collisions']} change index collision(s), "
                        f"{coverage['n_repaired']} answer(s) only readable leniently; "
                        f"{coverage.get('n_tables_truncated', 0)} of "
                        f"{coverage.get('n_tables', 0)} table(s) truncated, "
                        f"{coverage.get('n_asset_blocks_truncated', 0)} of "
                        f"{coverage.get('n_asset_blocks', 0)} asset block(s)")}


def answer_summary(n_chapters: int, n_interpreted: int, lost: list[dict],
                   prompt_dir: Path | str | None = None,
                   coverage: dict | None = None, n_reasoning: int = 0) -> list[str]:
    """The console summary of the interpretation stage, one line per reason.

    Args:
        lost: ``{"mapping_id", "reason"}`` per chapter without an interpretation.
        prompt_dir: where the prompts and raw answers were kept, named in the last line.
        coverage: :func:`coverage_report`; how much of the material was interpreted.
        n_reasoning: answers that came back with a chain of thought (AP-27). Like the
            reasons of :data:`ANSWER_OUTCOMES`, the line appears only when there is
            something to report -- a provider that does not think would otherwise write
            "0" under every single run.
    """
    lines = [(f"  Deutung: {n_chapters} Kapitel, {n_interpreted} gedeutet, "
              f"{len(lost)} ohne Deutung")]
    if coverage is not None:
        lines += coverage_lines(coverage)
    if n_reasoning:
        lines.append(f"    Denkmodus: {n_reasoning} Antwort(en) mit reasoning_content — "
                     f"der Anbieter hat trotz 'thinking: disabled' gedacht und es als "
                     f"Ausgabe abgerechnet")
    buckets: dict[str, list[str]] = {}
    for item in lost:
        bucket = _outcome_bucket(item["reason"])
        detail = item["reason"].split(":", 1)[1] if ":" in item["reason"] else ""
        name = str(item.get("mapping_id") or item.get("section_id"))
        buckets.setdefault(bucket, []).append(f"{name} ({detail})" if detail else name)
    order = [b for b in ANSWER_OUTCOMES if b in buckets]
    order += sorted(b for b in buckets if b not in ANSWER_OUTCOMES)
    for bucket in order:
        names = buckets[bucket]
        shown = ", ".join(names[:SUMMARY_NAMES])
        if len(names) > SUMMARY_NAMES:
            shown += f", … (+{len(names) - SUMMARY_NAMES} weitere)"
        lines.append(f"    {bucket:<14}{len(names):>4}   {shown}")
    # only the two reasons that write a file are worth pointing at; an export run has no
    # .FAILED.txt to look into
    if prompt_dir is not None and {OUTCOME_TRUNCATED, OUTCOME_UNPARSABLE} & set(buckets):
        lines.append(f"    -> Prompts und Rohantworten in {prompt_dir}/*.FAILED.txt")
    return lines


def answer_feedback(n_chapters: int, n_interpreted: int, lost: list[dict],
                    n_reasoning: int = 0) -> list[dict]:
    """The same numbers machine-readable, as ``pipeline_feedback`` entries of phase
    ``deutung`` -- one per lost chapter plus one total, so a later run can compare.

    ``n_reasoning`` adds one further entry, and only when it is non-zero: a run against a
    provider that respects ``thinking: disabled`` writes exactly the entries it wrote
    before (AP-27)."""
    entries = [{"section_id": item.get("section_id"), "mapping_id": item.get("mapping_id"),
                "phase": "deutung", "field": "answer", "reason": item["reason"], "count": 1,
                "finding": (f"no interpretation for this chapter ({item['reason']}); "
                            "the extractive summary was used instead")}
               for item in lost]
    reasons = Counter(_outcome_bucket(item["reason"]) for item in lost)
    entries.append({
        "section_id": None, "phase": "deutung", "field": "answers",
        "n_chapters": n_chapters, "n_interpreted": n_interpreted, "count": len(lost),
        "reasons": dict(sorted(reasons.items())),
        "finding": (f"{n_interpreted} of {n_chapters} chapters were interpreted; "
                    f"{len(lost)} without an interpretation"
                    + (f" ({', '.join(f'{k}: {v}' for k, v in sorted(reasons.items()))})"
                       if lost else "")),
    })
    if n_reasoning:
        entries.append({
            "section_id": None, "phase": "deutung", "field": "reasoning",
            "count": n_reasoning,
            "finding": (f"{n_reasoning} answer(s) carried a reasoning_content field: the "
                        "provider thought despite 'thinking: disabled' and billed the "
                        "chain of thought as output"),
        })
    return entries


def run_deutung(synopse: dict, old_doc: dict, new_doc: dict, root: Path, out_dir: Path,
                model: str, scope: str = "core", provider: str = "anthropic",
                base_url: str | None = None, key_env: str | None = None,
                api_version: str | None = None, batch: bool = False,
                language: str = "de", title: str = "",
                deutung_provider: DeutungProvider | None = None) -> dict:
    """Per-chapter interpretation. scope=core: chapters with substantive changes.
    provider/base_url/key_env/api_version steer the LLM backend; without a key the
    prompts are exported and summaries fall back to extractive.
    batch=True submits all uncached chapters in one Anthropic message batch (~50% cheaper,
    asynchronous). The JSON schema is neutral English; `language` decides in which language
    the free-text values are written, `title` names the compared standard in the prompt.
    deutung_provider replaces the source of the answers (see :class:`DeutungProvider`);
    the default is a :class:`LiveProvider` built from the arguments above, so existing
    callers are unaffected."""
    o_secs, n_secs = section_index(old_doc), section_index(new_doc)
    Path(out_dir).mkdir(parents=True, exist_ok=True)   # LiveProvider used to do this
    answer_source = deutung_provider or LiveProvider(
        root, model, out_dir / "llm_cache", out_dir / "llm_prompts",
        provider=provider, base_url=base_url, key_env=key_env, api_version=api_version,
        batch=batch, system=build_system_prompt(language, title))

    # ---- phase 1: build the prompts of every in-scope chapter --------------------
    # A chapter with more changes than fit into one request is split into several
    # (AP-19); block 1 is the prompt an unsplit chapter would get, down to the byte.
    asset_notes: list[dict] = []       # what the table/block budgets cut, per chapter
    jobs = chapter_jobs(synopse, o_secs, n_secs, scope, asset_notes)

    # ---- phase 2: resolve the answers --------------------------------------------
    # The provider decides how: batch mode submits every uncached chapter in one
    # asynchronous batch (~50% cheaper), otherwise the chapters are asked one by one.
    answers = answer_source.resolve([(tag, prompt) for _ch, _cid, _mid, tags, parts, *_ in jobs
                                     for tag, (prompt, _sel) in zip(tags, parts)])

    # ---- phase 3: assemble, guard the evidence, collect the review queue ----------
    results = []
    # (where it belongs, the record) per interpretation and per asset, in the order
    # deutung.json lists them -- the queue is built from these once every check has run
    candidates: list[tuple[dict, dict]] = []
    dropped: list[dict] = []       # pipeline-owned values the model supplied anyway
    lost: list[dict] = []          # chapters without an interpretation, with the reason
    outcomes = getattr(answer_source, "outcomes", None) or {}
    n_llm = 0
    n_split = 0                    # chapters that needed more than one request
    n_extra = 0                    # ... and how many requests that cost on top
    n_collisions = 0               # two blocks claiming the same change index
    n_repaired = 0                 # answers only readable with the lenient parse
    unjoined = 0                   # table interpretations naming no table of their chapter
    for ch, cid, mid, tags, parts, old_x, new_x in jobs:
        # one chapter, one interpretation -- however many requests it took to get it
        data, _sel, collisions = merge_chapter_answers(
            [(answers.get(t), part_sel) for t, (_p, part_sel) in zip(tags, parts)])
        n_collisions += collisions
        n_repaired += sum(1 for t in tags if outcomes.get(t) == OUTCOME_REPAIRED)
        if len(parts) > 1:
            n_split += 1
            n_extra += len(parts) - 1
        drops = Counter()          # field -> how often it arrived in this chapter
        supplied = {}              # field -> the discarded value, for the report
        invalid = Counter()        # (axis field, violation) -> how often in this chapter
        offenders: dict = {}       # (axis field, violation) -> the discarded values
        if data is None:
            # fallback: extractive -- and the reason it came to that is kept, so the run
            # can say which chapters it lost and why (AP-18)
            lost.append({"section_id": cid, "mapping_id": mid,
                         "reason": outcomes.get(tags[0], OUTCOME_NO_ANSWER)})
            data = {"section_id": cid,
                    "summary_old": extractive_summary(old_x),
                    "summary_new": extractive_summary(new_x),
                    "change_overview": "",
                    "training_relevance": None, "practical_note": "",
                    "interpretations": [], "_source": "extractive"}
        else:
            n_llm += 1
            # class P: whatever the model wrote into a pipeline-owned field is discarded
            # here, before anything reads it, and counted below (AP-07)
            supplied = drop_pipeline_owned(data, PIPELINE_OWNED_CHAPTER)
            drops.update(supplied.keys())
            data["_source"] = "llm"
            for d in data.get("interpretations", []):
                drops.update(drop_pipeline_owned(d, PIPELINE_OWNED_INTERPRETATION).keys())
                # ENT-01: axis A from the change record, axes B/C/D checked against
                # their vocabularies. A rejected value is discarded and counted below,
                # never corrected -- see check_axes
                axes, violations = check_axes(d, change_record(d, ch))
                d.update(axes)
                for v in violations:
                    key = (v["field"], v["reason"])
                    invalid[key] += 1
                    offenders.setdefault(key, []).append(v["value"])
                d.update(check_evidence(d, ch))
                # class V: the model's change_index stays as it is, the cross-check only
                # says whether another record of the chapter fits the quote better
                d.update(change_index_check(d, ch))
                # the review queue still keys on evidence_ok (ENT-18): it switches to
                # evidence_strict once the difference between both is quantified.
                # change_index_disputed is an additional trigger, with its own reason.
                # Which interpretations end up in the queue is decided after the loop:
                # the two reasons of AP-29 compare a chapter with another one, so they
                # are not known while this chapter is being assembled.
                candidates.append(({"section_id": cid, "mapping_id": mid}, d))
            # table/figure interpretations: check evidence against cells/captions
            if data.get("tables") or data.get("figures"):
                hay = asset_haystack(ch, o_secs, n_secs)
                o_tabs, n_tabs = _tabmap(o_secs), _tabmap(n_secs)
                for kind in ("tables", "figures"):
                    for a in (data.get(kind) or []):
                        drops.update(drop_pipeline_owned(a, PIPELINE_OWNED_ASSET).keys())
                        a.update(check_asset_evidence(a, hay))
                        if kind == "tables":
                            # AP-31: the values the entry claims, checked against the
                            # cells of the table it names -- marked, never corrected
                            a.update(check_asset_values(a, resolve_table(a, ch),
                                                        o_tabs, n_tabs))
                            if a["values_ok"] is None:
                                unjoined += 1
                        candidates.append(({"section_id": cid, "mapping_id": mid,
                                            "asset": kind[:-1]}, a))
        # Which chapter this answer belongs to is decided here, not in the answer: the
        # model is asked to echo section_id and in the 4110 run it did not -- it wrote a
        # slug of the heading, or the value of the "Teil" field for unnumbered annexes,
        # which left 165 interpretations (10.6 %) without a chapter to check them against.
        data["mapping_id"] = mid
        data["section_id"] = cid
        data["_changes_total"] = len(ch["changes"])
        # what came back, not what was asked: `sel` is the selection the answering blocks
        # were built from, and a model may answer about fewer changes than it was shown
        # (AP-28). Every in-scope change reaches some block, so `_changes_total` is at the
        # same time the number of changes this chapter presented.
        data["_changes_interpreted"] = (answered_indices(data, len(ch["changes"]))
                                        if data.get("_source") == "llm" else [])
        results.append(data)
        for field, n in sorted(drops.items()):
            was = supplied.get(field)
            dropped.append({
                "section_id": cid, "mapping_id": mid, "phase": "deutung",
                "field": field, "count": n,
                "finding": (f"the model supplied the pipeline-owned field "
                            f"'{field}' {n}x; the value was discarded"
                            + (f" (it said {was!r}, the pipeline says {cid!r})"
                               if field == "section_id" and was != cid else "")),
            })
        # ... and what the model wrote into an axis it does own, outside the vocabulary
        # it was given (ENT-01). Counted, not repaired: a quietly corrected answer would
        # make the measurement of the new taxonomy measure the correction.
        for (field, reason), n in sorted(invalid.items()):
            seen = list(dict.fromkeys(str(v) for v in offenders[(field, reason)]))
            dropped.append({
                "section_id": cid, "mapping_id": mid, "phase": "deutung",
                "field": field, "reason": reason, "count": n,
                "finding": (f"the axis field '{field}' {AXIS_VIOLATIONS[reason]} in {n} "
                            f"interpretation(s): {seen[:5]}; discarded, not corrected"),
            })

    # ---- phase 4: the checks that need more than one chapter, then the queue ------
    # AP-29: a move is one event with two change records in two chapters, so the two
    # sides can only be compared once every chapter is assembled. The three other checks
    # run here as well, in one pass over the same index.
    consistency = check_consistency(results, synopse)
    review = []
    for where, record in candidates:
        if "asset" in where:
            # a table or figure has no axes and no change index; its triggers are the
            # quote, as before, and since AP-31 the values it claims -- the latter only
            # where they could be checked at all
            reasons = asset_review_reasons(record)
        else:
            reasons = _review_reasons(record)
        if reasons:
            review.append({**where, "review_reasons": reasons, **record})

    # aggregate preprocessing feedback -> feed it back to the pipeline phases
    feedback = []
    for r in results:
        for fb in r.get("pipeline_feedback") or []:
            feedback.append({"section_id": r.get("section_id"), **fb})
    # ... and the pipeline's own feedback about the answers: what the model wrote into a
    # field it does not own (AP-07). Five invented chapter ids made 165 interpretations
    # uncheckable in the 4110 run, and nothing in the output said so.
    feedback += dropped
    # ... and what the run itself lost: which chapters have no interpretation, and why
    # ... including what the provider thought although it was told not to (AP-27)
    n_reasoning = getattr(answer_source, "n_reasoning", 0)
    feedback += answer_feedback(len(results), n_llm, lost, n_reasoning)
    # ... and which value statements nobody could check, because the entry names no table
    # of its chapter (AP-31). Unchecked, not wrong: a number that counted them as failures
    # would measure the join instead of the statement.
    if unjoined:
        feedback.append({"section_id": None, "phase": "deutung", "field": "value_changes",
                         "count": unjoined,
                         "finding": (f"{unjoined} table interpretation(s) name no table of "
                                     f"their chapter; their value changes are unchecked")})
    # ... and how much of the material carries an interpretation at all (AP-19)
    # ... measured against every change of the comparison, the chapters left out of the
    # scope included: their changes carry no interpretation either, and a base that hides
    # them would report a completeness the deliverables do not have
    coverage = coverage_report(results, n_split, n_extra, n_collisions, n_repaired,
                               asset_notes,
                               n_changes_total=sum(len(c.get("changes") or [])
                                                   for c in synopse["chapters"]))
    feedback.append(coverage_feedback(coverage))
    # ... and where the interpretation contradicts what the pipeline knows by itself
    feedback += consistency_feedback(consistency)
    for line in answer_summary(len(results), n_llm, lost, out_dir / "llm_prompts",
                               coverage=coverage, n_reasoning=n_reasoning):
        print(line)
    live = answer_source.live
    out = {"model": model if live else None, "mode": "api" if live else "export",
           "language": language,
           "n_chapters": len(results), "n_llm": n_llm, "coverage": coverage,
           "consistency": consistency,
           "chapters": results,
           "review_queue": review, "pipeline_feedback": feedback}
    (out_dir / "deutung.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    if feedback:
        L = ["# Vorverarbeitungs-Feedback aus der KI-Deutung\n",
             "_Von der Deutung gemeldete Artefakte — Kandidaten für Verbesserungen "
             "in Ingest/Alignment/Diff (Phase in Klammern). Phase `deutung`: Felder, "
             "die das Modell geliefert hat, obwohl die Pipeline sie selbst setzt._\n"]
        by_phase = Counter(fb["phase"] for fb in feedback)
        L.append("**Verteilung:** " + ", ".join(f"{p}: {n}" for p, n in by_phase.most_common()) + "\n")
        for fb in feedback:
            idx = f" (Änderungen {fb['change_indices']})" if fb.get("change_indices") else ""
            where = fb.get("section_id") or "(ganzer Lauf)"
            L.append(f"- **{where}** [{fb['phase']}]{idx}: {fb['finding']}")
        (out_dir / "pipeline_feedback.md").write_text("\n".join(L), encoding="utf-8")
    return out
