import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'browser_tab_watcher', 'browser_tab_watcher_plugin')
BrowserTabWatcherPlugin = _MODULE.BrowserTabWatcherPlugin


class BrowserTabWatcherPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = BrowserTabWatcherPlugin()
        self.timer_callbacks = {}
        self.cancelled_timers = []
        self.sent = []
        self.replies = []
        self.spoken = []
        self.saved_properties = {}
        self.browser_response = {'ok': False, 'error': 'not_set'}

        self.plugin.set_timer_once = self._fake_set_timer_once  # type: ignore[method-assign]
        self.plugin.cancel_timer = lambda timer_id: self.cancelled_timers.append(timer_id)  # type: ignore[method-assign]
        self.plugin.send_message = lambda tag, data=None: self.sent.append((tag, data))  # type: ignore[method-assign]
        self.plugin.reply = lambda sender, data=None: self.replies.append((sender, data))  # type: ignore[method-assign]
        self.plugin.request_say_direct = lambda text, **kwargs: self.spoken.append(text)  # type: ignore[method-assign]
        self.plugin.send_message_with_response = self._fake_send_message_with_response  # type: ignore[method-assign]
        self.plugin.set_property = lambda key, value: self.saved_properties.__setitem__(key, value)  # type: ignore[method-assign]
        self.plugin.save_properties = lambda: None  # type: ignore[method-assign]

    def _fake_set_timer_once(self, delay_ms, callback):  # type: ignore[no-untyped-def]
        timer_id = len(self.timer_callbacks) + 1
        self.timer_callbacks[timer_id] = (delay_ms, callback)
        return timer_id

    def _fake_send_message_with_response(self, tag, data=None, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
        self.sent.append((tag, data))
        if on_response is not None:
            on_response('browser-extension', self.browser_response, tag)
        if on_complete is not None:
            on_complete('browser-extension', None, tag)
        return 1

    def test_youtube_watch_tab_starts_interest_timer(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'atMs': 1700000000000,
                'tabs': [
                    {
                        'id': 10,
                        'url': 'https://www.youtube.com/watch?v=abc123XYZ',
                        'title': 'Video',
                    },
                    {
                        'id': 11,
                        'url': 'https://example.com/',
                    },
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(set(self.plugin._tracked_tabs.keys()), {10})
        self.assertEqual(self.timer_callbacks[1][0], _MODULE.DEFAULT_INTEREST_DELAY_MS)
        self.assertEqual(self.plugin._tracked_tabs[10].video_key, 'youtube:abc123XYZ')

    def test_closing_youtube_tab_cancels_timer(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {
                        'id': 10,
                        'url': 'https://www.youtube.com/watch?v=abc123XYZ',
                    },
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {'ok': True, 'tabs': []},
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(self.cancelled_timers, [1])
        self.assertEqual(self.plugin._tracked_tabs, {})

    def test_timer_collects_page_info_and_publishes_record(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'atMs': 1700000000000,
                'tabs': [
                    {
                        'id': 10,
                        'url': 'https://www.youtube.com/watch?v=abc123XYZ',
                    },
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )
        self.browser_response = {
            'ok': True,
            'atMs': 1700000180000,
            'url': 'https://www.youtube.com/watch?v=abc123XYZ',
            'tab': {'id': 10, 'windowId': 1, 'index': 0, 'active': True},
            'page': {'title': 'Fallback', 'description': 'Desc'},
            'youtube': {
                'ok': True,
                'videoId': 'abc123XYZ',
                'title': 'Good Video',
                'author': 'Channel',
                'durationSec': 600,
                'currentTimeSec': 187,
                'transcript': {
                    'ok': True,
                    'languageCode': 'ru',
                    'segments': [{'startMs': 0, 'durationMs': 1000, 'text': 'Привет'}],
                    'text': 'Привет',
                },
            },
        }

        self.timer_callbacks[1][1]()

        self.assertEqual(
            self.sent[0],
            (
                'browser-extension:get-tab-page-info',
                {
                    'tabId': 10,
                    'includeTranscript': True,
                    'transcriptMaxSegments': _MODULE.DEFAULT_TRANSCRIPT_SEGMENTS,
                    'transcriptMaxChars': _MODULE.DEFAULT_TRANSCRIPT_CHARS,
                },
            ),
        )
        interest_events = [
            item
            for item in self.sent
            if item[0] == _MODULE.EVENT_YOUTUBE_INTEREST
        ]
        self.assertEqual(len(interest_events), 1)
        record = interest_events[0][1]
        self.assertEqual(record['title'], 'Good Video')
        self.assertEqual(record['author'], 'Channel')
        self.assertEqual(record['currentTimeSec'], 187.0)
        self.assertEqual(record['openDurationMs'], 180000)
        self.assertEqual(self.saved_properties['youtubeRecords'][0]['videoKey'], 'youtube:abc123XYZ')

    def test_video_navigation_resets_tracking_timer(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 10, 'url': 'https://www.youtube.com/watch?v=abc123XYZ'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 10, 'url': 'https://www.youtube.com/watch?v=def456XYZ'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(self.cancelled_timers, [1])
        self.assertEqual(self.plugin._tracked_tabs[10].timer_id, 2)
        self.assertEqual(self.plugin._tracked_tabs[10].video_key, 'youtube:def456XYZ')

    def test_list_command_replies_with_collected_records(self) -> None:
        self.plugin._records = [
            {'videoKey': 'youtube:abc123XYZ', 'title': 'Good Video', 'author': 'Channel'},
        ]

        self.plugin.on_list_youtube_interests(
            'tester',
            {},
            _MODULE.CMD_LIST_YOUTUBE_INTERESTS,
        )

        self.assertEqual(self.replies[-1][0], 'tester')
        self.assertTrue(self.replies[-1][1]['ok'])
        self.assertEqual(self.replies[-1][1]['count'], 1)
        self.assertIn('Good Video', self.spoken[-1])


if __name__ == '__main__':
    unittest.main()
