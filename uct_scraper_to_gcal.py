import os
import json
from datetime import datetime, timedelta
import pytz
import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build

# CONFIG
TIMEZONE = "America/Los_Angeles"
CALENDAR_ID = "primary"  # or your shared calendar ID
EVENT_LOOKAHEAD_DAYS = 365
USER_AGENT = "uct-scraper/1.0 (+https://github.com/yourrepo)"
tz = pytz.timezone(TIMEZONE)

def fetch_events():
    html = requests.get("https://www.theuctheatre.org/events", headers={"User-Agent": USER_AGENT}).text
    soup = BeautifulSoup(html, "html.parser")
    events = []

    for event_block in soup.select("div.event-card"):
        try:
            title = event_block.select_one("h3").get_text(strip=True)
            date_text = event_block.select_one(".event-date").get_text(strip=True)
            times = [t.get_text(strip=True) for t in event_block.select(".event-time")]
            url = event_block.select_one("a.event-link")["href"] if event_block.select_one("a.event-link") else ""
            doors = event_block.select_one(".doors").get_text(strip=True) if event_block.select_one(".doors") else ""

            for time_text in times:
                dt_str = f"{date_text} {time_text}"
                try:
                    dt_start = datetime.strptime(dt_str, "%a, %b %d %I:%M %p")
                    dt_start = tz.localize(dt_start)
                except Exception:
                    dt_start = tz.localize(datetime.now().replace(hour=19, minute=0))
                dt_end = dt_start + timedelta(hours=2)

                description = f"Doors: {doors}\nURL: {url}"
                events.append({"title": title, "start": dt_start, "end": dt_end, "description": description})
        except Exception:
            continue
    return events

def push_to_gcal(events):
    # Load service account credentials from environment
    sa_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    service = build("calendar", "v3", credentials=credentials)

    # Optional: clear existing events in the next 365 days (to prevent duplicates)
    now = datetime.utcnow().isoformat() + "Z"
    future = (datetime.utcnow() + timedelta(days=EVENT_LOOKAHEAD_DAYS)).isoformat() + "Z"
    events_result = service.events().list(calendarId=CALENDAR_ID, timeMin=now, timeMax=future).execute()
    for ev in events_result.get("items", []):
        service.events().delete(calendarId=CALENDAR_ID, eventId=ev["id"]).execute()

    # Insert new events
    for ev in events:
        event_body = {
            "summary": ev["title"],
            "description": ev["description"],
            "start": {"dateTime": ev["start"].isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": ev["end"].isoformat(), "timeZone": TIMEZONE}
        }
        service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()

def main():
    events = fetch_events()
    push_to_gcal(events)

if __name__ == "__main__":
    main()
