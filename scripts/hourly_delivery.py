#!/usr/bin/env python3
"""One GitHub Issues acknowledgement for every completed scheduled hourly FX review.

This is a research-delivery audit, not a trading bot or ChatGPT notification.
A cron job may be delayed/skipped; an issue means the checks ACTUALLY ran.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ASSESSMENT = Path("data/hourly_assessment.json")
NEWS = Path("data/news_latest.json")
HOURLY_CRON = "43 6-18 * * 1-5"
CRITICAL_PLAN_STATES = {
    "REASSESS_OR_EXPIRE_PRIOR_PLAN_LEVEL_REACHED",
    "PAIR_M15_CANDLES_UNAVAILABLE",
    "INVALID_SETUP",
    "EXPIRED_OR_FUTURE",
    "MANUAL_EVENT_BLACKOUT",
    "NEWS_COVERAGE_NOT_VERIFIED",
}
EXISTING_ALERT_STATES = {
    "REASSESS_NEWS_FIRST",
    "PRICE_REASSESSMENT_ALERT_SENT",
}


def parse_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def should_send(event_name, cron):
    if event_name == "workflow_dispatch":
        return True  # Explicit manual validation.
    return event_name == "schedule" and cron.strip() == HOURLY_CRON


def evaluate(report, news, steps, current):
    """Classify only verified completed checks; never confuse zero ideas with clear trades."""
    problems = [step for step, outcome in steps.items() if outcome != "success"]
    if problems:
        return ("Update failed", "Workflow step(s) failed or were skipped: " + ", ".join(problems),
                "FAILURE")
    if report.get("status") != "OK":
        return ("Update failed", "Hourly setup review did not report OK.", "FAILURE")
    for field in ("oanda_collected_at", "news_collected_at", "collected_at"):
        try:
            moment = parse_date(report[field])
            if moment > current + timedelta(minutes=2) or current - moment > timedelta(minutes=20):
                return ("Update failed", "Research source timestamp stale/invalid: " + field,
                        "FAILURE")
        except (KeyError, TypeError, ValueError):
            return ("Update failed", "Missing or invalid source timestamp: " + field, "FAILURE")
    counts = report.get("counts", {})
    number = counts.get("watchlist")
    if not isinstance(number, int) or not (0 <= number <= 3):
        return ("Update failed", "Missing or invalid monitored setup count.", "FAILURE")

    lines = report.get("signals", [])
    issue_alerts = [s for s in lines if s.get("status") in EXISTING_ALERT_STATES]
    if issue_alerts:
        return ("Action needed",
                "An actual qualifying price/news alert was sent for: " +
                ", ".join(s.get("pair", "?") for s in issue_alerts),
                "ALREADY_NOTIFIED")
    invalidated = [s for s in lines if s.get("status") in CRITICAL_PLAN_STATES]
    if invalidated:
        return ("Action needed",
                "Review suspended/invalidated FX ideas: " +
                "; ".join(s.get("pair", "?") + " (" + s.get("status", "?") + ")"
                          for s in invalidated),
                "REVIEW")
    if number == 0:
        message = "OANDA and hourly news/data processes completed. Zero approved FX setups are currently monitored."
    else:
        message = (f"OANDA and hourly news/data processes completed. "
                   f"{number} approved FX setup(s) checked; no new qualifying change.")
    if report.get("news_coverage") != "OK":
        message += (" News coverage was LIMITED or PARTIAL; this is not an all-clear on "
                    "unexpected market developments.")
    if news.get("calendar", {}).get("status") != "OK":
        message += " Economic-calendar coverage was unavailable."
    return "Update conducted", message, "HEARTBEAT"


def create_issue(title, body, repository, token):
    if not token or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise RuntimeError("Cannot post GitHub Issue: invalid credentials or repository")
    owner = repository.split("/")[0]
    data = json.dumps({"title": title, "body": body, "assignees": [owner]}).encode("utf-8")
    req = Request("https://api.github.com/repos/" + repository + "/issues",
                  method="POST", data=data,
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json",
                           "User-Agent": "FX-ReadOnly-Research"})
    with urlopen(req, timeout=20) as response:
        result = json.load(response)
    return result["html_url"]


def run():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    cron = os.environ.get("GITHUB_SCHEDULE", "")
    if not should_send(event, cron):
        print("Hourly acknowledgement skipped: event", event, "schedule", cron or "(none)")
        return
    current = datetime.now(timezone.utc)
    try:
        report = json.loads(ASSESSMENT.read_text())
    except (OSError, ValueError):
        report = {}
    try:
        news = json.loads(NEWS.read_text())
    except (OSError, ValueError):
        news = {}
    steps = {
        "OANDA": os.environ.get("FX_COLLECT_OUTCOME", "missing"),
        "news": os.environ.get("FX_NEWS_OUTCOME", "missing"),
        "setups": os.environ.get("FX_WATCH_OUTCOME", "missing"),
        "snapshot save": os.environ.get("FX_PERSIST_OUTCOME", "missing"),
    }
    title, summary, kind = evaluate(report, news, steps, current)
    if kind == "ALREADY_NOTIFIED":
        print("Meaningful signal already generated its own GitHub Issue:", summary)
        return
    from zoneinfo import ZoneInfo
    local = current.astimezone(ZoneInfo("Europe/London"))
    reference = local.strftime("%Y-%m-%d %H:%M %Z")
    title = "[FX HOURLY] " + title + " — " + reference
    body = "\n".join((
        "**" + title.split("] ", 1)[-1].split(" — ")[0] + "**",
        "",
        summary,
        "",
        f"Actual checked at: {reference} ({current.isoformat(timespec='seconds')})",
        "OANDA snapshot: " + str(report.get("oanda_collected_at", "UNAVAILABLE")),
        "News snapshot: " + str(report.get("news_collected_at", "UNAVAILABLE")),
        "News source status: " + str(report.get("news_coverage", "UNAVAILABLE")),
        "Approved ideas monitored: " + str(report.get("counts", {}).get("watchlist", "UNKNOWN")),
        "",
        "This message confirms a *completed check*, not a valid Pepperstone entry or a full ChatGPT analysis.",
        "Any eligible trade still requires up-to-date broker executable quotes, macro evaluation and human approval.",
        "Actual report: data/hourly_assessment.json in this private GitHub repository.",
        "GitHub Actions run: https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "") +
            "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", ""),
        "",
        "GitHub Issues notification delivery depends on your GitHub settings; no trades were placed.",
    ))
    link = create_issue(title, body, os.environ.get("GITHUB_REPOSITORY", ""),
                        os.environ.get("GITHUB_TOKEN", ""))
    print(kind, "issue created:", link)


if __name__ == "__main__":
    try:
        run()
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print("Hourly acknowledgement failed:", type(exc).__name__, str(exc)[:120],
              file=sys.stderr)
        sys.exit(1)
