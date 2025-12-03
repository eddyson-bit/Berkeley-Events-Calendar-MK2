#!/usr/bin/env python3
"""
uct_scraper_to_ics.py

- Scrapes upcoming shows from https://www.theuctheatre.org/events
- Generates a standard iCal (.ics) file
- Can be run automatically via GitHub Actions
"""

import os
import hashlib
from datetime import datetime, timedelta
import pytz
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

# CONFIG
TIMEZONE = "America/Los_Angeles"
OUTPUT_ICS = "uct_events.ics"
EVENT_LOOKAHEAD_DAYS = 365
USER_AGENT = "uct-scraper/1.0 (+https://github.com/yourrepo)"

# Set timezone
tz = pytz.timezone(TIMEZONE)

def fetch_events_page(url="https://www.theuctheatre.org/events"):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def parse_events(html):
    """
    Parses the event list.
    Returns list of dicts with: title, start, end
    """
    soup = BeautifulSoup(html, "html.parser")
    events = []
    MONTHS = {m.upper(): i for i, m in enumerate(["Jan","Feb","Mar","Apr","May","Jun",
                                                  "Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
    for block in soup.find_all(string=lambda s: "Doors:" in s):
        # Find title
        title = block.find_parent().get_text(strip=True).split("Doors:")[0]
        if not title:
            continue

        # Find month/day/year and show time
        parent_text = block.find_parent().get_text(" ", strip=True)
        import re
        month_m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", parent_text)
        day_m = re.search(r"\b([0-3]?\d)\b", parent_text)
        show_time_m = re.search(r"([0-1]?\d(?::[0-5]\d)?\s*(?:am|pm))", parent_text, re.IGNORECASE)

        if not (month_m and day_m and show_time_m):
            continue

        month = MONTHS[month_m.group(1).title()]
        day = int(day_m.group(1))
        show_time = show_time_m.group(1)

        now = datetime.now(tz)
        year = now.year
        if month < now.month - 1:
            year += 1

        dt_str = f"{year}-{month:02d}-{day:02d} {show_time}"
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
        except ValueError:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %I %p")
        dt = tz.localize(dt)

        events.append({
            "title": title.strip(),
            "start": dt,
            "end": dt + timedelta(hours=2)
        })
    return events

def generate_ics(events, filename=OUTPUT_ICS):
    cal = Calendar()
    cal.add("prodid", "-//UC Theatre Events//github.com//")
    cal.add("version", "2.0")

    for ev in events:
        e = Event()
        e.add("summary", ev["title"])
        e.add("dtstart", ev["start"])
        e.add("dtend", ev["end"])
        e.add("dtstamp", datetime.now(tz))
        # optional: unique ID based on title+start
        uid = hashlib.sha1(f"{ev['title']}{ev['start']}".encode()).hexdigest()
        e.add("uid", uid + "@uctscraper")
        cal.add_component(e)

    with open(filename, "wb") as f:
        f.write(cal.to_ical())
    print(f"Generated {filename} with {len(events)} events")

def main():
    html = fetch_events_page()
    events = parse_events(html)
    # filter lookahead
    now = datetime.now(tz)
    cutoff = now + timedelta(days=EVENT_LOOKAHEAD_DAYS)
    events = [e for e in events if now - timedelta(days=1) <= e["start"] <= cutoff]
    generate_ics(events)

if __name__ == "__main__":
    main()
