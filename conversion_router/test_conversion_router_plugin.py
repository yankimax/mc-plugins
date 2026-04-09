import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'conversion_router' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('conversion_router_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for conversion_router plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
ConversionRouterPlugin = _MODULE.ConversionRouterPlugin


class ConversionRouterPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = ConversionRouterPlugin()
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

    def test_on_init_registers_shared_conversion_rules_and_fallback(self) -> None:
        self.plugin.on_init()

        self.assertTrue(any(args[0] == _MODULE.CMD_CONVERSION_QUERY for args, _ in self.commands))
        self.assertEqual(len([item for item in self.rules if item[0][0] == _MODULE.CMD_CONVERSION_QUERY]), 4)
        self.assertEqual(
            self.alternatives,
            [((_MODULE.CMD_CONVERSION_QUERY, _MODULE.CMD_CONVERSION_QUERY_FALLBACK, _MODULE.CONVERSION_FALLBACK_PRIORITY), {})],
        )

    def test_fallback_reports_missing_request(self) -> None:
        self.plugin.on_conversion_query_fallback('tester', {}, _MODULE.CMD_CONVERSION_QUERY_FALLBACK)

        self.assertEqual(
            self.spoken,
            ['Скажи, что именно нужно конвертировать.'],
        )
        self.assertEqual(self.replies[-1][1]['error'], 'missing_query')

    def test_fallback_reports_unsupported_request(self) -> None:
        self.plugin.on_conversion_query_fallback(
            'tester',
            {'text': 'сколько градусов в Белграде'},
            _MODULE.CMD_CONVERSION_QUERY_FALLBACK,
        )

        self.assertEqual(
            self.spoken,
            ['Я не нашла плагин, который умеет обработать такой запрос на конвертацию.'],
        )
        self.assertEqual(self.replies[-1][1]['error'], 'unsupported_query')
        self.assertEqual(self.replies[-1][1]['request'], 'сколько градусов в Белграде')


if __name__ == '__main__':
    unittest.main()
