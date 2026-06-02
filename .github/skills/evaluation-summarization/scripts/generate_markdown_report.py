# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
Generate detailed Markdown evaluation report from individual evaluation JSONs.

Reads per-question assessment files from per-applicant assessor directories and produces:
  - An aggregate markdown report with cohort statistics
  - Per-applicant summary markdown files (rich 4-section format)

New schema (preferred): reads q1_written.json + q2_oral.json + q3_logical.json per applicant
  and merges them into a unified evaluation. The analyst also generates an aggregate
  assessor_impression from the three per-response impressions.
Legacy schema (fallback): reads evaluation.json if the three-file schema is not present.

The per-applicant summary also reads from the preparer directory (if available):
  - meta.json → Kira Review profile URL
  - submission.json → assessment question text + full student answers/transcripts

Usage:
    uv run .github/skills/evaluation-summarization/scripts/generate_markdown_report.py \
        --input-dir data/assessor/ --output-dir data/analyst/ [--preparer-dir data/preparer/]
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev, variance


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Score extraction helpers (handles multiple JSON schema variants)
# ---------------------------------------------------------------------------

SCORE_ALIASES = {
    "written": ["written_communications", "q1_written_communications", "Q1_written", "q1_written"],
    "oral": ["oral_reflective", "oral_communications", "oral_communications_reflective",
             "q2_oral_reflective", "Q2_oral", "q2_oral"],
    "logical": ["oral_logical", "logical_thinking", "oral_communications_logical",
                "q3_oral_logical", "q3_logical_thinking", "q3_oral_logical_thinking",
                "Q3_logical", "q3_logical"],
}


def get_score(e: dict, key: str) -> float | None:
    """Extract final_score from a section, trying multiple key aliases."""
    candidates = SCORE_ALIASES.get(key, [key])
    for wrapper in [e, e.get("assessments", {}), e.get("assessment_scores", {}), e.get("scores", {})]:
        for c in candidates:
            if c in wrapper:
                sec = wrapper[c]
                val = sec.get("final_score", sec.get("overall_score"))
                if val is not None:
                    return val
    return None


def get_section(e: dict, key: str) -> dict:
    """Return the evaluation section dict for written/oral/logical."""
    candidates = SCORE_ALIASES.get(key, [key])
    for wrapper in [e, e.get("assessments", {}), e.get("assessment_scores", {}), e.get("scores", {})]:
        for c in candidates:
            if c in wrapper:
                return wrapper[c]
    return {}


def is_skeleton(e: dict) -> bool:
    """Return True for placeholder files with no real submission data."""
    hook = e.get("candidate_hook", "")
    skeleton_phrases = ("No submission provided", "No responses provided", "Empty file", "Empty submission")
    return any(phrase in hook for phrase in skeleton_phrases)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def merge_assessor_impressions(impressions: list[dict]) -> dict:
    """Merge multiple per-response assessor impressions into a candidate-level aggregate."""
    merged = {
        "name": "", "country_of_origin": "", "ethnicity": "",
        "language_skills": "", "city_currently_lived": "",
        "school_attended": "", "candidate_hook": ""
    }
    hooks = []
    for imp in impressions:
        if not imp:
            continue
        for k, v in imp.items():
            if k == "candidate_hook":
                if v:
                    hooks.append(v)
            elif v and not merged.get(k):
                merged[k] = v
    merged["candidate_hook"] = " | ".join(hooks) if hooks else ""
    return merged


# Maps per-question JSON filename to its section key in the merged evaluation dict
_Q_FILE_SECTION_MAP = [
    ("q1_written.json", "written_communications"),
    ("q2_oral.json", "oral_communications"),
    ("q3_logical.json", "logical_thinking"),
]


def load_evaluation_for_applicant(applicant_dir: Path) -> dict | None:
    """
    Load evaluation from 3 separate question files (new schema) or fall back to
    evaluation.json (legacy schema).

    New schema: q1_written.json + q2_oral.json + q3_logical.json are merged into
    a single dict with top-level section keys matching SCORE_ALIASES.
    Legacy schema: evaluation.json is used as-is.
    """
    has_new = any((applicant_dir / fname).exists() for fname, _ in _Q_FILE_SECTION_MAP)

    if has_new:
        merged: dict = {"applicant_id": applicant_dir.name}
        impressions = []
        meta = None

        for fname, section_key in _Q_FILE_SECTION_MAP:
            json_file = applicant_dir / fname
            if not json_file.exists():
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                log(f"WARNING: Skipping {json_file}: Invalid JSON — {exc}")
                continue

            # Copy the assessment section into merged dict
            if section_key in data:
                merged[section_key] = data[section_key]
                imp = data[section_key].get("assessor_impression", {})
                if imp:
                    impressions.append(imp)

            # Prefer a non-unknown applicant_id
            if data.get("applicant_id") and data["applicant_id"] != "unknown":
                merged["applicant_id"] = data["applicant_id"]

            # Use metadata from whichever file we find first
            if meta is None and data.get("evaluation_metadata"):
                meta = data["evaluation_metadata"]

        if meta:
            merged["evaluation_metadata"] = meta

        # Generate candidate-level aggregate assessor impression
        if impressions:
            merged["assessor_impression"] = merge_assessor_impressions(impressions)

        return merged

    # Legacy: single evaluation.json
    eval_file = applicant_dir / "evaluation.json"
    if not eval_file.exists():
        return None
    try:
        return json.loads(eval_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"WARNING: Skipping {applicant_dir.name}: Invalid JSON — {exc}")
        return None


def load_evaluations(input_dir: Path) -> list[dict]:
    """Load evaluations from each applicant subdirectory (new 3-file schema or legacy)."""
    evaluations = []
    for applicant_dir in sorted(input_dir.iterdir()):
        if not applicant_dir.is_dir():
            continue
        data = load_evaluation_for_applicant(applicant_dir)
        if data is None:
            continue
        if is_skeleton(data):
            log(f"WARNING: Skipping {applicant_dir.name}: empty placeholder")
            continue
        if not data.get("applicant_id") or data["applicant_id"] == "unknown":
            data["applicant_id"] = applicant_dir.name
        evaluations.append(data)
    return evaluations


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def calculate_distribution(scores: list[float]) -> dict[int, int]:
    rounded_scores = [round(score) for score in scores]
    distribution = Counter(rounded_scores)
    return {i: distribution.get(i, 0) for i in range(1, 6)}


def find_best_worst(evaluations: list[dict], assessment_type: str) -> dict:
    scores_map: dict[float, list[str]] = {}
    for e in evaluations:
        score = get_score(e, assessment_type)
        if score is None:
            continue
        scores_map.setdefault(score, []).append(e["applicant_id"])
    if not scores_map:
        return {"best": {"score": 0, "candidates": []}, "worst": {"score": 0, "candidates": []}}
    return {
        "best": {"score": max(scores_map), "candidates": sorted(scores_map[max(scores_map)])},
        "worst": {"score": min(scores_map), "candidates": sorted(scores_map[min(scores_map)])},
    }


def calculate_statistics(evaluations: list[dict]) -> dict:
    written_scores, oral_scores, logical_scores = [], [], []
    for e in evaluations:
        w = get_score(e, "written")
        o = get_score(e, "oral")
        l = get_score(e, "logical")
        if w is not None:
            written_scores.append(w)
        if o is not None:
            oral_scores.append(o)
        if l is not None:
            logical_scores.append(l)

    def score_stats(scores):
        if not scores:
            return {"mean": 0, "median": 0, "stdev": 0, "min": 0, "max": 0, "distribution": {i: 0 for i in range(1, 6)}, "n": 0}
        return {
            "mean": mean(scores), "median": median(scores),
            "stdev": stdev(scores) if len(scores) > 1 else 0,
            "min": min(scores), "max": max(scores),
            "distribution": calculate_distribution(scores),
            "n": len(scores),
        }

    return {
        "total_applicants": len(evaluations),
        "written": score_stats(written_scores),
        "oral": score_stats(oral_scores),
        "logical": score_stats(logical_scores),
        "best_worst": {
            "written": find_best_worst(evaluations, "written"),
            "oral": find_best_worst(evaluations, "oral"),
            "logical": find_best_worst(evaluations, "logical"),
        },
    }


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

NON_CRITERIA = {"overall_score", "overall_commentary", "final_score", "original_score",
                "submission_word_count", "word_count", "compass", "assessor_impression"}


# ---------------------------------------------------------------------------
# Compass score → numeric modifier
# ---------------------------------------------------------------------------

COMPASS_SCORE_MAP = {
    "clear yes": 0.4,
    "lean yes": 0.15,
    "neutral": -0.1,
    "genuinely uncertain": -0.1,
    "lean no": -0.35,
    "clear no": -0.6,
}


def compass_score_to_modifier(score_label: str) -> float | None:
    return COMPASS_SCORE_MAP.get(score_label.lower().strip() if score_label else "", None)


# ---------------------------------------------------------------------------
# Preparer data loading
# ---------------------------------------------------------------------------

def load_preparer_data(preparer_dir: Path | None, applicant_id: str) -> tuple[str | None, dict]:
    """
    Returns (kira_url, submission_data).
    submission_data keys: q1_written, q2_oral, q3_logical (each may be absent).
    """
    if preparer_dir is None:
        return None, {}
    app_dir = preparer_dir / applicant_id
    if not app_dir.is_dir():
        return None, {}

    kira_url = None
    meta_path = app_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            kira_url = meta.get("url")
        except Exception:
            pass

    submission = {}
    sub_path = app_dir / "submission.json"
    if sub_path.exists():
        try:
            submission = json.loads(sub_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return kira_url, submission


def get_full_answer(submission: dict, assessment_key: str) -> str:
    """
    Extract the full answer text from submission.json for a given assessment.
    Returns empty string if not found.
    """
    key_map = {
        "written": "q1_written",
        "oral": "q2_oral",
        "logical": "q3_logical",
    }
    sub_key = key_map.get(assessment_key, "")
    section = submission.get(sub_key, {})
    return section.get("response", "")


def get_question_text(submission: dict, assessment_key: str) -> str:
    key_map = {
        "written": "q1_written",
        "oral": "q2_oral",
        "logical": "q3_logical",
    }
    sub_key = key_map.get(assessment_key, "")
    section = submission.get(sub_key, {})
    return section.get("question", "")


# ---------------------------------------------------------------------------
# Rich per-applicant summary (new 4-section format)
# ---------------------------------------------------------------------------

# Criteria labels per assessment for the score matrix
WRITTEN_CRITERIA_KEYS = [
    ("clarity", "Clarity"),
    ("organization_coherence", "Organization / Coherence"),
    ("meaning_development", "Meaning / Development"),
    ("proposed_engagement", "Proposed Engagement"),
]
ORAL_CRITERIA_KEYS = [
    ("clarity", "Clarity"),
    ("organization_coherence", "Organization / Coherence"),
    ("meaning_development", "Meaning / Development"),
]
LOGICAL_CRITERIA_KEYS = [
    ("relevance_of_variables", "Relevance of Variables"),
    ("rationale_for_variables", "Rationale for Variables"),
    ("quality_of_answer", "Quality of Answer"),
    ("comfort_with_ambiguity", "Comfort with Ambiguity"),
]

# All rows that appear in the score matrix (in display order)
MATRIX_ROWS = [
    # (row_label, written_key, oral_key, logical_key)
    ("Clarity",                  "clarity",                  "clarity",                  None),
    ("Organization / Coherence", "organization_coherence",   "organization_coherence",   None),
    ("Meaning / Development",    "meaning_development",       "meaning_development",      None),
    ("Proposed Engagement",      "proposed_engagement",       None,                       None),
    ("Relevance of Variables",   None,                        None,                       "relevance_of_variables"),
    ("Rationale for Variables",  None,                        None,                       "rationale_for_variables"),
    ("Quality of Answer",        None,                        None,                       "quality_of_answer"),
    ("Comfort with Ambiguity",   None,                        None,                       "comfort_with_ambiguity"),
    ("Compass Modifier",         "compass_modifier",          "compass_modifier",         "compass_modifier"),
]


def fmt_score(val, decimals=1) -> str:
    if val is None:
        return "—"
    return f"{val:.{decimals}f}"


def get_criterion_score(sec: dict, key: str) -> float | None:
    if key == "compass_modifier":
        return sec.get("compass", {}).get("compass_modifier")
    item = sec.get(key)
    if isinstance(item, dict):
        return item.get("score")
    return None


def render_score_matrix(w_sec: dict, o_sec: dict, l_sec: dict) -> list[str]:
    w_final = w_sec.get("final_score") if w_sec else None
    o_final = o_sec.get("final_score") if o_sec else None
    l_final = l_sec.get("final_score") if l_sec else None

    lines = [
        "| Criteria | Written | Oral | Logical |",
        "|---|---|---|---|",
        f"| **Final Score** | **{fmt_score(w_final)}** | **{fmt_score(o_final)}** | **{fmt_score(l_final)}** |",
    ]
    for (label, wk, ok, lk) in MATRIX_ROWS:
        w_val = get_criterion_score(w_sec, wk) if (w_sec and wk) else None
        o_val = get_criterion_score(o_sec, ok) if (o_sec and ok) else None
        l_val = get_criterion_score(l_sec, lk) if (l_sec and lk) else None
        # Use more decimals for compass modifier
        decimals = 2 if label == "Compass Modifier" else 1
        lines.append(f"| {label} | {fmt_score(w_val, decimals)} | {fmt_score(o_val, decimals)} | {fmt_score(l_val, decimals)} |")
    return lines


def word_count_penalty_label(word_count: int | None, assessment_key: str) -> str:
    if word_count is None:
        return "—"
    thresholds = {"written": (175, 100), "oral": (160, 100), "logical": (200, 100)}
    low, very_low = thresholds.get(assessment_key, (175, 100))
    if word_count < very_low:
        return f"Cap 2.0 (word count {word_count} < {very_low})"
    elif word_count < low:
        return f"Cap 3.0 (word count {word_count} < {low})"
    return f"None ({word_count} words)"


def render_assessment_section(
    label: str,
    assessment_key: str,
    sec: dict,
    submission: dict,
) -> list[str]:
    """Render one assessment section (Written / Oral / Logical) in the new format."""
    if not sec:
        return []

    lines = [f"## {label}\n"]

    # --- Score Summary ---
    final_score = sec.get("final_score", sec.get("overall_score"))
    original_score = sec.get("original_score")
    word_count = sec.get("word_count")
    compass_modifier = sec.get("compass", {}).get("compass_modifier")

    # Recalculate criteria average
    if assessment_key == "written":
        crit_keys = [k for k, _ in WRITTEN_CRITERIA_KEYS]
    elif assessment_key == "oral":
        crit_keys = [k for k, _ in ORAL_CRITERIA_KEYS]
    else:
        crit_keys = [k for k, _ in LOGICAL_CRITERIA_KEYS]

    crit_scores = [sec[k]["score"] for k in crit_keys if k in sec and isinstance(sec.get(k), dict)]
    criteria_avg = round(sum(crit_scores) / len(crit_scores), 2) if crit_scores else None

    lines.append("### Score Summary\n")
    lines.append("| Component | Value |")
    lines.append("|---|---|")
    lines.append(f"| Word Count | {word_count if word_count is not None else '—'} |")
    lines.append(f"| Criteria Average | {fmt_score(criteria_avg, 2)} |")
    lines.append(f"| Compass Modifier | {fmt_score(compass_modifier, 2)} |")
    lines.append(f"| Original Score | {fmt_score(original_score)} |")
    lines.append(f"| Word Count Penalty | {word_count_penalty_label(word_count, assessment_key)} |")
    lines.append(f"| **Final Score** | **{fmt_score(final_score)}/5.0** |")
    lines.append("")

    # --- Assessment Question ---
    question = get_question_text(submission, assessment_key)
    if question:
        lines.append("### Assessment Question\n")
        lines.append(f"> {question}\n")

    # --- Rubric Criteria ---
    lines.append("### Rubric Criteria\n")
    criteria_def = (
        WRITTEN_CRITERIA_KEYS if assessment_key == "written"
        else ORAL_CRITERIA_KEYS if assessment_key == "oral"
        else LOGICAL_CRITERIA_KEYS
    )
    for key, display_label in criteria_def:
        item = sec.get(key, {})
        score = item.get("score")
        rationale = item.get("rationale", item.get("commentary", ""))
        if score is not None:
            lines.append(f"**{display_label}** ({fmt_score(score)}/5.0)")
            if rationale:
                lines.append(f"> {rationale}\n")

    # --- Compass Modifier ---
    compass = sec.get("compass", {})
    if compass:
        lines.append("### Compass Modifier\n")
        lines.append("| Compass | Score | Rationale |")
        lines.append("|---|---|---|")
        for cq in ("engineer", "student", "professional"):
            cq_data = compass.get(cq, {})
            cq_score = cq_data.get("score", "—")
            cq_numeric = compass_score_to_modifier(cq_score)
            cq_label = f"{cq_score} ({fmt_score(cq_numeric, 2)})" if cq_numeric is not None else cq_score
            cq_rationale = cq_data.get("rationale", "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {cq.title()} | {cq_label} | {cq_rationale} |")
        lines.append("")
        if compass.get("compass_modifier") is not None:
            lines.append(f"**Compass Modifier:** {fmt_score(compass['compass_modifier'], 2)}")
        if compass.get("overall_commentary"):
            lines.append(f"\n**Overall Commentary:** {compass['overall_commentary']}\n")

    # --- Student's Full Answer ---
    answer = get_full_answer(submission, assessment_key)
    if answer:
        lines.append("### Student's Response\n")
        # Indent each line with blockquote
        for para in answer.strip().split("\n"):
            lines.append(f"> {para}" if para.strip() else ">")
        lines.append("")

    return lines


def generate_applicant_summary(evaluation: dict, preparer_dir: Path | None = None) -> str:
    """Generate a rich 4-section per-applicant summary markdown string."""
    aid = evaluation["applicant_id"]
    kira_url, submission = load_preparer_data(preparer_dir, aid)

    w_sec = get_section(evaluation, "written")
    o_sec = get_section(evaluation, "oral")
    l_sec = get_section(evaluation, "logical")

    lines = [f"# Applicant {aid}\n"]
    lines.append("[← Evaluation Report](../evaluation_report.md)\n")
    lines.append("")

    # --- Kira link ---
    if kira_url:
        lines.append(f"[Kira Review Profile]({kira_url})\n")

    # --- Score Matrix ---
    lines.append("## Score Matrix\n")
    lines.extend(render_score_matrix(w_sec, o_sec, l_sec))
    lines.append("")

    # --- Evaluation Metadata ---
    meta = evaluation.get("evaluation_metadata", {})
    if meta:
        lines.append("## Evaluation Metadata\n")
        if meta.get("evaluation_date"):
            lines.append(f"- **Evaluation Date:** {meta['evaluation_date']}")
        if meta.get("llm_model"):
            lines.append(f"- **Model:** {meta['llm_model']}")
        if meta.get("assessment_instruction_version"):
            lines.append(f"- **Instruction Version:** {meta['assessment_instruction_version']}")
        if meta.get("rubrics_version"):
            lines.append(f"- **Rubrics Version:** {meta['rubrics_version']}")
        lines.append("")

    # --- Per-assessment sections ---
    if w_sec:
        lines.extend(render_assessment_section("Written Communication", "written", w_sec, submission))
    if o_sec:
        lines.extend(render_assessment_section("Oral Communication", "oral", o_sec, submission))
    if l_sec:
        lines.extend(render_assessment_section("Logical Thinking", "logical", l_sec, submission))

    return "\n".join(lines)


def generate_aggregate_report(evaluations: list[dict], stats: dict) -> str:
    """Generate formatted aggregate markdown report."""
    lines = []

    lines.append("# BBA Applicant Evaluation Report")
    lines.append(f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"\n**Total Applicants Evaluated:** {stats['total_applicants']}")
    lines.append("\n---\n")

    # Summary Statistics
    lines.append("## Summary Statistics\n")
    lines.append("### Assessment Scores\n")
    lines.append("| Assessment Type | n | Mean | Median | Std Dev | Min | Max |")
    lines.append("|-----------------|---|------|--------|---------|-----|-----|")
    for key, label in [("written", "Written (Q1)"), ("oral", "Oral Reflective (Q2)"), ("logical", "Oral Logical (Q3)")]:
        s = stats[key]
        lines.append(f"| {label} | {s['n']} | {s['mean']:.2f} | {s['median']:.2f} | {s['stdev']:.2f} | {s['min']:.1f} | {s['max']:.1f} |")
    lines.append("\n---\n")

    # Score Distribution
    lines.append("## Score Distribution\n")
    for assessment, label in [("written", "Written Communications"), ("oral", "Oral Communications"), ("logical", "Logical Thinking")]:
        lines.append(f"### {label}\n")
        lines.append("| Score | Count | Percentage |")
        lines.append("|-------|-------|------------|")
        dist = stats[assessment]["distribution"]
        total = stats[assessment]["n"]
        for score in range(5, 0, -1):
            count = dist[score]
            percentage = (count / total * 100) if total > 0 else 0
            lines.append(f"| {score} | {count} | {percentage:.1f}% |")
        lines.append("")
    lines.append("---\n")

    # Performance Analysis
    lines.append("## Performance Analysis\n")
    for assessment, label in [("written", "Written Communications"), ("oral", "Oral Communications"), ("logical", "Logical Thinking")]:
        lines.append(f"### {label}\n")
        lines.append("| Category | Score | Candidates |")
        lines.append("|----------|-------|------------|")
        bw = stats["best_worst"][assessment]
        lines.append(f"| Best | {bw['best']['score']:.1f} | {', '.join(bw['best']['candidates'])} |")
        lines.append(f"| Worst | {bw['worst']['score']:.1f} | {', '.join(bw['worst']['candidates'])} |")
        lines.append("")
    lines.append("---\n")

    # Applicant Score Matrix — sorted by composite score descending
    def composite(e: dict) -> float:
        w = get_score(e, "written") or 0.0
        o = get_score(e, "oral") or 0.0
        l = get_score(e, "logical") or 0.0
        return w + o + l

    lines.append("## Applicants\n")
    lines.append("| Applicant | Written (Q1) | Oral (Q2) | Logical (Q3) | Total |")
    lines.append("|---|---|---|---|---|")
    for evaluation in sorted(evaluations, key=composite, reverse=True):
        aid = evaluation["applicant_id"]
        w = get_score(evaluation, "written")
        o = get_score(evaluation, "oral")
        l = get_score(evaluation, "logical")
        total = (w or 0.0) + (o or 0.0) + (l or 0.0)
        w_str = f"{w:.1f}" if w is not None else "—"
        o_str = f"{o:.1f}" if o is not None else "—"
        l_str = f"{l:.1f}" if l is not None else "—"
        lines.append(f"| [{aid}]({aid}/summary.md) | {w_str} | {o_str} | {l_str} | {total:.1f} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate markdown evaluation report from assessor data"
    )
    p.add_argument(
        "--input-dir", required=True, type=Path,
        help="Directory containing per-applicant evaluation folders (e.g. data/assessor/)"
    )
    p.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write reports to (e.g. data/analyst/)"
    )
    p.add_argument(
        "--preparer-dir", required=False, type=Path, default=None,
        help="Optional preparer directory to read Kira URLs and student answers (e.g. data/preparer/)"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    preparer_dir = args.preparer_dir.resolve() if args.preparer_dir else None

    if not input_dir.is_dir():
        log(f"ERROR: Input directory does not exist: {input_dir}")
        return 2

    log("Loading evaluations...")
    evaluations = load_evaluations(input_dir)

    if not evaluations:
        log("ERROR: No evaluation files found in input directory")
        return 2

    log(f"Loaded {len(evaluations)} evaluations")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Calculate statistics
    log("Calculating statistics...")
    stats = calculate_statistics(evaluations)

    # Generate aggregate report
    log("Generating aggregate report...")
    report_md = generate_aggregate_report(evaluations, stats)
    report_path = output_dir / "evaluation_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    log(f"Written: {report_path}")

    # Generate per-applicant summaries
    log("Generating per-applicant summaries...")
    for evaluation in evaluations:
        aid = evaluation["applicant_id"]
        applicant_out = output_dir / aid
        applicant_out.mkdir(parents=True, exist_ok=True)
        summary_md = generate_applicant_summary(evaluation, preparer_dir)
        summary_path = applicant_out / "summary.md"
        summary_path.write_text(summary_md, encoding="utf-8")

    # JSON summary to stdout
    summary = {
        "action": "generate_markdown_report",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "total_evaluations": len(evaluations),
        "report_file": str(report_path),
        "per_applicant_summaries": len(evaluations),
        "statistics": {
            "written_mean": round(stats["written"]["mean"], 2),
            "oral_mean": round(stats["oral"]["mean"], 2),
            "logical_mean": round(stats["logical"]["mean"], 2),
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
