import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'conversion_router', 'conversion_router_plugin')
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
