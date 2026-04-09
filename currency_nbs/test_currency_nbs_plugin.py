import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'currency_nbs' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('currency_nbs_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for currency_nbs plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
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
