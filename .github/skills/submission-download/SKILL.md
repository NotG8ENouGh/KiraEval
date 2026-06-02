---
name: submission-download
description: Downloads applicant submissions (written text + video files) from Kira Review platform using Selenium browser automation. Use when asked to download or fetch applicant data from Kira Review.
allowed-tools: shell
---

## Purpose

Downloads applicant written responses and video submissions from the Kira Review platform (review.kiratalent.com). Uses Selenium with Chrome to navigate the review interface, extract written text, and download video files.

## Prerequisites

- Chrome browser installed
- `.env` file with KIRA_USERNAME and KIRA_PASSWORD
- Network access to review.kiratalent.com

## Usage

```bash
uv run .github/skills/submission-download/scripts/download.py --output-dir data/preparer/ [OPTIONS]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--output-dir` | Directory to save applicant data | Required |
| `--dry-run` | List applicants without downloading | false |
| `--limit N` | Maximum number of applicants to process | unlimited |
| `--headless` | Run browser in headless mode | true |
| `--all-candidates` | Download all COMPLETE applicants (not just unreviewed) | false |

### Output Structure

For each applicant, creates:
```
{output-dir}/{applicant_id}/
├── written.txt       # Q1 written response text
├── oral.mp4          # Q2 oral communication video
├── logical.mp4       # Q3 logical thinking video
└── meta.json         # Metadata (name, status, download timestamp)
```

### Exit Codes

- 0: Success (or dry-run completed)
- 1: Login failed
- 2: No applicants found
- 3: Download error (partial — check stderr for which applicants failed)

## Troubleshooting

- If login fails, verify .env credentials
- If videos don't download, CDN URLs may have expired — retry
- Use `--headless false` for debugging selector issues
