#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin
from weather_tools import MAX_FORECAST_DAYS, build_default_weather_parser


CACHE_TTL_SEC = 10 * 60
GEO_CACHE_TTL_SEC = 24 * 60 * 60
_COORD_RE = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$')

WEATHER_CODE_DESC_EN = {
    0: 'clear sky',
    1: 'mainly clear',
    2: 'partly cloudy',
    3: 'overcast',
    45: 'fog',
    48: 'rime fog',
    51: 'light drizzle',
    53: 'moderate drizzle',
    55: 'dense drizzle',
    56: 'light freezing drizzle',
    57: 'dense freezing drizzle',
    61: 'slight rain',
    63: 'moderate rain',
    65: 'heavy rain',
    66: 'light freezing rain',
    67: 'heavy freezing rain',
    71: 'slight snowfall',
    73: 'moderate snowfall',
    75: 'heavy snowfall',
    77: 'snow grains',
    80: 'slight rain showers',
    81: 'moderate rain showers',
    82: 'violent rain showers',
    85: 'slight snow showers',
    86: 'heavy snow showers',
    95: 'thunderstorm',
    96: 'thunderstorm with slight hail',
    99: 'thunderstorm with heavy hail',
}

WEATHER_CODE_DESC_RU = {
    0: 'ясно',
    1: 'преимущественно ясно',
    2: 'переменная облачность',
    3: 'пасмурно',
    45: 'туман',
    48: 'изморозевый туман',
    51: 'слабая морось',
    53: 'умеренная морось',
    55: 'сильная морось',
    56: 'слабая переохлажденная морось',
    57: 'сильная переохлажденная морось',
    61: 'слабый дождь',
    63: 'умеренный дождь',
    65: 'сильный дождь',
    66: 'слабый ледяной дождь',
    67: 'сильный ледяной дождь',
    71: 'слабый снег',
    73: 'умеренный снег',
    75: 'сильный снег',
    77: 'снежная крупа',
    80: 'слабые ливни',
    81: 'умеренные ливни',
    82: 'сильные ливни',
    85: 'слабые снежные заряды',
    86: 'сильные снежные заряды',
    95: 'гроза',
    96: 'гроза с небольшим градом',
    99: 'гроза с сильным градом',
}


class WeatherOpenMeteoPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._weather_cache: Dict[str, Dict[str, Any]] = {}
        self._geo_cache: Dict[str, Dict[str, Any]] = {}
        self._default_city = ''
        self._parser = build_default_weather_parser()
        self._ui_locale = 'ru'

    def on_init(self) -> None:
        self.add_listener(
            'weather:forecast-open-meteo',
            self.on_forecast,
            listener_id='weather_open_meteo_forecast',
        )
        self.add_listener(
            'weather:open-meteo:forecast',
            self.on_forecast,
            listener_id='weather_open_meteo_forecast_alias',
        )
        self.add_listener(
            'gui:request-panels',
            self.on_request_panels,
            listener_id='weather_open_meteo_request_panels',
        )
        self.add_listener(
            'weather_open_meteo:update-settings',
            self.on_update_settings,
            listener_id='weather_open_meteo_update_settings',
        )
        # Compatibility path used by scenario bootstrap and old weather flow.
        self.add_listener(
            'weather:update-settings',
            self.on_update_settings,
            listener_id='weather_open_meteo_update_settings_compat',
        )

        self._load_settings()
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

        self.register_command(
            'weather:forecast-open-meteo',
            {
                'en': 'Weather forecast via Open-Meteo',
                'ru': 'Прогноз погоды через Open-Meteo',
            },
            {
                'city': 'City/location name or coordinates "lat,lon"',
                'days': 'Number of days (1..3), default 1',
                'lang': 'Language code (ru/en), default by locale',
                'text': 'Free-form weather query',
            },
        )
        self.register_command(
            'weather:open-meteo:forecast',
            {
                'en': 'Alias for weather:forecast-open-meteo',
                'ru': 'Алиас для weather:forecast-open-meteo',
            },
        )

        self.register_speech_rule('weather:forecast-open-meteo', 'погода open meteo в {city:String}')
        self.register_speech_rule('weather:forecast-open-meteo', 'прогноз open meteo в {city:String}')
        self.register_speech_rule('weather:forecast-open-meteo', 'open meteo погода в {city:String}')
        self.register_speech_rule('weather:forecast-open-meteo', 'weather open meteo in {city:String}')
        self.register_speech_rule('weather:forecast-open-meteo', 'open meteo weather in {city:String}')

    def on_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._register_settings_gui()

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return
        city = self._parser.normalize_city(data.get('city'))
        self._default_city = city
        self.set_property('defaultCity', city)
        self.save_properties()
        self._register_settings_gui()
        template_vars: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}
        if city:
            template_vars['city'] = city
            extra['city'] = city
        self.request_say_intent(
            'WEATHER_SETTINGS_SAVED',
            template_vars=template_vars,
            extra=extra,
        )

    def on_forecast(self, sender: str, data: Any, tag: str) -> None:
        prepared_data = self._prepare_payload_for_parser(data)
        parsed = self._parser.parse_payload(
            prepared_data,
            default_city=self._default_city,
            default_days=1,
            default_lang=self._default_lang(),
        )
        if parsed is None:
            self.request_say_intent('WEATHER_BAD_QUERY')
            return
        self._process(parsed, say_on_fail=True)

    def _process(self, parsed: Tuple[str, int, str, int], say_on_fail: bool) -> None:
        city, days, lang, day_offset = parsed
        if not city:
            if say_on_fail:
                self.request_say_intent('WEATHER_CITY_NOT_SET')
            return
        try:
            location, payload = self._load_open_meteo(city, lang)
            weather_vars = self._build_weather_vars(city, days, lang, day_offset, location, payload)
        except Exception as error:
            self.log(f'weather_open_meteo error: {error}')
            if say_on_fail:
                self.request_say_intent('WEATHER_FETCH_FAIL')
            return
        extra: Dict[str, Any] = {}
        for key, value in weather_vars.items():
            if isinstance(value, (str, int, float, bool)):
                extra[key] = value
        self.request_say_intent(
            'WEATHER_RESULT',
            template_vars=weather_vars,
            extra=extra,
        )

    def _load_open_meteo(self, city: str, lang: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        location = self._resolve_location(city, lang)
        payload = self._load_forecast(
            latitude=float(location.get('latitude')),
            longitude=float(location.get('longitude')),
            timezone=str(location.get('timezone') or 'auto'),
        )
        return location, payload

    def _resolve_location(self, city: str, lang: str) -> Dict[str, Any]:
        coords = self._parse_coordinates(city)
        if coords is not None:
            latitude, longitude = coords
            return {
                'name': f'{self._format_number(latitude, 4)}, {self._format_number(longitude, 4)}',
                'latitude': latitude,
                'longitude': longitude,
                'timezone': 'auto',
            }

        cache_key = f'{city.strip().lower()}::{lang}'
        now = time.time()
        cached = self._geo_cache.get(cache_key)
        if cached and (now - float(cached.get('ts', 0))) < GEO_CACHE_TTL_SEC:
            data = cached.get('data')
            if isinstance(data, dict):
                return data

        query_lang = 'ru' if str(lang).startswith('ru') else 'en'
        params = urllib.parse.urlencode(
            {
                'name': city,
                'count': 1,
                'language': query_lang,
                'format': 'json',
            },
        )
        url = f'https://geocoding-api.open-meteo.com/v1/search?{params}'
        payload = self._request_json(url)
        results = payload.get('results')
        if not isinstance(results, list) or not results:
            raise ValueError(f'Open-Meteo geocoding: city "{city}" not found')
        row = results[0]
        if not isinstance(row, dict):
            raise ValueError('Open-Meteo geocoding: invalid result row')

        latitude = self._to_float(row.get('latitude'))
        longitude = self._to_float(row.get('longitude'))
        if latitude is None or longitude is None:
            raise ValueError('Open-Meteo geocoding: latitude/longitude missing')

        location = {
            'name': self._to_text(row.get('name')),
            'admin1': self._to_text(row.get('admin1')),
            'country': self._to_text(row.get('country')),
            'country_code': self._to_text(row.get('country_code')),
            'timezone': self._to_text(row.get('timezone')) or 'auto',
            'latitude': latitude,
            'longitude': longitude,
        }
        self._geo_cache[cache_key] = {
            'ts': now,
            'data': location,
        }
        return location

    def _load_forecast(self, latitude: float, longitude: float, timezone: str) -> Dict[str, Any]:
        cache_key = f'{latitude:.4f}:{longitude:.4f}:{timezone}'
        now = time.time()
        cached = self._weather_cache.get(cache_key)
        if cached and (now - float(cached.get('ts', 0))) < CACHE_TTL_SEC:
            data = cached.get('data')
            if isinstance(data, dict):
                return data

        params = urllib.parse.urlencode(
            {
                'latitude': f'{latitude:.6f}',
                'longitude': f'{longitude:.6f}',
                'current': (
                    'temperature_2m,relative_humidity_2m,apparent_temperature,'
                    'weather_code,wind_speed_10m,precipitation'
                ),
                'daily': (
                    'weather_code,temperature_2m_max,temperature_2m_min,'
                    'precipitation_sum,precipitation_probability_max,'
                    'sunrise,sunset,wind_speed_10m_max'
                ),
                'timezone': timezone or 'auto',
            },
        )
        url = f'https://api.open-meteo.com/v1/forecast?{params}'
        payload = self._request_json(url)
        if not isinstance(payload, dict):
            raise ValueError('Open-Meteo forecast payload is not an object')

        current = payload.get('current')
        daily = payload.get('daily')
        if not isinstance(current, dict) or not isinstance(daily, dict):
            raise ValueError('Open-Meteo forecast payload missing current/daily sections')

        self._weather_cache[cache_key] = {
            'ts': now,
            'data': payload,
        }
        return payload

    def _build_weather_vars(
        self,
        requested_city: str,
        days: int,
        lang: str,
        day_offset: int,
        location: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        is_ru = lang.startswith('ru')
        resolved_city = self._compose_location_label(location)
        display_city = requested_city or resolved_city or ('вашей локации' if is_ru else 'your location')

        out: Dict[str, Any] = {
            'lang': lang,
            'city': display_city,
            'requested_city': requested_city or '',
            'days': int(days),
            'day_offset': int(day_offset),
            'provider_name': 'Open-Meteo',
            'source': 'open-meteo',
        }
        if resolved_city:
            out['resolved_city'] = resolved_city
        if (
            resolved_city
            and requested_city
            and self._normalize_city_key(resolved_city) != self._normalize_city_key(requested_city)
        ):
            out['resolved_note'] = (
                f'Open-Meteo определил локацию как: {resolved_city}.'
                if is_ru
                else f'Open-Meteo resolved location as: {resolved_city}.'
            )

        daily = payload.get('daily')
        if isinstance(daily, dict):
            total_days = self._daily_size(daily)
            if total_days > 0:
                start = max(0, min(day_offset, total_days - 1))

                date_value = self._daily_value(daily, 'time', start)
                min_c = self._format_number(self._daily_value(daily, 'temperature_2m_min', start), 1)
                max_c = self._format_number(self._daily_value(daily, 'temperature_2m_max', start), 1)
                code = self._daily_value(daily, 'weather_code', start)
                desc = self._weather_code_text(code, lang)
                precip_prob = self._format_number(self._daily_value(daily, 'precipitation_probability_max', start), 0)
                wind_max = self._format_number(self._daily_value(daily, 'wind_speed_10m_max', start), 1)
                precip_sum = self._format_number(self._daily_value(daily, 'precipitation_sum', start), 1)
                sunrise = self._extract_time_fragment(self._daily_value(daily, 'sunrise', start))
                sunset = self._extract_time_fragment(self._daily_value(daily, 'sunset', start))

                if date_value:
                    out['date'] = str(date_value)
                if min_c:
                    out['min_c'] = min_c
                if max_c:
                    out['max_c'] = max_c
                if precip_prob:
                    out['precip_prob'] = precip_prob
                if precip_sum:
                    out['precip_mm_day'] = precip_sum
                if wind_max:
                    out['wind_kmph_day'] = wind_max
                if sunrise:
                    out['sunrise'] = sunrise
                if sunset:
                    out['sunset'] = sunset
                if day_offset > 0 and desc:
                    out['description'] = desc

                forecast_text = self._format_daily(daily, days, day_offset, is_ru)
                if forecast_text:
                    out['forecast'] = forecast_text

        if day_offset == 0:
            current = payload.get('current')
            if isinstance(current, dict):
                temp_c = self._format_number(current.get('temperature_2m'), 1)
                feels_c = self._format_number(current.get('apparent_temperature'), 1)
                humidity = self._format_number(current.get('relative_humidity_2m'), 0)
                wind = self._format_number(current.get('wind_speed_10m'), 1)
                precip = self._format_number(current.get('precipitation'), 1)
                desc = self._weather_code_text(current.get('weather_code'), lang)

                if temp_c:
                    out['temp_c'] = temp_c
                if feels_c:
                    out['feels_c'] = feels_c
                if humidity:
                    out['humidity'] = humidity
                if wind:
                    out['wind_kmph'] = wind
                if precip:
                    out['precip_mm'] = precip
                if desc:
                    out['description'] = desc

        return out

    def _format_daily(self, daily: Dict[str, Any], days: int, day_offset: int, is_ru: bool) -> str:
        total = self._daily_size(daily)
        if total <= 0:
            return ''
        start = max(0, min(day_offset, total - 1))
        count = max(1, min(days, MAX_FORECAST_DAYS, total - start))
        chunks = []
        for idx in range(start, start + count):
            date = self._to_text(self._daily_value(daily, 'time', idx))
            tmin = self._format_number(self._daily_value(daily, 'temperature_2m_min', idx), 1) or '?'
            tmax = self._format_number(self._daily_value(daily, 'temperature_2m_max', idx), 1) or '?'
            desc = self._weather_code_text(self._daily_value(daily, 'weather_code', idx), 'ru' if is_ru else 'en')
            precip = self._format_number(self._daily_value(daily, 'precipitation_probability_max', idx), 0)
            wind = self._format_number(self._daily_value(daily, 'wind_speed_10m_max', idx), 1)

            if is_ru:
                chunk = f'{date}: {tmin}..{tmax}°C, {desc}'
                extra = []
                if precip:
                    extra.append(f'осадки до {precip}%')
                if wind:
                    extra.append(f'ветер до {wind} км/ч')
            else:
                chunk = f'{date}: {tmin}..{tmax}C, {desc}'
                extra = []
                if precip:
                    extra.append(f'precip up to {precip}%')
                if wind:
                    extra.append(f'wind up to {wind} km/h')

            if extra:
                chunk = f'{chunk}, {", ".join(extra)}'
            chunks.append(chunk)

        if not chunks:
            return ''
        prefix = 'Прогноз: ' if is_ru else 'Forecast: '
        return prefix + '; '.join(chunks) + '.'

    def _daily_size(self, daily: Dict[str, Any]) -> int:
        size = 0
        for value in daily.values():
            if isinstance(value, list):
                size = max(size, len(value))
        return size

    def _daily_value(self, daily: Dict[str, Any], key: str, idx: int) -> Any:
        values = daily.get(key)
        if not isinstance(values, list):
            return None
        if idx < 0 or idx >= len(values):
            return None
        return values[idx]

    def _parse_coordinates(self, value: str) -> Optional[Tuple[float, float]]:
        match = _COORD_RE.match(str(value or '').strip())
        if match is None:
            return None
        latitude = self._to_float(match.group(1))
        longitude = self._to_float(match.group(2))
        if latitude is None or longitude is None:
            return None
        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            return None
        return latitude, longitude

    def _request_json(self, url: str) -> Dict[str, Any]:
        raw = ''
        last_error: Optional[Exception] = None
        for _ in range(2):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'MinaChan/1.0'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    raw = response.read().decode('utf-8', errors='replace')
                last_error = None
                break
            except Exception as error:
                last_error = error
                time.sleep(0.35)
        if last_error is not None:
            raise last_error

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('Open-Meteo payload is not an object')
        return payload

    def _prepare_payload_for_parser(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        prepared = dict(data)
        for key in ('text', 'value', 'msgData', 'query'):
            raw_value = prepared.get(key)
            if raw_value is None:
                continue
            prepared[key] = self._strip_provider_tokens(str(raw_value))
        return prepared

    def _strip_provider_tokens(self, value: str) -> str:
        out = str(value or '')
        out = re.sub(r'open\s*[- ]?\s*meteo', ' ', out, flags=re.IGNORECASE)
        out = re.sub(r'опен\s+метео', ' ', out, flags=re.IGNORECASE)
        out = re.sub(r'\s+', ' ', out).strip()
        return out

    def _weather_code_text(self, code: Any, lang: str) -> str:
        code_num = self._to_int(code)
        if code_num is None:
            return 'неизвестная погода' if str(lang).startswith('ru') else 'unknown weather'
        if str(lang).startswith('ru'):
            return WEATHER_CODE_DESC_RU.get(code_num, 'неизвестная погода')
        return WEATHER_CODE_DESC_EN.get(code_num, 'unknown weather')

    def _compose_location_label(self, location: Dict[str, Any]) -> str:
        if not isinstance(location, dict):
            return ''
        parts = []
        for key in ('name', 'admin1', 'country'):
            value = self._to_text(location.get(key))
            if value and self._normalize_city_key(value) not in [self._normalize_city_key(p) for p in parts]:
                parts.append(value)
        if not parts:
            code = self._to_text(location.get('country_code'))
            if code:
                parts.append(code)
        return ', '.join(parts)

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale
        self._register_settings_gui()

    def _load_settings(self) -> None:
        self._default_city = self._parser.normalize_city(self.get_property('defaultCity', ''))

    def _register_settings_gui(self) -> None:
        texts = self._ui_texts()
        self.setup_options_panel(
            panel_id='weather_open_meteo_settings',
            name=texts['panel_name'],
            msg_tag='weather_open_meteo:update-settings',
            controls=[
                {
                    'id': 'description',
                    'type': 'label',
                    'label': texts['description'],
                },
                {
                    'id': 'city',
                    'type': 'textfield',
                    'label': texts['default_city_label'],
                    'value': self._default_city,
                },
            ],
        )

    def _default_lang(self) -> str:
        return 'ru' if self._is_ru_locale() else 'en'

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _ui_texts(self) -> Dict[str, str]:
        if self._is_ru_locale():
            return {
                'panel_name': 'Погода Open-Meteo',
                'description': (
                    'Город по умолчанию для запросов через Open-Meteo.\n'
                    'Можно указать координаты: 44.81, 20.46'
                ),
                'default_city_label': 'Город или координаты',
            }
        return {
            'panel_name': 'Weather Open-Meteo',
            'description': (
                'Default city/location for Open-Meteo weather requests.\n'
                'Coordinates are supported: 44.81, 20.46'
            ),
            'default_city_label': 'City or coordinates',
        }

    def _normalize_city_key(self, value: Any) -> str:
        return re.sub(r'\s+', ' ', str(value or '').strip().lower().replace('ё', 'е'))

    def _to_float(self, value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    def _to_int(self, value: Any) -> Optional[int]:
        try:
            return int(float(value))
        except Exception:
            return None

    def _format_number(self, value: Any, digits: int) -> str:
        number = self._to_float(value)
        if number is None:
            return ''
        if digits <= 0:
            text = f'{number:.0f}'
        else:
            text = f'{number:.{digits}f}'
            if '.' in text:
                text = text.rstrip('0').rstrip('.')
        if text == '-0':
            return '0'
        return text

    def _extract_time_fragment(self, value: Any) -> str:
        text = self._to_text(value)
        if not text:
            return ''
        if 'T' in text:
            return text.split('T', 1)[1]
        return text

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ''
        text = str(value).strip()
        return text if text else ''


if __name__ == '__main__':
    run_plugin(WeatherOpenMeteoPlugin)
