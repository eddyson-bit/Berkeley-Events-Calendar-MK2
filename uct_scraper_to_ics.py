#!/usr/bin/env python3
"""
uct_scraper_to_ics.py

- Scrapes upcoming shows from https://www.theuctheatre.org/events
- Generates a detailed iCal (.ics) file
- Includes title, doors/show times, description, and ticket links
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

tz = pytz.timezone(TIMEZONE)

def fetch_events_page(url="https://www.theuctheatre.org/events"):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def parse_events(html):
    """
    Parses events from the UC Theatre website.
    Returns a list of dicts:
    {
        title: str,
        start: datetime,
        end: datetime,
        description: str
    }
    """
    soup = BeautifulSoup(html, "html.parser")
    events = []

    for event_block in soup.select("div.event-card"):  # adjust selector if needed
        try:
            title_tag = event_block.select_one("h3")
            title = title_tag.get_text(strip=True) if title_tag else "Untitled Event"

            date_tag = event_block.select_one(".event-date")
            date_text = date_tag.get_text(strip=True) if date_tag else ""

            time_tag = event_block.select_one(".event-time")
            time_text = time_tag.get_text(strip=True) if time_tag else ""

            link_tag = event_block.select_one("a.event-link")
            event_url = link_tag['href'] if link_tag and link_tag.has_attr('href') else ""

            doors_text = event_block.select_one(".doors").get_text(strip=True) if event_block.select_one(".doors") else ""

            # Parse date/time
            # Example: "Fri, Dec 6 7:30 PM"
            dt_str = f"{date_text} {time_text}"
            try:
                dt_start = datetime.strptime(dt_str, "%a, %b %d %I:%M %p")
                dt_start = tz.localize(dt_start)
            except Exception:
                # fallback: use today + 19:00 if parsing fails
                dt_start = tz.localize(datetime.now().replace(hour=19, minute=0, second=0, microsecond=0))

            dt_end = dt_start + timedelta(hours=2)

            description = f"{title}\nDoors: {doors_text}\nURL: {event_url}"

            events.append({
                "title": title,
                "start": dt_start,
                "end": dt_end,
                "description": description
            })
        except Exception as e:
            print(f"Failed to parse an event: {e}")

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
        e.add("description", ev["description"])
        uid = hashlib.sha1(f"{ev['title']}{ev['start']}".encode()).hexdigest()
        e.add("uid", uid + "@uctscraper")
        cal.add_component(e)

    with open(filename, "wb") as f:
        f.write(cal.to_ical())
    print(f"Generated {filename} with {len(events)} events")

def main():
    html = fetch_events_page()
    events = parse_events(html)
    now = datetime.now(tz)
    cutoff = now + timedelta(days=EVENT_LOOKAHEAD_DAYS)
    events = [e for e in events if now - timedelta(days=1) <= e["start"] <= cutoff]
    generate_ics(events)

if __name__ == "__main__":
    main()
