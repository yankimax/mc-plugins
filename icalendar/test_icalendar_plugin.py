import pathlib
import sys
import unittest
from datetime import datetime, timezone

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'icalendar', 'icalendar_plugin')
ICalendarPlugin = _MODULE.ICalendarPlugin
MANAGED_EXPORT_UID_PREFIX = _MODULE.MANAGED_EXPORT_UID_PREFIX
CalendarSource = _MODULE.CalendarSource
ExpandedIcalEvent = _MODULE.ExpandedIcalEvent


def _utc_ms(text: str) -> int:
    return int(datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp() * 1000)


class ICalendarPluginLogicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = ICalendarPlugin()
        self.plugin._max_instances_per_event = 200

    def test_parse_calendar_expands_daily_rrule_and_exdate(self) -> None:
        content = (
            'BEGIN:VCALENDAR\r\n'
            'X-WR-CALNAME:Demo Calendar\r\n'
            'BEGIN:VEVENT\r\n'
            'UID:demo-1\r\n'
            'DTSTART:20260310T100000Z\r\n'
            'RRULE:FREQ=DAILY;COUNT=4\r\n'
            'EXDATE:20260312T100000Z\r\n'
            'SUMMARY:Daily standup\r\n'
            'DESCRIPTION:Sync call\r\n'
            'END:VEVENT\r\n'
            'END:VCALENDAR\r\n'
        )
        window_start = _utc_ms('2026-03-09 00:00')
        window_end = _utc_ms('2026-03-20 00:00')

        calendar_name, events = self.plugin._parse_calendar_content(content, window_start, window_end)
        starts = [event.start_at_ms for event in events]

        self.assertEqual(calendar_name, 'Demo Calendar')
        self.assertEqual(starts, [_utc_ms('2026-03-10 10:00'), _utc_ms('2026-03-11 10:00'), _utc_ms('2026-03-13 10:00')])
        self.assertTrue(all(event.uid == 'demo-1' for event in events))

    def test_sync_plan_does_not_delete_items_of_failed_calendar(self) -> None:
        calendar_ok = 'aaa111bbb222'
        calendar_failed = 'ccc333ddd444'

        existing_ok_uid = self.plugin._build_external_uid(calendar_ok, 'uid-1', 'single')
        existing_failed_uid = self.plugin._build_external_uid(calendar_failed, 'uid-2', 'single')

        existing = [
            {
                'id': 1,
                'externalUid': existing_ok_uid,
                'title': 'ok',
                'description': '',
                'startAtMs': _utc_ms('2026-03-10 10:00'),
                'dueAtMs': None,
                'payload': {'fingerprint': 'a'},
            },
            {
                'id': 2,
                'externalUid': existing_failed_uid,
                'title': 'failed',
                'description': '',
                'startAtMs': _utc_ms('2026-03-10 11:00'),
                'dueAtMs': None,
                'payload': {'fingerprint': 'b'},
            },
        ]

        upserts, deletes, unchanged = self.plugin._build_sync_plan(
            existing_items=existing,
            desired={},
            synced_calendar_ids={calendar_ok},
            configured_calendar_ids={calendar_ok, calendar_failed},
        )

        self.assertEqual(upserts, [])
        self.assertEqual(unchanged, 0)
        self.assertEqual([item.get('id') for item in deletes], [1])

    def test_is_item_up_to_date_uses_fingerprint(self) -> None:
        desired = {
            'title': 'Demo',
            'description': 'Desc',
            'startAtMs': _utc_ms('2026-03-10 10:00'),
            'dueAtMs': _utc_ms('2026-03-10 11:00'),
            'payload': {'fingerprint': 'fp-1'},
        }
        item = {
            'title': 'Demo',
            'description': 'Desc',
            'startAtMs': _utc_ms('2026-03-10 10:00'),
            'dueAtMs': _utc_ms('2026-03-10 11:00'),
            'payload': {'fingerprint': 'fp-1'},
        }

        self.assertTrue(self.plugin._is_item_up_to_date(item, desired))
        item['payload'] = {'fingerprint': 'fp-2'}
        self.assertFalse(self.plugin._is_item_up_to_date(item, desired))

    def test_merge_calendar_replaces_only_managed_export_events(self) -> None:
        existing = (
            'BEGIN:VCALENDAR\r\n'
            'VERSION:2.0\r\n'
            'BEGIN:VEVENT\r\n'
            f'UID:{MANAGED_EXPORT_UID_PREFIX}55@minachan\r\n'
            'DTSTART:20260310T100000Z\r\n'
            'SUMMARY:Old managed\r\n'
            'END:VEVENT\r\n'
            'BEGIN:VEVENT\r\n'
            'UID:external-keep\r\n'
            'DTSTART:20260311T120000Z\r\n'
            'SUMMARY:Keep me\r\n'
            'END:VEVENT\r\n'
            'END:VCALENDAR\r\n'
        )
        blocks = self.plugin._build_managed_export_blocks(
            [
                {
                    'id': 77,
                    'title': 'New managed',
                    'description': '',
                    'startAtMs': _utc_ms('2026-03-12 13:00'),
                    'dueAtMs': None,
                }
            ]
        )
        merged = self.plugin._merge_calendar_with_managed_events(existing, blocks)

        self.assertIn('UID:external-keep', merged)
        self.assertIn(f'UID:{MANAGED_EXPORT_UID_PREFIX}77@minachan', merged)
        self.assertNotIn(f'UID:{MANAGED_EXPORT_UID_PREFIX}55@minachan', merged)

    def test_build_desired_item_maps_todoist_checked_title_to_done(self) -> None:
        source = CalendarSource(
            raw='https://example.test/todoist.ics',
            normalized='https://example.test/todoist.ics',
            calendar_id='abc123',
            local_path=None,
        )
        event = ExpandedIcalEvent(
            uid='uid-1',
            recurrence_key='single',
            recurrence_id_ms=None,
            title='✓ Done task',
            description='',
            location='',
            categories=[],
            status='NEEDS-ACTION',
            start_at_ms=_utc_ms('2026-03-10 10:00'),
            due_at_ms=_utc_ms('2026-03-10 11:00'),
            all_day=False,
        )

        desired = self.plugin._build_desired_item(source, 'Todoist', event)
        self.assertEqual(desired.get('state'), 'done')

    def test_build_desired_item_does_not_map_todoist_without_checkmark(self) -> None:
        source = CalendarSource(
            raw='https://example.test/todoist.ics',
            normalized='https://example.test/todoist.ics',
            calendar_id='ghi789',
            local_path=None,
        )
        event = ExpandedIcalEvent(
            uid='uid-3',
            recurrence_key='single',
            recurrence_id_ms=None,
            title='Regular task',
            description='',
            location='',
            categories=[],
            status='CONFIRMED',
            start_at_ms=_utc_ms('2026-03-10 10:00'),
            due_at_ms=_utc_ms('2026-03-10 11:00'),
            all_day=False,
        )

        desired = self.plugin._build_desired_item(source, 'Todoist', event)
        self.assertNotIn('state', desired)

    def test_build_desired_item_keeps_state_empty_for_non_todoist(self) -> None:
        source = CalendarSource(
            raw='https://example.test/calendar.ics',
            normalized='https://example.test/calendar.ics',
            calendar_id='def456',
            local_path=None,
        )
        event = ExpandedIcalEvent(
            uid='uid-2',
            recurrence_key='single',
            recurrence_id_ms=None,
            title='Regular event',
            description='',
            location='',
            categories=[],
            status='CONFIRMED',
            start_at_ms=_utc_ms('2026-03-10 10:00'),
            due_at_ms=_utc_ms('2026-03-10 11:00'),
            all_day=False,
        )

        desired = self.plugin._build_desired_item(source, 'Demo Calendar', event)
        self.assertNotIn('state', desired)

    def test_is_item_up_to_date_checks_mapped_state(self) -> None:
        desired = {
            'title': 'Demo',
            'description': '',
            'startAtMs': _utc_ms('2026-03-10 10:00'),
            'dueAtMs': _utc_ms('2026-03-10 11:00'),
            'state': 'done',
            'payload': {'fingerprint': 'fp-1'},
        }
        item = {
            'title': 'Demo',
            'description': '',
            'startAtMs': _utc_ms('2026-03-10 10:00'),
            'dueAtMs': _utc_ms('2026-03-10 11:00'),
            'state': 'planned',
            'payload': {'fingerprint': 'fp-1'},
        }

        self.assertFalse(self.plugin._is_item_up_to_date(item, desired))


class ICalendarPluginStartupTest(unittest.TestCase):
    def test_on_init_starts_sync_without_loading_complete_barrier(self) -> None:
        plugin = ICalendarPlugin()
        scheduled = []
        sync_calls = []

        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_speech_rule = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin._load_settings = lambda: None  # type: ignore[method-assign]
        plugin._register_settings_gui = lambda: None  # type: ignore[method-assign]
        plugin._schedule_next_sync = lambda: scheduled.append(True)  # type: ignore[method-assign]

        def _start_sync(trigger: str, speak: bool):
            sync_calls.append((trigger, speak))
            return (True, '')

        plugin._start_sync = _start_sync  # type: ignore[method-assign]

        plugin.on_init()

        self.assertEqual(scheduled, [True])
        self.assertEqual(sync_calls, [('startup', False)])


if __name__ == '__main__':
    unittest.main()
