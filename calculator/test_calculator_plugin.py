import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'calculator', 'calculator_plugin')
CalculatorPlugin = _MODULE.CalculatorPlugin


class CalculatorPluginContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = CalculatorPlugin()
        self.calls = []
        self.next_alternatives = []

        def fake_send_message(tag, data=None):
            self.calls.append((tag, data))

        self.plugin.send_message = fake_send_message  # type: ignore[method-assign]
        self.plugin.call_next_alternative = lambda *args: self.next_alternatives.append(args)  # type: ignore[method-assign]

    def _last_say_text(self):
        say_calls = [item for item in self.calls if item[0] == 'MinaChan:request-say']
        self.assertTrue(say_calls)
        payload = say_calls[-1][1]
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload.get('direct'))
        return payload.get('text')

    def test_arithmetic_expression_still_works(self) -> None:
        self.plugin.on_eval('tester', {'text': '2+2*5'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '12')

    def test_length_conversion_km_to_m(self) -> None:
        self.plugin.on_eval('tester', {'text': '10 км в м'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '10000 m')

    def test_weight_conversion_kg_to_lb(self) -> None:
        self.plugin.on_eval('tester', {'text': '2 kg to lb'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '4.409245 lb')

    def test_temperature_conversion_c_to_f(self) -> None:
        self.plugin.on_eval('tester', {'text': '100 c в f'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '212 °F')

    def test_time_conversion_hours_to_minutes(self) -> None:
        self.plugin.on_eval('tester', {'text': '2 часа в минуты'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '120 min')

    def test_how_many_phrase_without_amount_uses_one(self) -> None:
        self.plugin.on_eval('tester', {'text': 'сколько метров в километре'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '1000 m')

    def test_direct_payload_amount_from_to_conversion(self) -> None:
        self.plugin.on_eval('tester', {'amount': 5, 'from': 'day', 'to': 'h'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '120 h')

    def test_incompatible_units_return_calc_error(self) -> None:
        self.plugin.on_eval('tester', {'text': '5 кг в метры'}, 'calculator:eval')
        err_calls = [item for item in self.calls if item[0] == 'MinaChan:request-say']
        self.assertTrue(err_calls)
        self.assertEqual(err_calls[-1][1], {'intent': 'CALC_ERROR'})

    def test_percentage_expression_works(self) -> None:
        self.plugin.on_eval('tester', {'text': '20 процентов от 50'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '10')

    def test_decimal_comma_expression_works(self) -> None:
        self.plugin.on_eval('tester', {'text': '2,5 + 1'}, 'calculator:eval')
        self.assertEqual(self._last_say_text(), '3.5')

    def test_eval_injection_payload_returns_error(self) -> None:
        self.plugin.on_eval('tester', {'text': '__import__(\"os\").system(\"id\")'}, 'calculator:eval')
        err_calls = [item for item in self.calls if item[0] == 'MinaChan:request-say']
        self.assertTrue(err_calls)
        self.assertEqual(err_calls[-1][1], {'intent': 'CALC_ERROR'})

    def test_route_conversion_handles_supported_unit_query(self) -> None:
        self.plugin.on_route_conversion('tester', {'text': '10 км в м'}, _MODULE.CMD_ROUTE_CONVERSION)

        self.assertEqual(self._last_say_text(), '10000 m')
        self.assertEqual(self.next_alternatives, [])

    def test_route_conversion_passes_currency_query_to_next_alternative(self) -> None:
        self.plugin.on_route_conversion('tester', {'text': '15 usd в rub'}, _MODULE.CMD_ROUTE_CONVERSION)

        self.assertEqual(
            self.next_alternatives,
            [('tester', _MODULE.CONVERSION_QUERY_TAG, _MODULE.CMD_ROUTE_CONVERSION, {'text': '15 usd в rub'})],
        )
        self.assertEqual(self.calls, [])

    def test_route_conversion_keeps_calc_error_for_incompatible_units(self) -> None:
        self.plugin.on_route_conversion('tester', {'text': '5 кг в метры'}, _MODULE.CMD_ROUTE_CONVERSION)

        err_calls = [item for item in self.calls if item[0] == 'MinaChan:request-say']
        self.assertTrue(err_calls)
        self.assertEqual(err_calls[-1][1], {'intent': 'CALC_ERROR'})
        self.assertEqual(self.next_alternatives, [])


if __name__ == '__main__':
    unittest.main()
