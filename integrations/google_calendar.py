"""Acceso fail-safe a Google Calendar para el briefing matutino.

OAuth read-only. El token se cachea fuera de git. Sin credenciales/token o sin
red -> devuelve []. La lógica de parseo se aísla en events_from_api_items para
poder testearla sin red.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass(frozen=True)
class CalEvent:
    start: str       # "HH:MM" o "" si all_day
    summary: str
    all_day: bool


def events_from_api_items(items: list[dict]) -> list[CalEvent]:
    out: list[CalEvent] = []
    for it in items:
        start = it.get("start", {}) or {}
        summary = (it.get("summary") or "(sin título)").strip()
        if "date" in start and "dateTime" not in start:
            out.append(CalEvent(start="", summary=summary, all_day=True))
        else:
            raw = start.get("dateTime", "")
            hhmm = ""
            try:
                hhmm = datetime.fromisoformat(raw).strftime("%H:%M")
            except ValueError:
                hhmm = ""
            out.append(CalEvent(start=hhmm, summary=summary, all_day=False))
    return out


def _load_credentials(credentials_path: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds
    if not credentials_path.exists():
        return None
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), _SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def today_events(*, credentials_path: Path, token_path: Path) -> list[CalEvent]:
    try:
        credentials_path = Path(credentials_path)
        token_path = Path(token_path)
        creds = _load_credentials(credentials_path, token_path)
        if creds is None:
            return []
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=creds,
                        cache_discovery=False)
        now = datetime.now()
        start = datetime.combine(now.date(), time.min).astimezone(timezone.utc)
        end = datetime.combine(now.date(), time.max).astimezone(timezone.utc)
        resp = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime",
        ).execute()
        return events_from_api_items(resp.get("items", []))
    except Exception:
        return []
