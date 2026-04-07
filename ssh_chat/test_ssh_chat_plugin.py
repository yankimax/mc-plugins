import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('ssh_chat_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for ssh_chat plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
ParseResult = _MODULE.ParseResult
SshChatPlugin = _MODULE.SshChatPlugin


class SshChatPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = SshChatPlugin()
        self.spoken = []
        self.replies = []
        self.sent = []
        self.plugin.request_say_direct = lambda text, **kwargs: self.spoken.append(text)  # type: ignore[method-assign]
        self.plugin.reply = lambda sender, data=None: self.replies.append((sender, data))  # type: ignore[method-assign]

    def test_parse_host_only(self) -> None:
        parsed = self.plugin._parse_connect_text('ssh 10.0.0.5')

        self.assertIsInstance(parsed, ParseResult)
        assert parsed.intent is not None
        self.assertEqual(parsed.intent.mode, 'connect')
        self.assertEqual(parsed.intent.host, '10.0.0.5')
        self.assertIsNone(parsed.intent.port)

    def test_parse_host_and_positional_port(self) -> None:
        parsed = self.plugin._parse_connect_text('ssh 10.0.0.5 2222')

        assert parsed.intent is not None
        self.assertEqual(parsed.intent.host, '10.0.0.5')
        self.assertEqual(parsed.intent.port, 2222)

    def test_parse_host_and_flag_port(self) -> None:
        parsed = self.plugin._parse_connect_text('ssh 10.0.0.5 -P 2201')

        assert parsed.intent is not None
        self.assertEqual(parsed.intent.host, '10.0.0.5')
        self.assertEqual(parsed.intent.port, 2201)

    def test_parse_config_shortcut(self) -> None:
        parsed = self.plugin._parse_connect_text('ssh config')

        assert parsed.intent is not None
        self.assertEqual(parsed.intent.mode, 'config')

    def test_connect_command_launches_terminal(self) -> None:
        def fake_send(command, payload, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
            self.sent.append((command, payload))
            if on_response is not None:
                on_response('system:spawn-in-terminal', {'ok': True}, command)
            if on_complete is not None:
                on_complete('system:spawn-in-terminal', None, command)
            return 1

        self.plugin.send_message_with_response = fake_send  # type: ignore[method-assign]

        self.plugin.on_connect('tester', {'request': 'ssh prod-box 2200'}, SshChatPlugin.CMD_CONNECT)

        self.assertEqual(
            self.sent,
            [('system:spawn-in-terminal', {'argv': ['ssh', '-p', '2200', 'prod-box']})],
        )
        self.assertTrue(any('prod-box' in item for item in self.spoken))
        self.assertEqual(self.replies[-1][0], 'tester')
        self.assertEqual(self.replies[-1][1]['ok'], True)

    def test_ssh_config_opens_editor(self) -> None:
        fake_path = pathlib.Path('/tmp/fake-ssh-config')
        self.plugin._ensure_ssh_config_file = lambda: fake_path  # type: ignore[method-assign]
        def fake_send(command, payload, on_response=None, on_complete=None):  # type: ignore[no-untyped-def]
            self.sent.append((command, payload))
            if on_response is not None:
                on_response('system:open-in-editor', {'ok': True}, command)
            if on_complete is not None:
                on_complete('system:open-in-editor', None, command)
            return 1

        self.plugin.send_message_with_response = fake_send  # type: ignore[method-assign]

        self.plugin.on_open_config('tester', None, SshChatPlugin.CMD_OPEN_CONFIG)

        self.assertEqual(
            self.sent,
            [('system:open-in-editor', {'path': str(fake_path)})],
        )
        self.assertTrue(any('SSH config' in item for item in self.spoken))
        self.assertEqual(self.replies[-1][1]['ok'], True)


if __name__ == '__main__':
    unittest.main()
