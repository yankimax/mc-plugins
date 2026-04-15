import importlib.machinery
import importlib.util
import pathlib
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'os_interaction' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('os_interaction_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for os_interaction plugin')
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
OsInteractionPlugin = _MODULE.OsInteractionPlugin


class OsInteractionPluginContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = OsInteractionPlugin()

    def test_close_process_strips_exe_when_operation_appends_exe(self) -> None:
        captured = {}

        def fake_execute(operation, *, values, wait):
            captured['operation'] = operation
            captured['values'] = dict(values)
            captured['wait'] = wait
            return True

        self.plugin._capabilities = {'system.closeProcessByName': True}
        self.plugin._operations = {
            'closeProcessByName': {
                'strategy': 'command',
                'command': 'taskkill',
                'args': ['/IM', '{name}.exe', '/F'],
            },
        }
        self.plugin._execute_operation = fake_execute  # type: ignore[method-assign]

        ok, reason = self.plugin._close_process('notepad.exe')

        self.assertTrue(ok)
        self.assertEqual(reason, '')
        self.assertEqual(captured['values']['name'], 'notepad')
        self.assertTrue(captured['wait'])

    def test_open_url_uses_open_path_operation_adapter(self) -> None:
        captured = {}

        def fake_execute(operation, *, values, wait):
            captured['operation'] = operation
            captured['values'] = dict(values)
            captured['wait'] = wait
            return True

        operation = {
            'strategy': 'command',
            'command': 'xdg-open',
            'args': ['{path}'],
        }
        self.plugin._capabilities = {'system.openUrl': True}
        self.plugin._operations = {'openPath': operation}
        self.plugin._execute_operation = fake_execute  # type: ignore[method-assign]

        url = 'https://example.com/docs'
        ok, reason = self.plugin._open_url(url)

        self.assertTrue(ok)
        self.assertEqual(reason, '')
        self.assertEqual(captured['operation'], operation)
        self.assertEqual(captured['values']['path'], url)
        self.assertFalse(captured['wait'])

    def test_open_url_returns_graceful_reason_without_operation(self) -> None:
        self.plugin._capabilities = {'system.openUrl': True}
        self.plugin._operations = {}

        ok, reason = self.plugin._open_url('https://example.com')

        self.assertFalse(ok)
        self.assertIn('openPath operation is missing', reason)

    def test_run_command_uses_spawn_detached_operation(self) -> None:
        captured = {}

        def fake_execute(operation, *, values, wait):
            captured['operation'] = operation
            captured['values'] = dict(values)
            captured['wait'] = wait
            return True

        operation = {'strategy': 'spawnDetached'}
        self.plugin._capabilities = {'process.spawnDetached': True}
        self.plugin._operations = {'spawnDetached': operation}
        self.plugin._execute_operation = fake_execute  # type: ignore[method-assign]

        ok, reason = self.plugin._run_command('echo hello')

        self.assertTrue(ok)
        self.assertEqual(reason, '')
        self.assertEqual(captured['operation'], operation)
        self.assertEqual(captured['values']['argv'], ['echo', 'hello'])
        self.assertFalse(captured['wait'])

    def test_run_command_returns_graceful_reason_without_operation(self) -> None:
        self.plugin._capabilities = {'process.spawnDetached': True}
        self.plugin._operations = {}

        ok, reason = self.plugin._run_command('echo hello')

        self.assertFalse(ok)
        self.assertIn('spawnDetached operation is missing', reason)

    def test_spawn_in_terminal_replies_with_argv(self) -> None:
        replies = []
        self.plugin.reply = lambda sender, data=None: replies.append((sender, data))  # type: ignore[method-assign]
        self.plugin._launch_terminal_command = lambda argv: (True, '')  # type: ignore[method-assign]

        self.plugin.on_spawn_in_terminal('tester', {'argv': ['ssh', 'demo']}, 'system:spawn-in-terminal')

        self.assertEqual(
            replies,
            [('tester', {'ok': True, 'argv': ['ssh', 'demo']})],
        )

    def test_launch_terminal_command_uses_run_in_terminal_operation(self) -> None:
        captured = {}

        def fake_execute(operation, *, values, wait):
            captured['operation'] = operation
            captured['values'] = dict(values)
            captured['wait'] = wait
            return True

        operation = {
            'strategy': 'command',
            'command': 'cmd.exe',
            'args': ['/c', 'start', '', 'cmd.exe', '/k', '{commandLine}'],
        }
        self.plugin._system_snapshot = {'platform': 'windows'}
        self.plugin._capabilities = {'system.runInTerminal': True}
        self.plugin._operations = {'runInTerminal': operation}
        self.plugin._execute_operation = fake_execute  # type: ignore[method-assign]

        ok, reason = self.plugin._launch_terminal_command(['ssh', 'demo'])

        self.assertTrue(ok)
        self.assertEqual(reason, '')
        self.assertEqual(captured['operation'], operation)
        self.assertEqual(captured['values']['argv'], ['ssh', 'demo'])
        self.assertIn('ssh', captured['values']['commandLine'])
        self.assertIn('demo', captured['values']['commandLine'])
        self.assertFalse(captured['wait'])

    def test_open_in_editor_uses_preferred_editor(self) -> None:
        launched = []
        self.plugin._preferred_editor = lambda: ['code', '--wait']  # type: ignore[method-assign]
        self.plugin._spawn_detached = lambda argv: launched.append(list(argv)) or True  # type: ignore[method-assign]

        ok, reason = self.plugin._open_in_editor('/tmp/demo.txt')

        self.assertTrue(ok)
        self.assertEqual(reason, '')
        self.assertEqual(launched, [['code', '--wait', '/tmp/demo.txt']])

    def test_open_in_editor_falls_back_to_open_path_adapter(self) -> None:
        self.plugin._preferred_editor = lambda: []  # type: ignore[method-assign]
        self.plugin._open_path = lambda path: (True, '') if path == '/tmp/demo.txt' else (False, 'bad path')  # type: ignore[method-assign]

        ok, reason = self.plugin._open_in_editor('/tmp/demo.txt')

        self.assertTrue(ok)
        self.assertEqual(reason, '')

    def test_on_init_requests_system_snapshot_without_loading_complete_barrier(self) -> None:
        calls = []
        self.plugin.send_message = lambda tag, data=None: calls.append((tag, data))  # type: ignore[method-assign]
        self.plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.set_event_link = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin._load_open_aliases = lambda: {}  # type: ignore[method-assign]

        self.plugin.on_init()

        self.assertIn(('system-runtime:get-snapshot', None), calls)


if __name__ == '__main__':
    unittest.main()
