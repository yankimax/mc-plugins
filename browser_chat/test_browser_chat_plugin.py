import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'browser_chat' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('browser_chat_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for browser_chat plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
BrowserChatPlugin = _MODULE.BrowserChatPlugin


class BrowserChatPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = BrowserChatPlugin()
        self.spoken = []
        self.replies = []
        self.sent = []
        self.plugin.request_say_direct = lambda text, **kwargs: self.spoken.append(text)  # type: ignore[method-assign]
        self.plugin.reply = lambda sender, data=None: self.replies.append((sender, data))  # type: ignore[method-assign]

    def test_normalize_open_target_supports_domain_and_search(self) -> None:
        self.assertEqual(
            self.plugin._normalize_open_target('example.com/docs'),
            'https://example.com/docs',
        )
        self.assertEqual(
            self.plugin._normalize_open_target('how to use spine in flutter'),
            'https://www.google.com/search?q=how+to+use+spine+in+flutter',
        )

    def test_find_best_tab_match_prefers_domain_and_title(self) -> None:
        tabs = [
            self.plugin._tab_from_map(
                {
                    'id': 10,
                    'windowId': 1,
                    'index': 0,
                    'title': 'YouTube - Lofi Mix',
                    'url': 'https://www.youtube.com/watch?v=abc',
                    'active': False,
                },
            ),
            self.plugin._tab_from_map(
                {
                    'id': 11,
                    'windowId': 1,
                    'index': 1,
                    'title': 'VK Feed',
                    'url': 'https://vk.com/feed',
                    'active': True,
                },
            ),
        ]
        tabs = [item for item in tabs if item is not None]

        match_domain = self.plugin._find_best_tab_match(tabs, 'youtube')
        match_title = self.plugin._find_best_tab_match(tabs, 'feed')
        match_index = self.plugin._find_best_tab_match(tabs, '2')

        self.assertIsNotNone(match_domain)
        self.assertEqual(match_domain.tab_id, 10)
        self.assertIsNotNone(match_title)
        self.assertEqual(match_title.tab_id, 11)
        self.assertIsNotNone(match_index)
        self.assertEqual(match_index.tab_id, 11)

    def test_open_tab_calls_browser_bridge(self) -> None:
        def fake_send(command, payload, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
            self.sent.append((command, payload))
            if on_response is not None:
                on_response(
                    command,
                    {
                        'ok': True,
                        'tab': {'id': 17, 'title': 'Example', 'url': payload.get('url')},
                    },
                    command,
                )
            if on_complete is not None:
                on_complete(command, None, command)
            return 1

        self.plugin.send_message_with_response = fake_send  # type: ignore[method-assign]

        self.plugin.on_open_tab(
            'tester',
            {'request': 'example.com'},
            BrowserChatPlugin.CMD_OPEN,
        )

        self.assertEqual(
            self.sent,
            [('browser-extension:open-tab', {'url': 'https://example.com'})],
        )
        self.assertTrue(any('example.com' in item for item in self.spoken))
        self.assertEqual(self.replies[-1][0], 'tester')
        self.assertTrue(self.replies[-1][1]['ok'])

    def test_activate_tab_resolves_query_then_calls_activate(self) -> None:
        def fake_send(command, payload, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
            self.sent.append((command, payload))
            if command == 'browser-extension:get-open-tabs' and on_response is not None:
                on_response(
                    command,
                    {
                        'ok': True,
                        'tabs': [
                            {
                                'id': 30,
                                'windowId': 1,
                                'index': 0,
                                'title': 'Docs - MinaChan',
                                'url': 'https://docs.minachan.local',
                                'active': False,
                            },
                            {
                                'id': 31,
                                'windowId': 1,
                                'index': 1,
                                'title': 'YouTube - Mix',
                                'url': 'https://youtube.com/watch?v=1',
                                'active': True,
                            },
                        ],
                    },
                    command,
                )
            if command == 'browser-extension:activate-tab' and on_response is not None:
                on_response(command, {'ok': True}, command)
            if on_complete is not None:
                on_complete(command, None, command)
            return 1

        self.plugin.send_message_with_response = fake_send  # type: ignore[method-assign]

        self.plugin.on_activate_tab(
            'tester',
            {'query': 'docs'},
            BrowserChatPlugin.CMD_ACTIVATE,
        )

        self.assertEqual(
            self.sent,
            [
                ('browser-extension:get-open-tabs', {}),
                ('browser-extension:activate-tab', {'tabId': 30}),
            ],
        )
        self.assertTrue(self.replies[-1][1]['ok'])
        self.assertEqual(self.replies[-1][1]['mode'], 'activate')

    def test_list_tabs_reports_preview(self) -> None:
        def fake_send(command, payload, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
            self.sent.append((command, payload))
            if on_response is not None:
                on_response(
                    command,
                    {
                        'ok': True,
                        'tabs': [
                            {
                                'id': 1,
                                'windowId': 1,
                                'index': 0,
                                'title': 'VK Feed',
                                'url': 'https://vk.com/feed',
                                'active': True,
                            },
                            {
                                'id': 2,
                                'windowId': 1,
                                'index': 1,
                                'title': 'YouTube',
                                'url': 'https://youtube.com',
                                'active': False,
                            },
                        ],
                    },
                    command,
                )
            if on_complete is not None:
                on_complete(command, None, command)
            return 1

        self.plugin.send_message_with_response = fake_send  # type: ignore[method-assign]

        self.plugin.on_list_tabs('tester', None, BrowserChatPlugin.CMD_LIST)

        self.assertEqual(self.sent, [('browser-extension:get-open-tabs', {})])
        self.assertTrue(self.spoken)
        self.assertIn('2', self.spoken[-1])
        self.assertTrue(self.replies[-1][1]['ok'])
        self.assertEqual(self.replies[-1][1]['count'], 2)


if __name__ == '__main__':
    unittest.main()
