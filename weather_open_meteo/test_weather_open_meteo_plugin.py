import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'weather_open_meteo' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('weather_open_meteo_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for weather_open_meteo plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
WeatherOpenMeteoPlugin = _MODULE.WeatherOpenMeteoPlugin
LIVE_WEATHER_ENV = 'MINACHAN_TEST_LIVE_WEATHER'


class WeatherOpenMeteoPluginContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = WeatherOpenMeteoPlugin()
        self.calls = []
        self.props = {}

        def fake_send_message(tag, data=None):
            self.calls.append((tag, data))

        def fake_set_property(name, value):
            self.props[name] = value

        self.plugin.send_message = fake_send_message  # type: ignore[method-assign]
        self.plugin.set_property = fake_set_property  # type: ignore[method-assign]
        self.plugin.save_properties = lambda: None  # type: ignore[method-assign]

    def _last_request_say(self):
        say_calls = [item for item in self.calls if item[0] == 'MinaChan:request-say']
        self.assertTrue(say_calls)
        return say_calls[-1][1]

    def _sample_weather_payload(self):
        location = {
            'name': 'Belgrade',
            'admin1': 'Central Serbia',
            'country': 'Serbia',
            'latitude': 44.8176,
            'longitude': 20.4633,
            'timezone': 'Europe/Belgrade',
        }
        payload = {
            'current': {
                'temperature_2m': 3.4,
                'apparent_temperature': 1.2,
                'relative_humidity_2m': 77,
                'wind_speed_10m': 12.6,
                'precipitation': 0.2,
                'weather_code': 3,
            },
            'daily': {
                'time': ['2026-02-25', '2026-02-26', '2026-02-27'],
                'temperature_2m_min': [-1.4, -2.0, -3.0],
                'temperature_2m_max': [6.1, 4.8, 3.3],
                'weather_code': [3, 61, 71],
                'precipitation_sum': [0.3, 4.2, 1.9],
                'precipitation_probability_max': [30, 70, 85],
                'sunrise': ['2026-02-25T06:31', '2026-02-26T06:29', '2026-02-27T06:27'],
                'sunset': ['2026-02-25T17:18', '2026-02-26T17:20', '2026-02-27T17:21'],
                'wind_speed_10m_max': [16.3, 21.2, 24.9],
            },
        }
        return location, payload

    def test_settings_saved_uses_common_weather_intent(self) -> None:
        self.plugin.on_update_settings('tester', {'city': ' Belgrade '}, 'weather_open_meteo:update-settings')
        payload = self._last_request_say()
        self.assertEqual(payload.get('intent'), 'WEATHER_SETTINGS_SAVED')
        self.assertEqual(payload.get('city'), 'Belgrade')

    def test_forecast_sends_weather_result_with_open_meteo_vars(self) -> None:
        location, payload = self._sample_weather_payload()
        self.plugin._load_open_meteo = lambda city, lang: (location, payload)  # type: ignore[method-assign]

        self.plugin.on_forecast('tester', {'city': 'Belgrade', 'days': 2, 'lang': 'ru'}, 'weather:forecast-open-meteo')

        message = self._last_request_say()
        self.assertEqual(message.get('intent'), 'WEATHER_RESULT')
        vars_map = message.get('vars')
        self.assertIsInstance(vars_map, dict)
        self.assertEqual(vars_map.get('provider_name'), 'Open-Meteo')
        self.assertEqual(vars_map.get('city'), 'Belgrade')
        self.assertIn('forecast', vars_map)
        self.assertIn('description', vars_map)
        self.assertEqual(message.get('provider_name'), 'Open-Meteo')

    def test_forecast_prefers_city_argument_when_text_contains_provider_words(self) -> None:
        location, payload = self._sample_weather_payload()
        self.plugin._load_open_meteo = lambda city, lang: (location, payload)  # type: ignore[method-assign]

        self.plugin.on_forecast(
            'tester',
            {'city': 'Belgrade', 'text': 'погода open meteo в белграде', 'lang': 'ru'},
            'weather:forecast-open-meteo',
        )

        message = self._last_request_say()
        vars_map = message.get('vars')
        self.assertIsInstance(vars_map, dict)
        city_value = str(vars_map.get('city') or '').lower()
        self.assertNotIn('open meteo', city_value)
        self.assertIn(city_value, ('belgrade', 'beograd'))

    def test_forecast_with_tomorrow_offset_uses_daily_slice(self) -> None:
        location, payload = self._sample_weather_payload()
        self.plugin._load_open_meteo = lambda city, lang: (location, payload)  # type: ignore[method-assign]

        self.plugin.on_forecast(
            'tester',
            {'city': 'Belgrade', 'days': 1, 'dayOffset': 1, 'lang': 'en'},
            'weather:forecast-open-meteo',
        )

        message = self._last_request_say()
        vars_map = message.get('vars')
        self.assertIsInstance(vars_map, dict)
        self.assertEqual(vars_map.get('date'), '2026-02-26')
        self.assertEqual(vars_map.get('min_c'), '-2')
        self.assertEqual(vars_map.get('max_c'), '4.8')
        self.assertIn('forecast', vars_map)

    def test_forecast_without_city_and_default_city_reports_city_not_set(self) -> None:
        self.plugin.on_forecast('tester', {}, 'weather:forecast-open-meteo')
        payload = self._last_request_say()
        self.assertEqual(payload, {'intent': 'WEATHER_CITY_NOT_SET'})

    def test_invalid_payload_reports_bad_query(self) -> None:
        self.plugin.on_forecast('tester', 123, 'weather:forecast-open-meteo')
        payload = self._last_request_say()
        self.assertEqual(payload, {'intent': 'WEATHER_BAD_QUERY'})


class WeatherOpenMeteoPluginLiveApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        enabled = str(os.environ.get(LIVE_WEATHER_ENV, '')).strip().lower()
        if enabled not in ('1', 'true', 'yes', 'on'):
            raise unittest.SkipTest(f'set {LIVE_WEATHER_ENV}=1 to run live Open-Meteo API checks')

    def setUp(self) -> None:
        self.plugin = WeatherOpenMeteoPlugin()

    def test_live_geocoding_and_forecast_for_city(self) -> None:
        location, payload = self.plugin._load_open_meteo('Belgrade', 'en')

        self.assertIsInstance(location, dict)
        self.assertIsNotNone(location.get('latitude'))
        self.assertIsNotNone(location.get('longitude'))
        self.assertTrue(str(location.get('name') or '').strip())

        self.assertIsInstance(payload, dict)
        current = payload.get('current')
        daily = payload.get('daily')
        self.assertIsInstance(current, dict)
        self.assertIsInstance(daily, dict)
        self.assertIn('temperature_2m', current)
        self.assertIn('weather_code', current)
        self.assertIn('time', daily)
        self.assertIn('temperature_2m_min', daily)
        self.assertIn('temperature_2m_max', daily)

    def test_live_forecast_for_coordinates(self) -> None:
        location, payload = self.plugin._load_open_meteo('44.8176,20.4633', 'en')

        self.assertIsInstance(location, dict)
        self.assertEqual(location.get('timezone'), 'auto')
        self.assertIsInstance(payload, dict)

        vars_map = self.plugin._build_weather_vars(
            requested_city='44.8176,20.4633',
            days=2,
            lang='en',
            day_offset=0,
            location=location,
            payload=payload,
        )
        self.assertIsInstance(vars_map, dict)
        self.assertEqual(vars_map.get('provider_name'), 'Open-Meteo')
        self.assertIn('forecast', vars_map)
        self.assertIn('city', vars_map)


if __name__ == '__main__':
    unittest.main()
