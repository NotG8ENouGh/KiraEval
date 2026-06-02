# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openai-whisper>=20231117",
#     "torch>=2.0",
# ]
# ///
"""
Transcribe oral and logical video responses using openai-whisper (local model).

Processes all applicant folders in the input directory that have .mp4 video
files but no existing transcripts. Produces JSON transcripts with full text,
timestamps, and word counts.

Usage:
    uv run .github/skills/submission-video-transcribe/scripts/transcribe.py --input-dir data/preparer/
    uv run .github/skills/submission-video-transcribe/scripts/transcribe.py --input-dir data/preparer/ --model tiny
    uv run .github/skills/submission-video-transcribe/scripts/transcribe.py --input-dir data/preparer/ --limit 5 --force
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_file(model, video_path: Path, model_name: str) -> dict:
    """Transcribe a single video file and return structured result."""
    log.info(f"  Transcribing {video_path.name}...")
    result = model.transcribe(str(video_path), fp16=False)

    text = result["text"].strip()
    segments = [
        {
            "start": s["start"],
            "end": s["end"],
            "text": s["text"].strip(),
        }
        for s in result.get("segments", [])
    ]

    return {
        "text": text,
        "word_count": len(text.split()),
        "segments": segments,
        "language": result.get("language", ""),
        "model": model_name,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# FFmpeg check
# ---------------------------------------------------------------------------

def ensure_ffmpeg() -> None:
    """Ensure ffmpeg is available on PATH."""
    if shutil.which("ffmpeg"):
        return

    # Try common Windows locations
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links",
        Path(os.environ.get("PROGRAMFILES", "")) / "ffmpeg/bin",
        Path(os.environ.get("USERPROFILE", "")) / "scoop/shims",
    ]
    for candidate in candidates:
        if (candidate / "ffmpeg.exe").exists():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            log.info(f"Added ffmpeg to PATH from {candidate}")
            return

    log.error("ffmpeg not found. Install it: winget install Gyan.FFmpeg")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Transcribe applicant oral/logical videos using openai-whisper"
    )
    p.add_argument(
        "--input-dir", required=True,
        help="Directory containing applicant folders with .mp4 files (e.g. data/preparer/)"
    )
    p.add_argument(
        "--model", default="medium",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: medium)"
    )
    p.add_argument("--limit", type=int, default=None, help="Maximum number of applicants to process")
    p.add_argument("--force", action="store_true", help="Re-transcribe even if transcript already exists")
    return p.parse_args()


def main():
    args = parse_args()
    input_dir = Path(args.input_dir)

    if not input_dir.exists():
        log.error(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    # Collect applicants that need transcription
    pending = []
    for aid_dir in sorted(input_dir.iterdir()):
        if not aid_dir.is_dir() or aid_dir.name.startswith("."):
            continue

        aid = aid_dir.name
        oral = aid_dir / "oral.mp4"
        logical = aid_dir / "logical.mp4"

        if not oral.exists() and not logical.exists():
            continue

        # Check if already done (unless --force)
        oral_done = (aid_dir / "oral_transcript.json").exists() and not args.force
        logical_done = (aid_dir / "logical_transcript.json").exists() and not args.force

        # Skip if no work needed
        oral_needed = oral.exists() and not oral_done
        logical_needed = logical.exists() and not logical_done

        if not oral_needed and not logical_needed:
            log.info(f"[{aid}] Already transcribed, skipping")
            continue

        pending.append((aid, aid_dir, oral_needed, logical_needed))

    if not pending:
        log.error("No video files found that need transcription")
        sys.exit(1)

    log.info(f"Pending transcription: {len(pending)} applicants")

    if args.limit:
        pending = pending[: args.limit]
        log.info(f"Limiting to {args.limit}")

    # Ensure ffmpeg is available before loading model
    ensure_ffmpeg()

    log.info(f"Loading Whisper model '{args.model}'...")
    import whisper
    model = whisper.load_model(args.model)
    log.info("Model loaded.")

    done_ids = []
    failed_ids = []

    for i, (aid, aid_dir, oral_needed, logical_needed) in enumerate(pending, 1):
        log.info(f"=== [{i}/{len(pending)}] {aid} ===")

        try:
            if oral_needed:
                oral_mp4 = aid_dir / "oral.mp4"
                oral_result = transcribe_file(model, oral_mp4, args.model)
                (aid_dir / "oral_transcript.json").write_text(
                    json.dumps(oral_result, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                log.info(f"[{aid}] Oral transcript: {oral_result['word_count']} words")

            if logical_needed:
                logical_mp4 = aid_dir / "logical.mp4"
                logical_result = transcribe_file(model, logical_mp4, args.model)
                (aid_dir / "logical_transcript.json").write_text(
                    json.dumps(logical_result, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                log.info(f"[{aid}] Logical transcript: {logical_result['word_count']} words")

            done_ids.append(aid)

        except Exception as e:
            log.error(f"[{aid}] Transcription failed: {e}", exc_info=True)
            failed_ids.append(aid)

    log.info(f"Transcription complete. Done: {len(done_ids)}  Failed: {len(failed_ids)}")

    # Structured JSON output to stdout
    summary = {
        "status": "success" if not failed_ids else "partial",
        "transcribed": done_ids,
        "failed": failed_ids,
        "total_processed": len(done_ids) + len(failed_ids),
        "model": args.model,
        "input_dir": str(input_dir),
    }
    print(json.dumps(summary, indent=2))

    if failed_ids:
        sys.exit(2)


if __name__ == "__main__":
    main()
