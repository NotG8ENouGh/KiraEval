# BBA Applicant Assessment Automation

Automates evaluation of university applicants from the Kira Review platform — downloading submissions, transcribing videos, scoring against rubrics using AI co-pilot assessment, and generating reports.

## System Overview

This system provides **AI co-pilot second-opinion assessment** alongside human reviewer evaluation. The AI assessor:
- Uses rubric-based scoring (1-5 scale, 0.1 increments) across 3 assessments
- Applies compass questions (Engineer/Student/Professional) for holistic assessment
- Provides independent scores that the human reviewer weighs against their own
- Operates with awareness of its own AI-specific biases (sycophancy, fluent prose bias, etc.)

## Agent Architecture

The system is structured as a **team of 4 GitHub Copilot agents** with packaged skills:

```
┌─────────────────────────┐
│    BBA-Orchestrator      │  Entry point — delegates only
└──────────┬──────────────┘
           │
    ┌──────┼──────────────────┐
    │      │                  │
    ▼      ▼                  ▼
┌────────┐ ┌────────┐ ┌────────┐
│Preparer│ │Assessor│ │Analyst │
└────────┘ └────────┘ └────────┘
```

| Agent | Role | Tools |
|-------|------|-------|
| **BBA-Orchestrator** | Coordinator — breaks down requests, delegates, validates handoffs | `agent`, `read`, `search` |
| **BBA-Preparer** | Downloads, transcribes, packages applicant data | `execute`, `read`, `search` |
| **BBA-Assessor** | Evaluates applicants against rubrics (prompt-only, IS the assessor) | `read`, `edit` |
| **BBA-Analyst** | Generates reports, Excel exports, verifies data coverage | `execute`, `read`, `search` |

### Data Flow

```
data/preparer/{id}/submission.json  →  data/assessor/{id}/evaluation.json  →  data/analyst/
```

Each agent owns its workspace directory. The Orchestrator validates handoff contracts between stages.

## Skills

Skills are packaged in `.github/skills/` and run with `uv run` (PEP 723 self-contained scripts):

| Skill | Agent | Purpose |
|-------|-------|---------|
| `submission-download` | Preparer | Selenium scraper for Kira Review |
| `submission-video-transcribe` | Preparer | Local Whisper transcription |
| `submission-package` | Preparer | Consolidate into submission JSON |
| `evaluation-summarization` | Analyst | Reports + Excel + verification |

## Setup

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Fill in credentials
cp .env.example .env
```

Fill in `.env`:
```
KIRA_USERNAME=your@email.com
KIRA_PASSWORD=yourpassword
```

## Usage

### With Copilot Agents (recommended)

Start with the BBA-Orchestrator agent and give it high-level instructions:
- "Process all new applicants end-to-end"
- "Download and prepare applicant submissions"
- "Generate reports for all assessed applicants"

### Manual CLI (via skills)

```bash
# Stage 1: Download applicant data
uv run .github/skills/submission-download/scripts/download.py --output-dir data/preparer/ --limit 1

# Stage 2: Transcribe videos
uv run .github/skills/submission-video-transcribe/scripts/transcribe.py --input-dir data/preparer/

# Stage 3: Package into submission JSON
uv run .github/skills/submission-package/scripts/package.py --input-dir data/preparer/

# Stage 4: Assessment (via BBA-Assessor agent — reads submission.json, writes evaluation.json)

# Stage 5: Generate reports
uv run .github/skills/evaluation-summarization/scripts/generate_markdown_report.py --input-dir data/assessor/ --output-dir data/analyst/
uv run .github/skills/evaluation-summarization/scripts/generate_excel_summary.py --input-dir data/assessor/ --output-dir data/analyst/
uv run .github/skills/evaluation-summarization/scripts/verify_coverage.py --preparer-dir data/preparer/ --assessor-dir data/assessor/ --analyst-dir data/analyst/
```

## Project Structure

```
├── .env                           # Credentials (not tracked)
├── .github/
│   ├── agents/                    # Copilot agent profiles
│   │   ├── bba-orchestrator.agent.md
│   │   ├── bba-preparer.agent.md
│   │   ├── bba-assessor.agent.md
│   │   └── bba-analyst.agent.md
│   └── skills/                    # Packaged skills (PEP 723 + uv run)
│       ├── submission-download/
│       │   ├── SKILL.md
│       │   ├── scripts/download.py
│       │   └── evals/evals.json
│       ├── submission-video-transcribe/
│       │   ├── SKILL.md
│       │   ├── scripts/transcribe.py
│       │   └── evals/evals.json
│       ├── submission-package/
│       │   ├── SKILL.md
│       │   ├── scripts/package.py
│       │   └── evals/evals.json
│       └── evaluation-summarization/
│           ├── SKILL.md
│           ├── scripts/generate_markdown_report.py
│           ├── scripts/generate_excel_summary.py
│           ├── scripts/verify_coverage.py
│           └── evals/evals.json
├── data/
│   ├── preparer/{id}/            # BBA-Preparer workspace
│   ├── assessor/{id}/            # BBA-Assessor workspace
│   └── analyst/                  # BBA-Analyst workspace
├── ref/                           # Rubrics and system prompt
│   ├── assessment_system_prompt.md
│   └── rubrics.md
└── scripts/                       # Legacy scripts (original, for reference)
    └── config.yml
```

## Assessment Types

**Three assessments per applicant:**

1. **Q1 - Written Communications** (300-350 words, 10-minute time limit)
   - Criteria: Clarity, Organization/Coherence, Meaning/Development, Proposed Engagement
   - Addresses: personal motivation, program fit, U of T engagement

2. **Q2 - Oral Communications** (2-min video, 2-min prep)
   - Criteria: Clarity, Organization/Coherence, Meaning/Development
   - Addresses: influential experiences, interests, what shaped them

3. **Q3 - Logical Thinking** (3-min video, 2-min prep)
   - Criteria: Relevance of Variables, Rationale for Variables, Quality of Answer, Comfort with Ambiguity
   - Addresses: engineering scenario, identify 3 variables and how to incorporate them

## Scoring System (as of May 17, 2026)

### Formula

For each assessment (Q1, Q2, Q3):

```
1. Calculate criteria average (0.1 increments, 1.0-5.0)
   - Written: (clarity + organization + meaning + engagement) / 4
   - Oral: (clarity + organization + meaning) / 3
   - Logical: (relevance + rationale + quality + ambiguity) / 4

2. Calculate compass modifier (-0.6 to +0.4)
   - Answer 3 compass questions: Engineer, Student, Professional
   - Each uses 6-option scale: clear yes (+0.4), lean yes (+0.15), neutral (-0.1), 
     genuinely uncertain (-0.1), lean no (-0.35), clear no (-0.6)
   - Modifier = average of 3 compass scores
   - Note: Anchored at -0.1 to magnify deductions

3. Apply compass modifier
   - modified_score = criteria_average + compass_modifier

4. Apply word count penalty (ABSOLUTE CAP)
   - If word_count < 100: final_score = min(modified_score, 2.0)
   - Else if word_count < 175/160/200 (Q1/Q2/Q3): final_score = min(modified_score, 3.0)
   - Else: final_score = modified_score

5. Round to nearest 0.1 increment
```

### Compass Questions

For each assessment, the AI answers three holistic questions:

1. **Engineer**: Do I see the early shape of a good engineer — curiosity, reasoning, resilience, disposition to make and figure out?

2. **Student**: Is this someone who will add to and survive this institution over four demanding years?

3. **Professional**: Do I see seeds of someone the profession will be glad to have — technically, ethically, collaboratively?

### Output Format

Each evaluation JSON includes:
- **Evaluation Metadata**: versions, date, model, duration
- **Personal Background Identification**: extracted from responses for anti-bias analysis
- **Candidate Hook**: 25-word vivid description (no names, no adjectives)
- **Assessment Scores**: rubric criteria with rationales (80 words max)
- **Compass Assessment**: per Q1/Q2/Q3 with rationales and overall commentary (100 words max)
- **Final Scores**: word count, original score, final score (post-compass + penalty)

## Reference Documents

- `ref/assessment_system_prompt.md` - Complete instructions for AI co-pilot assessor (embedded in BBA-Assessor agent)
- `ref/rubrics.md` - Official scoring rubrics for all 3 assessments (embedded in BBA-Assessor agent)

## Legacy

Original scripts remain in `scripts/` for reference. The active implementation is in `.github/skills/`.
