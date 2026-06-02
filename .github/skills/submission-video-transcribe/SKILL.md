---
name: submission-video-transcribe
description: Transcribes oral and logical video responses using openai-whisper (local model). Produces JSON transcripts with full text, timestamps, and word counts. Use when asked to transcribe applicant videos.
allowed-tools: shell
---

## Purpose

Transcribes .mp4 video files (oral and logical responses) into JSON transcripts using the openai-whisper model running locally. Processes all applicant folders that have video files but no existing transcripts.

## Prerequisites

- FFmpeg installed (required by whisper)
- Sufficient disk space for whisper model download (~1.5GB for medium model)

## Usage

```bash
uv run .github/skills/submission-video-transcribe/scripts/transcribe.py --input-dir data/preparer/ [OPTIONS]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--input-dir` | Directory containing applicant folders with .mp4 files | Required |
| `--model` | Whisper model size (tiny, base, small, medium, large) | medium |
| `--limit N` | Maximum number of applicants to process | unlimited |
| `--force` | Re-transcribe even if transcript already exists | false |

### Output

For each applicant folder containing videos, creates:
```
{input-dir}/{applicant_id}/
├── oral_transcript.json      # Transcript of oral.mp4
└── logical_transcript.json   # Transcript of logical.mp4
```

Each transcript JSON contains:
```json
{
  "text": "Full transcript text...",
  "word_count": 245,
  "segments": [
    {"start": 0.0, "end": 2.5, "text": "segment text..."}
  ],
  "model": "medium",
  "transcribed_at": "2026-05-22T14:00:00Z"
}
```

### Exit Codes

- 0: Success (all videos transcribed)
- 1: No video files found in input directory
- 2: Transcription error (partial — check stderr for which files failed)

## Notes

- First run downloads the whisper model (~1.5GB for medium)
- Transcription is CPU-intensive; medium model takes ~2-4 min per 3-min video
- Use `--model tiny` for faster testing (lower quality)
