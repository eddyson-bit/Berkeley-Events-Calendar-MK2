scrape_uc_events.py

#!/usr/bin/env python3
"""
uct_scraper_to_gcal.py

- Scrapes upcoming shows from https://www.theuctheatre.org/events
- Upserts events into a single Google Calendar (via service account)
- Idempotent: matches existing events by title + start datetime and updates instead of duplicating
"""

import os
import json
import hashlib
from datetime import datetime, timedelta
import logging
import pytz
import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build

# CONFIG
TIMEZONE = "America/Los_Angeles"
CALENDAR_ID = os.environ.get("CALENDAR_ID")  # required: set in env (or GitHub secret)
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")  # full JSON string

# settings
USER_AGENT = "uct-scraper/1.0 (+https://your-repo-or-email)"
EVENT_LOOKAHEAD_DAYS = 365  # how far ahead to insert events (adjust as needed)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_credentials():
    if not SERVICE_ACCOUNT_JSON:
        raise RuntimeError("SERVICE_ACCOUNT_JSON env var not set")
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return creds


def fetch_events_page(url="https://www.theuctheatre.org/events"):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def parse_event_blocks(html):
    """
    Parses the event list from the UC Theatre events page.

    Approach:
      - Find occurrences of "Doors:" in the page — those indicate event blocks
      - For each such occurrence, find the ancestor element that contains the block
      - From that block extract:
          * Title
          * Date (month/day or full)
          * Day-of-week (ignored)
          * Doors/show times if available (we prefer 'Show' time)
          * Ticket link (if available)
      - Return list of dicts: {title, start_dt (aware), end_dt (optional), url, description}
    """
    soup = BeautifulSoup(html, "html.parser")
    tz = pytz.timezone(TIMEZONE)
    events = []

    # find strings "Doors:" — these appear inside each event card (observed on the site)
    for doors_text in soup.find_all(string=lambda s: isinstance(s, str) and "Doors:" in s):
        block = doors_text
        # climb up to an ancestor event container (we'll go up several levels)
        parent = block.parent
        for _ in range(6):
            if parent is None:
                break
            parent = parent.parent

        # fallback: use the original holder of the "Doors:" text
        if parent is None:
            parent = block.parent

        # to be resilient, look at the nearest ancestor that contains the date and title nearby
        container = block.find_parent()
        if container is None:
            continue

        # try to find the show time (the text "Show:" nearby)
        show_time_tag = container.find(string=lambda s: isinstance(s, str) and "Show:" in s)
        doors_time_tag = container.find(string=lambda s: isinstance(s, str) and "Doors:" in s)
        # find title: usually appears as a big text sibling (we look for the nearest previous tag with meaningful text)
        title = None
        ticket_url = None

        # Search upwards / sideways for a title-like tag (text with reasonable length)
        search_space = container.find_all_previous(limit=10)
        for node in search_space:
            txt = node.get_text(strip=True) if hasattr(node, "get_text") else str(node).strip()
            if txt and len(txt) > 2 and txt.isprintable():
                # heuristic: skip small tokens like "Doors:" or month labels
                if "Doors:" in txt or "Show:" in txt or txt.upper() in ["DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                                                        "JUL", "AUG", "SEP", "OCT", "NOV"]:
                    continue
                # pick the first fairly long text that is not generic
                if len(txt) > 5 and len(txt.splitlines()) <= 2:
                    title = txt
                    break

        # sometimes the title is below, so try sibling search
        if not title:
            for sib in container.find_next_siblings(limit=6):
                txt = sib.get_text(strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                if txt and len(txt) > 5:
                    title = txt
                    break

        # robust fallback: look for h1/h2/h3 tags near container
        if not title:
            for h in container.find_all(["h1", "h2", "h3", "h4"]):
                t = h.get_text(strip=True)
                if t and len(t) > 4:
                    title = t
                    break

        # find the date info: look up parent / previous siblings for month/day tokens
        # The site prints lines like "# Dec" then "# 02" in separate tags; we'll search backwards for a month token and a day number
        MONTHS = {m.upper(): i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
        month = None
        day = None
        year = None

        # search previous ~20 nodes text for month and day
        prev_texts = []
        for prev in container.find_all_previous(limit=30):
            txt = prev.get_text(strip=True)
            if txt:
                prev_texts.append(txt)
        # collapse into a single string for regex-like search
        big = " | ".join(prev_texts)

        # try to find month tokens like "Dec", "Jan"
        import re
        m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", big, re.IGNORECASE)
        if m:
            month = m.group(1).title()
        # find day number (1-31) preceding or following month token
        d = re.search(r"\b([0-3]?\d)\b", big)
        if d:
            day = int(d.group(1))

        # try to find explicit year (4-digit)
        y = re.search(r"\b(20[2-9]\d)\b", big)
        if y:
            year = int(y.group(1))
        else:
            # no year on page -> assume current year or next if month already passed
            now = datetime.now(tz)
            year = now.year
            if month:
                mon_num = MONTHS.get(month.title(), now.month)
                if mon_num < now.month - 1:
                    # show probably in next year
                    year = now.year + 1

        # parse show time (prefer "Show:" time, else doors)
        def parse_time_from_text(s):
            if not s:
                return None
            # look for patterns like "8:00 pm", "7:00 pm"
            tmatch = re.search(r"([0-1]?\d(?::[0-5]\d)?\s*(?:am|pm))", s, re.IGNORECASE)
            if tmatch:
                return tmatch.group(1)
            return None

        show_time_str = None
        if show_time_tag:
            show_time_str = parse_time_from_text(str(show_time_tag))
        if not show_time_str and doors_time_tag:
            show_time_str = parse_time_from_text(str(doors_time_tag))

        if not title or not month or not day or not show_time_str:
            # if we can't parse minimal bits, skip — log for debugging
            logger.debug("Skipping block due to missing data: title=%s month=%s day=%s time=%s",
                         title, month, day, show_time_str)
            continue

        # build datetime
        month_num = MONTHS[month.title()]
        dt_text = f"{year}-{month_num:02d}-{int(day):02d} {show_time_str}"
        try:
            # normalize times like "8:00 pm"
            dt_parsed = datetime.strptime(dt_text, "%Y-%m-%d %I:%M %p")
        except ValueError:
            # try without minutes
            dt_parsed = datetime.strptime(dt_text, "%Y-%m-%d %I %p")
        dt_local = tz.localize(dt_parsed)

        # event object
        event = {
            "title": title.strip(),
            "start": dt_local,
            "end": dt_local + timedelta(hours=2),  # default duration 2h (adjust if you want)
            "description": "",  # optionally fill from nearby text or ticket link
            "ticket_url": ticket_url,
        }

        # compute a stable hash id (private key) to help idempotency
        key = f"{event['title']}|{event['start'].isoformat()}"
        event["private_id"] = hashlib.sha1(key.encode("utf-8")).hexdigest()

        events.append(event)

    # de-duplicate by private_id
    unique = {}
    for e in events:
        unique[e["private_id"]] = e
    logger.info("Parsed %d unique events", len(unique))
    return list(unique.values())


def build_gcal_service(creds):
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return service


def find_existing_event(service, calendar_id, title, start_dt):
    """Search events in a small window around start_dt and try to match by exact title + start time."""
    # set window +/- 1 hour to pick up events with minor variations
    time_min = (start_dt - timedelta(hours=2)).astimezone(pytz.utc).isoformat()
    time_max = (start_dt + timedelta(hours=2)).astimezone(pytz.utc).isoformat()
    events_result = service.events().list(
        calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy="startTime", maxResults=10
    ).execute()
    items = events_result.get("items", [])
    for it in items:
        # compare summary and start time
        it_summary = it.get("summary", "")
        it_start = it.get("start", {}).get("dateTime") or it.get("start", {}).get("date")
        if not it_start:
            continue
        try:
            it_start_dt = datetime.fromisoformat(it_start.replace("Z", "+00:00"))
        except Exception:
            continue
        # convert both to tz-aware in local timezone for comparison
        it_start_loc = it_start_dt.astimezone(pytz.timezone(TIMEZONE))
        # compare title and exact hour/minute
        if it_summary.strip().lower() == title.strip().lower() and abs((it_start_loc - start_dt).total_seconds()) < 60:
            return it
    return None


def upsert_event(service, calendar_id, event_obj):
    start = event_obj["start"]
    end = event_obj["end"]
    title = event_obj["title"]
    description = event_obj.get("description", "")
    ticket_url = event_obj.get("ticket_url")
    if ticket_url:
        description = (description + "\n\nTicket: " + ticket_url).strip()

    # find existing event
    existing = find_existing_event(service, calendar_id, title, start)
    event_body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.astimezone(pytz.utc).isoformat()},
        "end": {"dateTime": end.astimezone(pytz.utc).isoformat()},
        # put our private id in extendedProperties so it's available for humans / debugging (not required/used for search)
        "extendedProperties": {"private": {"uct_private_id": event_obj["private_id"]}},
        "source": {"title": "UC Theatre", "url": "https://www.theuctheatre.org/events"},
        # location left empty; add if you want
    }
    if existing:
        logger.info("Updating existing event: %s at %s", title, start.isoformat())
        updated = service.events().patch(calendarId=calendar_id, eventId=existing["id"], body=event_body).execute()
        return updated
    else:
        logger.info("Inserting new event: %s at %s", title, start.isoformat())
        inserted = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        return inserted


def main():
    if not CALENDAR_ID:
        raise RuntimeError("CALENDAR_ID not set in environment")

    creds = load_credentials()
    gcal = build_gcal_service(creds)

    html = fetch_events_page()
    parsed_events = parse_event_blocks(html)

    # limit to lookahead
    now = datetime.now(pytz.timezone(TIMEZONE))
    cutoff = now + timedelta(days=EVENT_LOOKAHEAD_DAYS)
    to_upsert = [e for e in parsed_events if now - timedelta(days=1) <= e["start"] <= cutoff]

    logger.info("Will upsert %d events to calendar %s", len(to_upsert), CALENDAR_ID)

    for ev in to_upsert:
        try:
            upsert_event(gcal, CALENDAR_ID, ev)
        except Exception as ex:
            logger.exception("Failed to upsert event %s: %s", ev.get("title"), ex)


if __name__ == "__main__":
    main()
