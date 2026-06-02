# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
Consolidate raw applicant downloads and transcripts into a single submission.json
per applicant with word counts, question text, and all responses.

Usage:
    uv run .github/skills/submission-package/scripts/package.py --input-dir data/preparer/
    uv run .github/skills/submission-package/scripts/package.py --input-dir data/preparer/ --limit 3
    uv run .github/skills/submission-package/scripts/package.py --input-dir data/preparer/ --force
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def word_count(text: str) -> int:
    return len(text.split()) if text.strip() else 0


def extract_question(competencies: list, name_keyword: str) -> str:
    """Extract question text from competencies list by keyword match."""
    for comp in competencies:
        if name_keyword.lower() in (comp.get("name") or "").lower() and comp.get("reviewable"):
            for qr in comp.get("competency_question_responses", []):
                q = (qr.get("question") or "").strip()
                if q:
                    return q
    return ""


def parse_written_txt(path: Path) -> tuple[str, str]:
    """Return (question, response) parsed from 'Q: ...\\n\\nA: ...' format."""
    text = path.read_text(encoding="utf-8")
    if "\n\nA: " in text:
        q_part, a_part = text.split("\n\nA: ", 1)
        question = q_part.removeprefix("Q: ").strip()
        response = a_part.strip()
    else:
        question = ""
        response = text.strip()
    return question, response


def package_applicant(applicant_dir: Path) -> dict:
    """Package a single applicant folder into a submission dict."""
    aid = applicant_dir.name

    # Load meta.json for question text and metadata
    meta_path = applicant_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found in {applicant_dir}")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    competencies = meta.get("competencies", [])

    submission = {"applicant_id": aid}
    metadata = {
        "downloaded_at": meta.get("downloaded_at", meta.get("date_responded", "")),
        "packaged_at": datetime.now(timezone.utc).isoformat(),
    }

    # Q1: Written response
    written_path = applicant_dir / "written.txt"
    if written_path.exists():
        w_question, w_response = parse_written_txt(written_path)
        if not w_question:
            w_question = extract_question(competencies, "written")
        submission["q1_written"] = {
            "question": w_question,
            "response": w_response,
            "word_count": word_count(w_response),
        }
    else:
        log(f"  WARNING: {aid} missing written.txt")

    # Q2: Oral transcript
    oral_path = applicant_dir / "oral_transcript.json"
    if oral_path.exists():
        oral_data = json.loads(oral_path.read_text(encoding="utf-8"))
        oral_text = oral_data.get("text", "").strip()
        segments = oral_data.get("segments", [])
        submission["q2_oral"] = {
            "question": extract_question(competencies, "oral"),
            "response": oral_text,
            "word_count": word_count(oral_text),
            "transcript_segments": segments,
        }
        if oral_data.get("transcribed_at"):
            metadata["transcribed_at"] = oral_data["transcribed_at"]
    else:
        log(f"  WARNING: {aid} missing oral_transcript.json")

    # Q3: Logical transcript
    logical_path = applicant_dir / "logical_transcript.json"
    if logical_path.exists():
        logical_data = json.loads(logical_path.read_text(encoding="utf-8"))
        logical_text = logical_data.get("text", "").strip()
        segments = logical_data.get("segments", [])
        submission["q3_logical"] = {
            "question": extract_question(competencies, "logical"),
            "response": logical_text,
            "word_count": word_count(logical_text),
            "transcript_segments": segments,
        }
        if logical_data.get("transcribed_at") and "transcribed_at" not in metadata:
            metadata["transcribed_at"] = logical_data["transcribed_at"]
    else:
        log(f"  WARNING: {aid} missing logical_transcript.json")

    submission["metadata"] = metadata
    return submission


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Consolidate applicant data into submission.json files"
    )
    p.add_argument(
        "--input-dir", required=True, type=Path,
        help="Directory containing per-applicant folders"
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Maximum number of applicants to process"
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-package even if submission.json already exists"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()

    if not input_dir.is_dir():
        log(f"ERROR: Input directory does not exist: {input_dir}")
        return 1

    # Collect applicant folders
    applicant_dirs = sorted(
        d for d in input_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if not applicant_dirs:
        log("ERROR: No applicant folders found in input directory")
        return 1

    # Filter already-packaged unless --force
    pending = []
    for d in applicant_dirs:
        out_path = d / "submission.json"
        if out_path.exists() and not args.force:
            log(f"  SKIP: {d.name} (submission.json exists)")
            continue
        # Must have meta.json at minimum
        if not (d / "meta.json").exists():
            log(f"  SKIP: {d.name} (no meta.json)")
            continue
        pending.append(d)

    if args.limit:
        pending = pending[: args.limit]

    if not pending:
        log("Nothing to package (all already done or no valid folders).")
        return 1

    log(f"Packaging {len(pending)} applicants...")

    done = 0
    failed = []

    for i, applicant_dir in enumerate(pending, 1):
        aid = applicant_dir.name
        log(f"  [{i}/{len(pending)}] {aid}")
        try:
            submission = package_applicant(applicant_dir)
            out_path = applicant_dir / "submission.json"
            out_path.write_text(
                json.dumps(submission, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            done += 1
        except Exception as e:
            log(f"  ERROR: {aid}: {e}")
            failed.append({"applicant_id": aid, "error": str(e)})

    # Print JSON summary to stdout
    summary = {
        "action": "submission-package",
        "input_dir": str(input_dir),
        "total_folders": len(applicant_dirs),
        "packaged": done,
        "failed": len(failed),
        "failures": failed,
    }
    print(json.dumps(summary, indent=2))

    if failed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
