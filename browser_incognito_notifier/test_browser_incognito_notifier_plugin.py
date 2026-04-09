import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'browser_incognito_notifier' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('browser_incognito_notifier_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for browser_incognito_notifier plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
BrowserIncognitoNotifierPlugin = _MODULE.BrowserIncognitoNotifierPlugin


class BrowserIncognitoNotifierPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = BrowserIncognitoNotifierPlugin()
        self.spoken = []
        self.timer_callbacks = {}
        self.cancelled_timers = []
        self.plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.set_timer_once = self._fake_set_timer_once  # type: ignore[method-assign]
        self.plugin.cancel_timer = lambda timer_id: self.cancelled_timers.append(timer_id)  # type: ignore[method-assign]
        self.plugin.request_say_intent = (  # type: ignore[method-assign]
            lambda intent, template_vars=None, emotion=None, extra=None: self.spoken.append(
                (intent, template_vars or {}, extra or {})
            )
        )
        self.plugin.send_message_with_response = self._fake_send_message_with_response  # type: ignore[method-assign]
        self.browser_response = {'ok': True, 'tab': {'id': 0, 'incognito': False}}

    def _fake_set_timer_once(self, delay_ms, callback):  # type: ignore[no-untyped-def]
        timer_id = len(self.timer_callbacks) + 1
        self.timer_callbacks[timer_id] = (delay_ms, callback)
        return timer_id

    def _fake_send_message_with_response(self, tag, data=None, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
        if on_response is not None:
            on_response('browser-extension', self.browser_response, tag)
        if on_complete is not None:
            on_complete('browser-extension', None, tag)
        return 1

    def test_on_init_registers_runtime_listeners_only(self) -> None:
        self.plugin.on_init()

        self.assertEqual(self.spoken, [])

    def test_first_snapshot_creates_baseline_without_phrase(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 10, 'incognito': True},
                    {'id': 11, 'incognito': False},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(self.spoken, [])
        self.assertTrue(self.plugin._snapshot_ready)
        self.assertEqual(self.plugin._known_incognito_tab_ids, {10})

    def test_new_incognito_tab_triggers_phrase_once(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 1, 'incognito': False},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 1, 'incognito': False},
                    {'id': 2, 'incognito': True},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 1, 'incognito': False},
                    {'id': 2, 'incognito': True},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(
            self.spoken,
            [
                (
                    'BROWSER_INCOGNITO_TAB_OPENED',
                    {'count': 1},
                    {'count': 1},
                ),
            ],
        )

    def test_connected_event_resets_baseline(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 10, 'incognito': True},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_browser_connected(
            'browser-extension',
            {'ok': True},
            _MODULE.EVENT_BROWSER_CONNECTED,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 10, 'incognito': True},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(self.spoken, [])
        self.assertTrue(self.plugin._snapshot_ready)
        self.assertEqual(self.plugin._known_incognito_tab_ids, {10})

    def test_closed_incognito_tab_triggers_close_phrase_once(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 2, 'incognito': True},
                    {'id': 3, 'incognito': False},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 3, 'incognito': False},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 3, 'incognito': False},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(
            self.spoken,
            [
                (
                    'BROWSER_INCOGNITO_TAB_CLOSED',
                    {'count': 1},
                    {'count': 1},
                ),
            ],
        )

    def test_active_incognito_snapshot_starts_long_active_timer(self) -> None:
        self.plugin.on_active_tab_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tab': {
                    'id': 77,
                    'incognito': True,
                },
            },
            _MODULE.EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT,
        )

        self.assertEqual(self.plugin._active_incognito_tab_id, 77)
        self.assertEqual(self.plugin._active_incognito_timer_id, 1)
        self.assertFalse(self.plugin._active_incognito_notified)
        self.assertEqual(self.timer_callbacks[1][0], _MODULE.LONG_ACTIVE_DELAY_MS)

    def test_switching_away_from_active_incognito_cancels_long_active_timer(self) -> None:
        self.plugin.on_active_tab_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tab': {
                    'id': 77,
                    'incognito': True,
                },
            },
            _MODULE.EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT,
        )

        self.plugin.on_active_tab_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tab': {
                    'id': 78,
                    'incognito': False,
                },
            },
            _MODULE.EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT,
        )

        self.assertEqual(self.cancelled_timers, [1])
        self.assertIsNone(self.plugin._active_incognito_tab_id)
        self.assertFalse(self.plugin._active_incognito_notified)

    def test_long_active_timer_confirms_active_incognito_before_phrase(self) -> None:
        self.plugin.on_active_tab_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tab': {
                    'id': 77,
                    'incognito': True,
                },
            },
            _MODULE.EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT,
        )
        self.browser_response = {'ok': True, 'tab': {'id': 77, 'incognito': True}}

        _, callback = self.timer_callbacks[1]
        callback('timer', None, '')

        self.assertEqual(
            self.spoken,
            [
                (
                    'BROWSER_INCOGNITO_TAB_LONG_ACTIVE',
                    {'count': 1},
                    {'count': 1},
                ),
            ],
        )
        self.assertTrue(self.plugin._active_incognito_notified)

    def test_long_active_timer_skips_phrase_when_active_tab_changed(self) -> None:
        self.plugin.on_active_tab_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tab': {
                    'id': 77,
                    'incognito': True,
                },
            },
            _MODULE.EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT,
        )
        self.browser_response = {'ok': True, 'tab': {'id': 78, 'incognito': True}}

        _, callback = self.timer_callbacks[1]
        callback('timer', None, '')

        self.assertEqual(self.spoken, [])
        self.assertFalse(self.plugin._active_incognito_notified)

    def test_query_pagination_growth_over_threshold_triggers_phrase_once(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 40, 'incognito': True, 'url': 'https://example.com/search?q=x&page=2'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 40, 'incognito': True, 'url': 'https://example.com/search?q=x&page=13'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 40, 'incognito': True, 'url': 'https://example.com/search?q=x&page=19'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(
            self.spoken,
            [
                (
                    'BROWSER_INCOGNITO_TAB_PAGINATION_SPIRAL',
                    {
                        'count': 1,
                        'pageStart': 2,
                        'pageCurrent': 13,
                        'pageDelta': 11,
                        'pageKey': 'page',
                    },
                    {
                        'count': 1,
                        'pageStart': 2,
                        'pageCurrent': 13,
                        'pageDelta': 11,
                        'pageKey': 'page',
                    },
                ),
            ],
        )

    def test_path_pagination_growth_triggers_phrase(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 41, 'incognito': True, 'url': 'https://example.com/gallery/page/3'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 41, 'incognito': True, 'url': 'https://example.com/gallery/page/15'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(self.spoken[0][0], 'BROWSER_INCOGNITO_TAB_PAGINATION_SPIRAL')
        self.assertEqual(self.spoken[0][1]['pageKey'], 'path:page')
        self.assertEqual(self.spoken[0][1]['pageDelta'], 12)

    def test_pagination_tracker_resets_when_scope_changes(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 42, 'incognito': True, 'url': 'https://example.com/search?page=5'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 42, 'incognito': True, 'url': 'https://example.com/other?page=20'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertEqual(self.spoken, [])

    def test_pagination_tracker_clears_when_incognito_tab_closes(self) -> None:
        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [
                    {'id': 43, 'incognito': True, 'url': 'https://example.com/search?page=1'},
                ],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.plugin.on_tabs_snapshot(
            'browser-extension',
            {
                'ok': True,
                'tabs': [],
            },
            _MODULE.EVENT_BROWSER_TABS_SNAPSHOT,
        )

        self.assertNotIn(43, self.plugin._pagination_by_tab_id)


if __name__ == '__main__':
    unittest.main()
