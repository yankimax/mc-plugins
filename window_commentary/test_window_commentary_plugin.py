import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('window_commentary_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for window_commentary plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
WindowCommentaryPlugin = _MODULE.WindowCommentaryPlugin


class WindowCommentaryPluginContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = WindowCommentaryPlugin()

    def test_request_window_titles_uses_system_runtime_bridge(self) -> None:
        calls = []
        collected = []

        def fake_send(tag, data=None, on_response=None, on_complete=None):
            calls.append((tag, data))
            if on_response is not None:
                on_response(
                    'system_runtime',
                    {
                        'ok': True,
                        'providerAvailable': True,
                        'titles': ['Code', 'Code', 'Browser'],
                    },
                    tag,
                )
            if on_complete is not None:
                on_complete('system_runtime', {'ok': True}, tag)
            return 1

        self.plugin.send_message_with_response = fake_send  # type: ignore[method-assign]

        self.plugin._request_window_titles(lambda titles: collected.append(list(titles)))

        self.assertEqual(calls, [('system-runtime:get-window-titles', {})])
        self.assertEqual(collected, [['Code', 'Browser']])
        self.assertTrue(self.plugin._window_provider_available)

    def test_parse_window_titles_response_marks_provider_unavailable_on_error(self) -> None:
        titles, provider_available = self.plugin._parse_window_titles_response(
            {
                'ok': False,
                'providerAvailable': False,
                'reason': 'adapter unavailable',
                'titles': [],
            },
        )

        self.assertEqual(titles, [])
        self.assertFalse(provider_available)


class WindowCommentaryPluginStartupTest(unittest.TestCase):
    def test_on_init_schedules_first_check_after_startup_grace_period(self) -> None:
        plugin = WindowCommentaryPlugin()
        timer_calls = []

        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin._load_settings = lambda: None  # type: ignore[method-assign]
        plugin._register_settings_gui = lambda: None  # type: ignore[method-assign]
        plugin.set_timer = lambda delay_ms, count, listener_id: timer_calls.append(  # type: ignore[method-assign]
            (delay_ms, count, listener_id),
        ) or 1

        plugin.on_init()

        self.assertEqual(
            timer_calls,
            [(_MODULE.STARTUP_GRACE_SEC * 1000, 1, 'window_commentary_tick')],
        )

    def test_on_tick_reschedules_regular_interval(self) -> None:
        plugin = WindowCommentaryPlugin()
        checks = []
        timer_calls = []

        plugin._check_and_comment = lambda force=False: checks.append(force)  # type: ignore[method-assign]
        plugin.set_timer = lambda delay_ms, count, listener_id: timer_calls.append(  # type: ignore[method-assign]
            (delay_ms, count, listener_id),
        ) or 1
        plugin._interval_sec = 180
        plugin._timer_armed = True

        plugin.on_tick('system', None, 'window-commentary:tick')

        self.assertEqual(checks, [False])
        self.assertEqual(timer_calls, [(180000, 1, 'window_commentary_tick')])


if __name__ == '__main__':
    unittest.main()
