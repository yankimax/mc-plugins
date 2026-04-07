import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('download_router_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for download_router plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
DownloadRouterPlugin = _MODULE.DownloadRouterPlugin


class DownloadRouterPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = DownloadRouterPlugin()
        self.spoken = []
        self.replies = []
        self.commands = []
        self.rules = []
        self.alternatives = []
        self.listeners = []
        self.plugin.request_say_direct = lambda text, **kwargs: self.spoken.append(text)  # type: ignore[method-assign]
        self.plugin.reply = lambda sender, data=None: self.replies.append((sender, data))  # type: ignore[method-assign]
        self.plugin.add_listener = lambda *args, **kwargs: self.listeners.append((args, kwargs))  # type: ignore[method-assign]
        self.plugin.register_command = lambda *args, **kwargs: self.commands.append((args, kwargs))  # type: ignore[method-assign]
        self.plugin.register_speech_rule = lambda *args, **kwargs: self.rules.append((args, kwargs))  # type: ignore[method-assign]
        self.plugin.set_alternative = lambda *args, **kwargs: self.alternatives.append((args, kwargs))  # type: ignore[method-assign]
        self.plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]

    def test_on_init_registers_generic_download_command_and_fallback(self) -> None:
        self.plugin.on_init()

        self.assertTrue(any(args[0] == _MODULE.CMD_DOWNLOAD_BY_LINK for args, _ in self.commands))
        self.assertTrue(any(args[0] == _MODULE.CMD_DOWNLOAD_BY_LINK for args, _ in self.rules))
        self.assertEqual(
            self.alternatives,
            [((_MODULE.CMD_DOWNLOAD_BY_LINK, _MODULE.CMD_DOWNLOAD_BY_LINK_FALLBACK, _MODULE.DOWNLOAD_FALLBACK_PRIORITY), {})],
        )

    def test_fallback_reports_missing_url(self) -> None:
        self.plugin.on_download_by_link_fallback('tester', {}, _MODULE.CMD_DOWNLOAD_BY_LINK_FALLBACK)

        self.assertEqual(
            self.spoken,
            ['Пришли прямую ссылку на страницу, которую нужно скачать.'],
        )
        self.assertEqual(self.replies[-1][1]['error'], 'missing_url')

    def test_fallback_reports_unsupported_url(self) -> None:
        self.plugin.on_download_by_link_fallback(
            'tester',
            {'request': 'https://example.com/file.zip'},
            _MODULE.CMD_DOWNLOAD_BY_LINK_FALLBACK,
        )

        self.assertEqual(
            self.spoken,
            ['У меня пока нет плагина-загрузчика для этой ссылки.'],
        )
        self.assertEqual(self.replies[-1][1]['error'], 'unsupported_url')
        self.assertEqual(self.replies[-1][1]['request'], 'https://example.com/file.zip')


if __name__ == '__main__':
    unittest.main()
