import importlib.util
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "groupme_to_ics.py"
SPEC = importlib.util.spec_from_file_location("groupme_to_ics", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class GroupMeToICSTests(unittest.TestCase):
    def test_extract_events_nested_payload(self):
        payload = {"response": {"events": [{"id": "1"}]}}
        result = MODULE.extract_events(payload)
        self.assertEqual(result, [{"id": "1"}])

    def test_normalize_event_defaults_end_and_timezone(self):
        raw = {
            "id": "abc",
            "name": "Practice",
            "start_at": "2026-02-14T10:00:00",
            "updated_at": "2026-02-14T11:00:00Z",
        }
        event = MODULE.normalize_event(raw, "UTC")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_id, "abc")
        self.assertEqual(event.tzid, "UTC")
        self.assertEqual(event.end - event.start, MODULE.timedelta(hours=1))

    def test_dedupe_and_sort_last_wins(self):
        raw_1 = {
            "id": "dup",
            "name": "Old",
            "start_at": "2026-02-14T09:00:00Z",
            "updated_at": "2026-02-14T09:00:00Z",
        }
        raw_2 = {
            "id": "dup",
            "name": "New",
            "start_at": "2026-02-14T09:00:00Z",
            "updated_at": "2026-02-14T09:05:00Z",
        }
        event_1 = MODULE.normalize_event(raw_1, "UTC")
        event_2 = MODULE.normalize_event(raw_2, "UTC")
        assert event_1 and event_2
        deduped = MODULE.dedupe_and_sort([event_1, event_2])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].title, "New")

    def test_dedupe_and_sort_tombstone_suppresses_older_active_event(self):
        active_raw = {
            "id": "dup",
            "name": "Still Here",
            "start_at": "2026-02-14T09:00:00Z",
            "updated_at": "2026-02-14T09:00:00Z",
        }
        deleted_raw = {
            "id": "dup",
            "name": "Still Here",
            "start_at": "2026-02-14T09:00:00Z",
            "updated_at": "2026-02-14T09:05:00Z",
            "deleted": True,
        }
        active_event = MODULE.normalize_event(active_raw, "UTC")
        deleted_event = MODULE.normalize_event(deleted_raw, "UTC")
        assert active_event and deleted_event
        deduped = MODULE.dedupe_and_sort([active_event, deleted_event])
        self.assertEqual(deduped, [])

    def test_is_deleted_event_with_boolean_flag(self):
        self.assertTrue(MODULE.is_deleted_event({"id": "1", "deleted": True}))

    def test_normalize_event_marks_deleted_event(self):
        raw = {
            "id": "abc",
            "name": "Practice",
            "start_at": "2026-02-14T10:00:00Z",
            "deleted": True,
        }
        event = MODULE.normalize_event(raw, "UTC")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertTrue(event.deleted)

    def test_is_deleted_event_with_status_value(self):
        self.assertTrue(MODULE.is_deleted_event({"id": "1", "status": "cancelled"}))

    def test_is_deleted_event_with_deleted_timestamp(self):
        self.assertTrue(MODULE.is_deleted_event({"id": "1", "deleted_at": "2026-02-14T09:00:00Z"}))

    def test_is_deleted_event_false_for_active_event(self):
        self.assertFalse(MODULE.is_deleted_event({"id": "1", "name": "Active"}))

    def test_build_ics_contains_required_blocks(self):
        raw = {
            "id": "evt1",
            "name": "Game Night",
            "description": "Bring snacks",
            "location": "Main Gym",
            "start_at": "2026-02-14T18:00:00Z",
            "end_at": "2026-02-14T19:00:00Z",
            "updated_at": "2026-02-14T17:00:00Z",
        }
        event = MODULE.normalize_event(raw, "UTC")
        assert event is not None
        output = MODULE.build_ics([event], "group123")
        self.assertIn("BEGIN:VCALENDAR", output)
        self.assertIn("BEGIN:VEVENT", output)
        self.assertIn("UID:groupme-group123-evt1@scll-calendar", output)
        self.assertIn("SUMMARY:Game Night", output)

    def test_write_if_changed_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "calendar.ics"
            first = MODULE.write_if_changed("abc", output)
            second = MODULE.write_if_changed("abc", output)
            third = MODULE.write_if_changed("def", output)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(third)
            self.assertEqual(output.read_text(encoding="utf-8"), "def")

    def test_parse_epoch_timestamp(self):
        dt = MODULE.parse_timestamp(1739520000, "UTC")
        assert dt is not None
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_describe_changes_added_deleted_updated(self):
        previous = {
            "groupme-g-1@scll-calendar": MODULE.CalendarEntry(
                uid="groupme-g-1@scll-calendar",
                title="Old Event",
                description="",
                location="",
                start=MODULE.parse_timestamp("2026-02-14T10:00:00Z", "UTC"),
                end=MODULE.parse_timestamp("2026-02-14T11:00:00Z", "UTC"),
                tzid="UTC",
            ),
            "groupme-g-2@scll-calendar": MODULE.CalendarEntry(
                uid="groupme-g-2@scll-calendar",
                title="Stay Event",
                description="old",
                location="",
                start=MODULE.parse_timestamp("2026-02-14T12:00:00Z", "UTC"),
                end=MODULE.parse_timestamp("2026-02-14T13:00:00Z", "UTC"),
                tzid="UTC",
            ),
        }
        current = {
            "groupme-g-2@scll-calendar": MODULE.CalendarEntry(
                uid="groupme-g-2@scll-calendar",
                title="Stay Event",
                description="new",
                location="",
                start=MODULE.parse_timestamp("2026-02-14T12:00:00Z", "UTC"),
                end=MODULE.parse_timestamp("2026-02-14T13:00:00Z", "UTC"),
                tzid="UTC",
            ),
            "groupme-g-3@scll-calendar": MODULE.CalendarEntry(
                uid="groupme-g-3@scll-calendar",
                title="Added Event",
                description="",
                location="",
                start=MODULE.parse_timestamp("2026-02-14T14:00:00Z", "UTC"),
                end=MODULE.parse_timestamp("2026-02-14T15:00:00Z", "UTC"),
                tzid="UTC",
            ),
        }
        changes = MODULE.describe_changes(previous, current)
        self.assertTrue(any(line.startswith('Added "Added Event"') for line in changes))
        self.assertTrue(any(line.startswith('Deleted "Old Event"') for line in changes))
        self.assertTrue(any(line.startswith('Updated "Stay Event"') for line in changes))

    def test_parse_ics_entries_round_trip(self):
        raw = {
            "id": "evt1",
            "name": "Game Night",
            "description": "Bring snacks",
            "location": "Main Gym",
            "start_at": "2026-02-14T18:00:00Z",
            "end_at": "2026-02-14T19:00:00Z",
            "updated_at": "2026-02-14T17:00:00Z",
        }
        event = MODULE.normalize_event(raw, "UTC")
        assert event is not None
        ics = MODULE.build_ics([event], "group123")
        parsed = MODULE.parse_ics_entries(ics)
        uid = "groupme-group123-evt1@scll-calendar"
        self.assertIn(uid, parsed)
        self.assertEqual(parsed[uid].title, "Game Night")

    def test_changelog_changes_only_when_calendar_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "calendar.ics"
            changelog = Path(tmp) / "calendar.changelog.txt"

            cal1 = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
            cal2 = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART;TZID=UTC:20260214T100000\nDTEND;TZID=UTC:20260214T110000\nSUMMARY:Event\nEND:VEVENT\nEND:VCALENDAR\n"

            first = MODULE.write_if_changed(cal1, output)
            self.assertTrue(first)
            if first:
                MODULE.write_if_changed("initial\n", changelog)

            second = MODULE.write_if_changed(cal1, output)
            self.assertFalse(second)

            third = MODULE.write_if_changed(cal2, output)
            self.assertTrue(third)
            if third:
                MODULE.write_if_changed("changed\n", changelog)

            self.assertEqual(changelog.read_text(encoding="utf-8"), "changed\n")


if __name__ == "__main__":
    unittest.main()
