import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'currency_nbs', 'currency_nbs_plugin')
CurrencyNbsPlugin = _MODULE.CurrencyNbsPlugin


class CurrencyNbsPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = CurrencyNbsPlugin()
        self.next_alternatives = []
        self.process_calls = []
        self.plugin.call_next_alternative = lambda *args: self.next_alternatives.append(args)  # type: ignore[method-assign]
        self.plugin._process = lambda parsed, say_on_fail: self.process_calls.append((parsed, say_on_fail))  # type: ignore[method-assign]

    def test_route_conversion_processes_nbs_marked_request(self) -> None:
        self.plugin.on_route_conversion(
            'tester',
            {'text': 'nbs 15 eur в rsd'},
            _MODULE.CMD_ROUTE_CONVERSION,
        )

        self.assertEqual(self.process_calls, [((15.0, 'EUR', 'RSD'), True)])
        self.assertEqual(self.next_alternatives, [])

    def test_route_conversion_skips_plain_currency_request(self) -> None:
        payload = {'text': '15 usd в rub'}

        self.plugin.on_route_conversion('tester', payload, _MODULE.CMD_ROUTE_CONVERSION)

        self.assertEqual(self.process_calls, [])
        self.assertEqual(
            self.next_alternatives,
            [('tester', _MODULE.CONVERSION_QUERY_TAG, _MODULE.CMD_ROUTE_CONVERSION, payload)],
        )


if __name__ == '__main__':
    unittest.main()
