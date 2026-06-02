# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "selenium>=4.20",
#     "webdriver-manager>=4.0",
#     "requests>=2.31",
#     "python-dotenv>=1.0",
# ]
# ///
"""
Download applicant submissions from Kira Review.

Uses Selenium to log in, list applicants, extract written text from API
responses, and download oral/logical video files from CDN URLs.

Usage:
    uv run .github/skills/submission-download/scripts/download.py --output-dir data/preparer/
    uv run .github/skills/submission-download/scripts/download.py --output-dir data/preparer/ --dry-run
    uv run .github/skills/submission-download/scripts/download.py --output-dir data/preparer/ --limit 1
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Load .env from project root (two levels up from .github/skills/submission-download/scripts/)
PROJECT_DIR = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def build_applicant_list_url(download_all: bool) -> str:
    """Build the Kira Review applicant list URL."""
    base = "https://review.kiratalent.com/applicants"
    params = [
        "page=1",
        "limit=50",
        "sort=assignment_date",
        "order=ASC",
        "applicant_status=COMPLETE",
    ]
    if not download_all:
        params.append("review_status=UNREVIEWED%2CIN_REVIEW")
    return base + "?" + "&".join(params)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state(output_dir: Path) -> dict:
    state_file = output_dir / ".download_state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"downloaded": [], "failed": {}, "last_run": None}


def save_state(state: dict, output_dir: Path) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state_file = output_dir / ".download_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

def build_driver(headless: bool) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(driver) -> None:
    log.info("Navigating to Kira Review (will redirect to auth.kiratalent.com)")
    driver.get("https://review.kiratalent.com/overview")

    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.NAME, "email")))

    username = os.environ.get("KIRA_USERNAME")
    password = os.environ.get("KIRA_PASSWORD")
    if not username or not password:
        log.error("KIRA_USERNAME and KIRA_PASSWORD must be set in .env")
        sys.exit(1)

    driver.find_element(By.NAME, "email").send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    try:
        WebDriverWait(driver, 20).until(EC.url_contains("review.kiratalent.com/overview"))
    except Exception:
        log.error("Login failed — timed out waiting for redirect")
        sys.exit(1)

    time.sleep(2)
    log.info("Logged in successfully")


# ---------------------------------------------------------------------------
# Applicant list
# ---------------------------------------------------------------------------

def get_applicant_ids(driver, download_all: bool) -> list[str]:
    """Fetch applicant IDs from the list (all or unreviewed only)."""
    base_url = build_applicant_list_url(download_all)
    mode_desc = "all complete applicants" if download_all else "unreviewed applicants"
    log.info(f"Fetching {mode_desc}")

    all_ids = []
    page = 1
    limit = 50

    while True:
        page_url = base_url.replace("page=1", f"page={page}")
        log.info(f"Fetching page {page}...")
        driver.get(page_url)
        time.sleep(4)

        rows = driver.find_elements(By.CSS_SELECTOR, "tbody tr")
        if not rows:
            log.info(f"Page {page}: no results found, stopping pagination")
            break

        page_ids = []
        for row in rows:
            text = row.text
            match = re.search(r'\b([A-Za-z0-9]{6})\b', text)
            if match:
                page_ids.append(match.group(1))

        if not page_ids:
            log.info(f"Page {page}: no IDs extracted, stopping pagination")
            break

        all_ids.extend(page_ids)
        log.info(f"Page {page}: found {len(page_ids)} candidates")

        if len(page_ids) < limit:
            log.info(f"Page {page}: got {len(page_ids)} < {limit}, last page reached")
            break

        page += 1

    log.info(f"Found {len(all_ids)} {mode_desc} across {page} page(s)")
    return all_ids


# ---------------------------------------------------------------------------
# Per-applicant extraction
# ---------------------------------------------------------------------------

def get_perf_log_response(driver, url_fragment: str) -> dict | None:
    """Extract a JSON response body from Chrome performance log by URL fragment."""
    logs = driver.get_log("performance")
    for entry in reversed(logs):
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") == "Network.responseReceived":
                url = msg["params"]["response"]["url"]
                if url_fragment in url:
                    req_id = msg["params"]["requestId"]
                    try:
                        body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": req_id})
                        return json.loads(body.get("body", "{}"))
                    except Exception as e:
                        log.debug(f"Body fetch failed for {url_fragment}: {e}")
                        continue
        except Exception:
            pass
    return None


def extract_written_text(review_data: dict) -> str:
    """Pull written response text from the review API payload."""
    if not review_data:
        return ""

    competencies = review_data.get("competencies", [])
    texts = []

    # Primary: competency named 'Written Communications' that is assigned and reviewable
    for comp in competencies:
        name = (comp.get("name") or "").lower()
        if "written" in name and comp.get("assigned") and comp.get("reviewable"):
            for qr in comp.get("competency_question_responses", []):
                response = (
                    qr.get("response_text")
                    or qr.get("response")
                    or qr.get("text_response")
                    or qr.get("answer")
                    or ""
                )
                response_type = (qr.get("response_type") or "").lower()
                if response and "video" not in response_type:
                    question = qr.get("question", "")
                    texts.append(f"Q: {question}\n\nA: {response}")

    # Fallback: any reviewable, assigned competency with non-video written responses
    if not texts:
        for comp in competencies:
            if not (comp.get("assigned") and comp.get("reviewable")):
                continue
            for qr in comp.get("competency_question_responses", []):
                response_type = (qr.get("response_type") or "").lower()
                response = (
                    qr.get("response_text")
                    or qr.get("response")
                    or qr.get("text_response")
                    or qr.get("answer")
                    or ""
                )
                if response and "video" not in response_type:
                    texts.append(f"Q: {qr.get('question','')}\n\nA: {response}")

    return "\n\n---\n\n".join(texts)


def click_section_get_video_url(driver, keyword: str, timeout: int = 30) -> str | None:
    """Click the section header matching keyword, then wait for a <source> element."""
    try:
        header = driver.find_element(By.XPATH, f"//h4[contains(text(),'{keyword}')]")
        driver.execute_script("arguments[0].click();", header)
        log.debug(f"Clicked '{keyword}' section")
    except Exception as e:
        log.warning(f"Could not click '{keyword}' section: {e}")
        return None

    try:
        WebDriverWait(driver, timeout).until(
            lambda d: any(
                s.get_attribute("src") and "cdn" in s.get_attribute("src")
                for s in d.find_elements(By.TAG_NAME, "source")
            )
        )
        sources = driver.find_elements(By.TAG_NAME, "source")
        for s in sources:
            src = s.get_attribute("src") or ""
            if "cdn" in src and ("mp4" in src or "webm" in src):
                return src
    except Exception:
        pass

    # Fallback: check <video> currentSrc
    videos = driver.find_elements(By.TAG_NAME, "video")
    for v in videos:
        src = v.get_attribute("currentSrc") or v.get_attribute("src") or ""
        if src and not src.startswith("blob:") and "cdn" in src:
            return src

    return None


def download_video(driver, url: str, dest: Path, chunk_size: int = 8192) -> None:
    """Download a CDN video using session cookies from the browser."""
    session = requests.Session()
    for c in driver.get_cookies():
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    # Also grab auth.kiratalent.com cookies
    current_url = driver.current_url
    driver.get("https://auth.kiratalent.com")
    time.sleep(1)
    for c in driver.get_cookies():
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    driver.get(current_url)
    time.sleep(2)

    ua = driver.execute_script("return navigator.userAgent;")
    headers = {
        "User-Agent": ua,
        "Referer": "https://review.kiratalent.com/",
    }

    log.info(f"Downloading: {url[:80]}...")
    with session.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
        log.info(f"Saved {downloaded:,} bytes -> {dest.name}")


def process_applicant(driver, aid: str, output_dir: Path, dry_run: bool) -> bool:
    """Process a single applicant: extract written text, download videos, write meta."""
    out_dir = output_dir / aid
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"[{aid}] Loading applicant page")
    driver.get(f"https://review.kiratalent.com/applicants/{aid}")
    time.sleep(5)

    if dry_run:
        log.info(f"[{aid}] Dry run — skipping downloads")
        return True

    success = True

    # --- Written text ---
    review_data = get_perf_log_response(driver, "get_review_and_competencies_details")
    if review_data:
        written_text = extract_written_text(review_data)
        if written_text:
            (out_dir / "written.txt").write_text(written_text, encoding="utf-8")
            log.info(f"[{aid}] Written text saved ({len(written_text)} chars)")
        else:
            log.warning(f"[{aid}] Written text extracted but empty")
            success = False
    else:
        log.warning(f"[{aid}] Could not find review API response in perf log")
        success = False

    # --- Oral video ---
    oral_url = click_section_get_video_url(driver, "Oral")
    if oral_url:
        try:
            download_video(driver, oral_url, out_dir / "oral.mp4")
        except Exception as e:
            log.error(f"[{aid}] Oral video download failed: {e}")
            success = False
    else:
        log.warning(f"[{aid}] No oral video URL found")
        success = False

    # --- Logical video ---
    logical_url = click_section_get_video_url(driver, "Logical")
    if logical_url:
        try:
            download_video(driver, logical_url, out_dir / "logical.mp4")
        except Exception as e:
            log.error(f"[{aid}] Logical video download failed: {e}")
            success = False
    else:
        log.warning(f"[{aid}] No logical video URL found")
        success = False

    # --- Metadata ---
    meta = {
        "id": aid,
        "url": f"https://review.kiratalent.com/applicants/{aid}",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": [p.name for p in out_dir.iterdir() if not p.name.startswith(".")],
        "success": success,
        "competencies": review_data.get("competencies", []) if review_data else [],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Download applicant submissions (written text + videos) from Kira Review"
    )
    p.add_argument(
        "--output-dir", required=True,
        help="Directory to save applicant data (e.g. data/preparer/)"
    )
    p.add_argument("--dry-run", action="store_true", help="List applicants without downloading")
    p.add_argument("--limit", type=int, default=None, help="Maximum number of applicants to process")
    p.add_argument(
        "--headless", type=lambda x: x.lower() != "false", default=True,
        help="Run browser in headless mode (default: true)"
    )
    p.add_argument(
        "--all-candidates", action="store_true",
        help="Download all COMPLETE applicants (not just unreviewed)"
    )
    return p.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(output_dir)
    already_done = set(state["downloaded"])
    log.info(f"Previously downloaded: {len(already_done)} applicants")

    if args.dry_run:
        log.info("DRY RUN — no files will be written")

    driver = build_driver(headless=args.headless)
    downloaded_ids = []
    failed_ids = []

    try:
        login(driver)

        all_ids = get_applicant_ids(driver, download_all=args.all_candidates)
        if not all_ids:
            log.error("No applicants found")
            sys.exit(2)

        pending = [aid for aid in all_ids if aid not in already_done]
        log.info(f"Pending: {len(pending)} of {len(all_ids)} total")

        if args.limit:
            pending = pending[: args.limit]
            log.info(f"Limiting to {args.limit}")

        for i, aid in enumerate(pending, 1):
            log.info(f"=== [{i}/{len(pending)}] {aid} ===")
            try:
                ok = process_applicant(driver, aid, output_dir, dry_run=args.dry_run)
                if not args.dry_run:
                    if ok:
                        state["downloaded"].append(aid)
                        state["failed"].pop(aid, None)
                        downloaded_ids.append(aid)
                    else:
                        state["failed"][aid] = "partial failure"
                        failed_ids.append(aid)
                    save_state(state, output_dir)
                else:
                    downloaded_ids.append(aid)
            except Exception as e:
                log.error(f"[{aid}] Unexpected error: {e}", exc_info=True)
                state["failed"][aid] = str(e)
                failed_ids.append(aid)
                save_state(state, output_dir)

    except SystemExit:
        raise
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        driver.quit()
        sys.exit(1)
    finally:
        driver.quit()

    # Structured JSON output to stdout
    summary = {
        "status": "success" if not failed_ids else "partial",
        "downloaded": downloaded_ids,
        "failed": failed_ids,
        "total_processed": len(downloaded_ids) + len(failed_ids),
        "output_dir": str(output_dir),
    }
    print(json.dumps(summary, indent=2))

    if failed_ids:
        sys.exit(3)


if __name__ == "__main__":
    main()
