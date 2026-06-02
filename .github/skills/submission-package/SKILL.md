---
name: submission-package
description: Consolidates raw applicant downloads and transcripts into a single structured submission JSON per applicant with word counts, question text, and all responses. Use when asked to package or consolidate applicant data.
allowed-tools: shell
---

## Purpose

Takes raw downloaded files (written.txt, meta.json) and transcripts (oral_transcript.json, logical_transcript.json) from a per-applicant folder and consolidates them into a single `submission.json` ready for assessment.

## Usage

```bash
uv run .github/skills/submission-package/scripts/package.py --input-dir data/preparer/ [OPTIONS]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--input-dir` | Directory containing applicant folders | Required |
| `--limit N` | Maximum number of applicants to process | unlimited |
| `--force` | Re-package even if submission.json exists | false |

### Input Expected

Each applicant folder should contain:
```
{input-dir}/{applicant_id}/
├── written.txt                 # Required: Q1 written response
├── meta.json                   # Required: download metadata (contains question text)
├── oral_transcript.json        # Required: Q2 transcript
└── logical_transcript.json     # Required: Q3 transcript
```

### Output

Creates `submission.json` in each applicant folder:
```json
{
  "applicant_id": "abc123",
  "q1_written": {
    "question": "Question text from Kira...",
    "response": "Applicant's written response...",
    "word_count": 312
  },
  "q2_oral": {
    "question": "Question text...",
    "response": "Full transcript text...",
    "word_count": 198,
    "transcript_segments": [...]
  },
  "q3_logical": {
    "question": "Question text...",
    "response": "Full transcript text...",
    "word_count": 267,
    "transcript_segments": [...]
  },
  "metadata": {
    "downloaded_at": "...",
    "transcribed_at": "...",
    "packaged_at": "2026-05-22T14:00:00Z"
  }
}
```

### Exit Codes

- 0: Success
- 1: No applicant folders found
- 2: Packaging error (partial — check stderr)
