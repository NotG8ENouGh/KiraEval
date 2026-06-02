---
name: evaluation-summarization
description: Generates evaluation reports (per-applicant markdown, aggregate summary, Excel export) and verifies data coverage across all pipeline stages. Use when asked to generate reports, summaries, or verify assessment data.
allowed-tools: shell
---

## Purpose

Produces reports and summaries from completed applicant evaluations, and verifies data integrity across the full pipeline (preparer → assessor → analyst).

## Available Scripts

### 1. Generate Markdown Report

Produces an aggregate markdown report with per-applicant summaries and cohort statistics.

```bash
uv run .github/skills/evaluation-summarization/scripts/generate_markdown_report.py --input-dir data/assessor/ --output-dir data/analyst/
```

### 2. Generate Excel Summary

Produces an Excel spreadsheet with scores, statistics, and formatting.

```bash
uv run .github/skills/evaluation-summarization/scripts/generate_excel_summary.py --input-dir data/assessor/ --output-dir data/analyst/
```

### 3. Verify Coverage

Cross-checks data across all pipeline stages to ensure completeness.

```bash
uv run .github/skills/evaluation-summarization/scripts/verify_coverage.py --preparer-dir data/preparer/ --assessor-dir data/assessor/ --analyst-dir data/analyst/
```

### Standard Workflow

Run all three in sequence:
1. `generate_markdown_report.py` → creates `evaluation_report.md`
2. `generate_excel_summary.py` → creates `evaluation_summary.xlsx`
3. `verify_coverage.py` → creates `verification_report.txt`, exits non-zero if gaps found

### Exit Codes (all scripts)

- 0: Success
- 1: Errors found (verification failures or missing data)
- 2: No evaluation files found in input directory

### Output Files

```
data/analyst/
├── {applicant_id}/
│   └── summary.md               # Per-applicant markdown (from markdown report script)
├── evaluation_report.md          # Full cohort report
├── evaluation_summary.xlsx       # Excel with scores + stats
└── verification_report.txt       # Coverage check results
```
