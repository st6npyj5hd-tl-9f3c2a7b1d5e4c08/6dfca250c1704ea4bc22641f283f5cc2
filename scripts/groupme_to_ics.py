#!/usr/bin/env python3
"""Sync GroupMe events into an ICS file."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = "https://api.groupme.com"
EVENTS_PATH_TEMPLATE = "/conversations/{group_id}/events/list"


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: str
    title: str
    description: str
    location: str
    start: datetime
    end: datetime
    tzid: str
    updated_at: datetime
    url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync GroupMe events to an ICS file")
    parser.add_argument("--output", help="ICS output path")
    parser.add_argument("--dry-run", action="store_true", help="Validate and generate without writing output")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"Missing required environment variable: {name}")


def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def build_candidate_urls(base_url: str, group_id: str) -> list[str]:
    base = base_url.rstrip("/")
    path = EVENTS_PATH_TEMPLATE.format(group_id=group_id)
    if base.endswith("/v3"):
        return [f"{base}{path}"]
    return [f"{base}/v3{path}", f"{base}{path}"]


def fetch_raw_events(session: requests.Session, base_url: str, group_id: str, token: str) -> list[dict[str, Any]]:
    headers = {"X-Access-Token": token, "Accept": "application/json"}
    # OpenGM docs note both fields as required on the calendar endpoint.
    params = {
        "token": token,
        "limit": int(os.getenv("EVENTS_LIMIT", "200")),
        # Old floor timestamp to include all available history from this API.
        "end_at": os.getenv("EVENTS_END_AT", "1970-01-01T00:00:00Z"),
    }
    errors: list[str] = []
    for url in build_candidate_urls(base_url, group_id):
        response = session.get(url, headers=headers, params=params, timeout=30)
        if response.status_code in (401, 403):
            raise RuntimeError(f"GroupMe auth failed with status {response.status_code}. Check GROUPME_TOKEN permissions.")
        if response.status_code >= 400:
            body_snippet = response.text[:240].replace("\n", " ")
            errors.append(f"{url} -> HTTP {response.status_code}: {body_snippet}")
            continue

        payload = response.json()
        events = extract_events(payload)
        if not isinstance(events, list):
            errors.append(f"{url} -> response parsed but events list missing")
            continue

        normalized: list[dict[str, Any]] = []
        for item in events:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    joined = "; ".join(errors) if errors else "no response details captured"
    raise RuntimeError(f"Unable to fetch GroupMe events from candidate endpoints: {joined}")


def extract_events(payload: Any) -> Any:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("events", "response", "data"):
        if key in payload:
            value = payload[key]
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested in ("events", "items", "results"):
                    if isinstance(value.get(nested), list):
                        return value[nested]

    for key in ("items", "results"):
        if isinstance(payload.get(key), list):
            return payload[key]

    return []


def parse_timestamp(value: Any, default_tz: str) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None

        if candidate.isdigit():
            return datetime.fromtimestamp(int(candidate), tz=timezone.utc)

        iso_candidate = candidate.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso_candidate)
        except ValueError:
            return None

        if dt.tzinfo is None:
            return localize_naive(dt, default_tz)
        return dt

    return None


def localize_naive(dt: datetime, tzid: str) -> datetime:
    zone = resolve_zone(tzid)
    return dt.replace(tzinfo=zone)


def resolve_zone(tzid: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(tzid)
    except Exception:
        return timezone.utc


def coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def normalize_event(raw: dict[str, Any], default_tz: str) -> NormalizedEvent | None:
    event_id = str(coalesce(raw.get("id"), raw.get("event_id"), raw.get("eventId"), "")).strip()
    title = str(coalesce(raw.get("name"), raw.get("title"), raw.get("subject"), "Untitled event")).strip()
    description = str(coalesce(raw.get("description"), raw.get("details"), "")).strip()

    location_value = raw.get("location")
    if isinstance(location_value, dict):
        location = str(coalesce(location_value.get("name"), location_value.get("address"), "")).strip()
    else:
        location = str(coalesce(location_value, raw.get("venue"), "")).strip()

    tzid = str(coalesce(raw.get("timezone"), raw.get("tz"), default_tz)).strip() or default_tz
    target_zone = resolve_zone(tzid)

    start_raw = coalesce(raw.get("start_at"), raw.get("start_time"), raw.get("starts_at"), raw.get("start"))
    end_raw = coalesce(raw.get("end_at"), raw.get("end_time"), raw.get("ends_at"), raw.get("end"))
    updated_raw = coalesce(raw.get("updated_at"), raw.get("updated"), raw.get("modified_at"), raw.get("created_at"))
    url = str(coalesce(raw.get("url"), raw.get("permalink"), "")).strip()

    start = parse_timestamp(start_raw, tzid)
    if start is None:
        logging.warning("Skipping event without a parseable start time: %s", json.dumps(raw, default=str))
        return None
    start = start.astimezone(target_zone)

    end = parse_timestamp(end_raw, tzid)
    if end is None:
        # GroupMe payloads may omit end time; default to 1 hour duration.
        end = start + timedelta(hours=1)
    else:
        end = end.astimezone(target_zone)

    if end <= start:
        end = start + timedelta(hours=1)

    updated_at = parse_timestamp(updated_raw, tzid) or datetime.now(tz=timezone.utc)

    if not event_id:
        derived = f"{title}-{int(start.timestamp())}"
        event_id = derived.replace(" ", "-").lower()

    return NormalizedEvent(
        event_id=event_id,
        title=title,
        description=description,
        location=location,
        start=start,
        end=end,
        tzid=tzid,
        updated_at=updated_at,
        url=url,
    )


def fold_ics_line(line: str, limit: int = 75) -> str:
    if len(line) <= limit:
        return line
    parts = [line[:limit]]
    remaining = line[limit:]
    while remaining:
        parts.append(" " + remaining[: limit - 1])
        remaining = remaining[limit - 1 :]
    return "\r\n".join(parts)


def escape_ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def format_dtstamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def format_local_datetime(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def build_ics(events: list[NormalizedEvent], group_id: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//scll//groupme-calendar-sync//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:GroupMe Events",
    ]

    for event in events:
        uid = f"groupme-{group_id}-{event.event_id}@scll-calendar"
        vevent_lines = [
            "BEGIN:VEVENT",
            f"UID:{escape_ics_text(uid)}",
            f"DTSTAMP:{format_dtstamp(event.updated_at)}",
            f"DTSTART;TZID={escape_ics_text(event.tzid)}:{format_local_datetime(event.start)}",
            f"DTEND;TZID={escape_ics_text(event.tzid)}:{format_local_datetime(event.end)}",
            f"SUMMARY:{escape_ics_text(event.title)}",
        ]

        if event.description:
            vevent_lines.append(f"DESCRIPTION:{escape_ics_text(event.description)}")
        if event.location:
            vevent_lines.append(f"LOCATION:{escape_ics_text(event.location)}")
        if event.url:
            vevent_lines.append(f"URL:{escape_ics_text(event.url)}")

        vevent_lines.append("END:VEVENT")
        lines.extend(vevent_lines)

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def write_if_changed(content: str, output_path: Path) -> bool:
    if output_path.exists() and output_path.read_text(encoding="utf-8") == content:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, delete=False) as tmp_file:
        tmp_file.write(content)
        tmp_name = tmp_file.name

    Path(tmp_name).replace(output_path)
    return True


def dedupe_and_sort(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    deduped: dict[str, NormalizedEvent] = {}
    for event in events:
        deduped[event.event_id] = event
    return sorted(deduped.values(), key=lambda e: (e.start, e.event_id))


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        group_id = require_env("GROUP_ID")
        token = require_env("GROUPME_TOKEN")
    except ValueError as error:
        logging.error(str(error))
        return 2

    output_path = Path(args.output or os.getenv("ICS_OUTPUT_PATH", "calendar.ics"))
    default_tz = os.getenv("DEFAULT_TZ", "UTC")
    base_url = os.getenv("GROUPME_BASE_URL", DEFAULT_BASE_URL)

    session = make_session()
    try:
        raw_events = fetch_raw_events(session, base_url, group_id, token)
    except requests.RequestException as error:
        logging.error("HTTP error while fetching GroupMe events: %s", error)
        return 1
    except RuntimeError as error:
        logging.error("%s", error)
        return 1

    normalized: list[NormalizedEvent] = []
    for raw_event in raw_events:
        event = normalize_event(raw_event, default_tz)
        if event is not None:
            normalized.append(event)

    ordered = dedupe_and_sort(normalized)
    ics_content = build_ics(ordered, group_id)

    if args.dry_run:
        logging.info("Dry-run complete: %d events parsed, %d events emitted", len(normalized), len(ordered))
        return 0

    changed = write_if_changed(ics_content, output_path)
    if changed:
        logging.info("Wrote %s with %d events", output_path, len(ordered))
    else:
        logging.info("No changes for %s (%d events)", output_path, len(ordered))
    return 0


if __name__ == "__main__":
    sys.exit(main())
