#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin
from weather_tools import MAX_FORECAST_DAYS, build_default_weather_parser


CACHE_TTL_SEC = 10 * 60


class WeatherWttrPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._default_city = ''
        self._parser = build_default_weather_parser()
        self._ui_locale = 'ru'

    def on_init(self) -> None:
        self.add_listener('weather:forecast', self.on_forecast, listener_id='weather_forecast')
        self.add_listener('gui:request-panels', self.on_request_panels, listener_id='weather_request_panels')
        self.add_listener('weather:update-settings', self.on_update_settings, listener_id='weather_update_settings')

        self._load_settings()
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

        self.register_command(
            'weather:forecast',
            {
                'en': 'Weather forecast via wttr.in',
                'ru': 'Прогноз погоды через wttr.in',
            },
            {
                'city': 'City/location name',
                'days': 'Number of days (1..3), default 1',
                'lang': 'Language code (ru/en), default by locale',
                'text': 'Free-form weather query',
            },
        )

        self.register_speech_rule('weather:forecast', 'погода в {city:String}')
        self.register_speech_rule('weather:forecast', 'прогноз погоды в {city:String}')
        self.register_speech_rule('weather:forecast', 'weather in {city:String}')

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
        parsed = self._parser.parse_payload(
            data,
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
            payload = self._load_weather(city, lang)
            weather_vars = self._build_weather_vars(city, days, lang, day_offset, payload)
        except Exception as error:
            self.log(f'weather_wttr error: {error}')
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

    def _build_weather_vars(self, requested_city: str, days: int, lang: str, day_offset: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        is_ru = lang.startswith('ru')
        area = self._extract_area_name(payload)
        display_city = requested_city or area or ('вашей локации' if is_ru else 'your location')

        out: Dict[str, Any] = {
            'lang': lang,
            'city': display_city,
            'requested_city': requested_city or '',
            'days': int(days),
            'day_offset': int(day_offset),
        }
        if area:
            out['resolved_city'] = area
        if area and requested_city and area.lower() != requested_city.lower():
            out['resolved_note'] = (
                f'wttr.in определил локацию как: {area}.'
                if is_ru
                else f'wttr.in resolved location as: {area}.'
            )

        daily_rows = payload.get('weather')
        if isinstance(daily_rows, list) and daily_rows:
            start = max(0, min(day_offset, len(daily_rows) - 1))
            first_row = daily_rows[start] if start < len(daily_rows) else {}
            if isinstance(first_row, dict):
                date_value = self._to_text(first_row.get('date'))
                if date_value:
                    out['date'] = date_value
                tmax = self._to_text(first_row.get('maxtempC'))
                tmin = self._to_text(first_row.get('mintempC'))
                if tmax:
                    out['max_c'] = tmax
                if tmin:
                    out['min_c'] = tmin

            forecast_text = self._format_daily(daily_rows, days, day_offset, is_ru)
            if forecast_text:
                out['forecast'] = forecast_text

        if day_offset == 0:
            current_rows = payload.get('current_condition')
            current = current_rows[0] if isinstance(current_rows, list) and current_rows else {}
            temp_c = self._to_text(current.get('temp_C'))
            feels_c = self._to_text(current.get('FeelsLikeC'))
            humidity = self._to_text(current.get('humidity'))
            wind = self._to_text(current.get('windspeedKmph'))
            desc = self._extract_desc(current, lang)
            if temp_c:
                out['temp_c'] = temp_c
            if feels_c:
                out['feels_c'] = feels_c
            if humidity:
                out['humidity'] = humidity
            if wind:
                out['wind_kmph'] = wind
            if desc:
                out['description'] = desc

        return out

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale
        self._register_settings_gui()

    def _load_weather(self, city: str, lang: str) -> Dict[str, Any]:
        cache_key = f'{city.lower()}::{lang}'
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - float(cached.get('ts', 0))) < CACHE_TTL_SEC:
            data = cached.get('data')
            if isinstance(data, dict):
                return data

        url_city = urllib.parse.quote(city.strip())
        url_lang = urllib.parse.quote(lang)
        url = f'https://wttr.in/{url_city}?format=j1&lang={url_lang}'

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
            raise ValueError('wttr payload is not an object')

        self._cache[cache_key] = {'ts': now, 'data': payload}
        return payload

    def _format_weather(self, requested_city: str, days: int, lang: str, day_offset: int, payload: Dict[str, Any]) -> str:
        is_ru = lang.startswith('ru')
        area = self._extract_area_name(payload)
        display_city = requested_city or area or ('вашей локации' if is_ru else 'your location')

        lines = []
        if day_offset == 0:
            current_rows = payload.get('current_condition')
            current = current_rows[0] if isinstance(current_rows, list) and current_rows else {}
            temp_c = self._to_text(current.get('temp_C'))
            feels_c = self._to_text(current.get('FeelsLikeC'))
            humidity = self._to_text(current.get('humidity'))
            wind = self._to_text(current.get('windspeedKmph'))
            desc = self._extract_desc(current, lang)

            if is_ru:
                lines.append(f'Погода: {display_city}. Сейчас {temp_c}°C, ощущается как {feels_c}°C, {desc}.')
                lines.append(f'Влажность: {humidity}%, ветер: {wind} км/ч.')
            else:
                lines.append(f'Weather: {display_city}. Now {temp_c}C, feels like {feels_c}C, {desc}.')
                lines.append(f'Humidity: {humidity}%, wind: {wind} km/h.')
        else:
            lines.append(f'Погода: {display_city}.' if is_ru else f'Weather: {display_city}.')

        if area and requested_city and area.lower() != requested_city.lower():
            lines.append(
                f'wttr.in определил локацию как: {area}.'
                if is_ru
                else f'wttr.in resolved location as: {area}.',
            )

        daily_rows = payload.get('weather')
        if isinstance(daily_rows, list) and daily_rows:
            lines.append(self._format_daily(daily_rows, days, day_offset, is_ru))

        return ' '.join(line for line in lines if line)

    def _format_daily(self, daily_rows: list, days: int, day_offset: int, is_ru: bool) -> str:
        chunks = []
        start = max(0, min(day_offset, len(daily_rows) - 1))
        count = max(1, min(days, MAX_FORECAST_DAYS, len(daily_rows) - start))
        for idx in range(start, start + count):
            row = daily_rows[idx]
            if not isinstance(row, dict):
                continue
            date = self._to_text(row.get('date'))
            tmax = self._to_text(row.get('maxtempC'))
            tmin = self._to_text(row.get('mintempC'))
            desc = ''
            hourly = row.get('hourly')
            if isinstance(hourly, list) and hourly:
                desc = self._extract_desc(hourly[0], 'ru' if is_ru else 'en')
            chunks.append(f'{date}: {tmin}..{tmax}°C, {desc}' if is_ru else f'{date}: {tmin}..{tmax}C, {desc}')

        if not chunks:
            return ''
        return ('Прогноз: ' if is_ru else 'Forecast: ') + '; '.join(chunks) + '.'

    def _extract_area_name(self, payload: Dict[str, Any]) -> str:
        nearest = payload.get('nearest_area')
        if not isinstance(nearest, list) or not nearest:
            return ''
        row = nearest[0]
        if not isinstance(row, dict):
            return ''
        names = row.get('areaName')
        if isinstance(names, list) and names:
            first = names[0]
            if isinstance(first, dict):
                value = first.get('value')
                return str(value).strip() if value else ''
        return ''

    def _extract_desc(self, row: Any, lang: str) -> str:
        if not isinstance(row, dict):
            return ''
        key = 'lang_ru' if str(lang).startswith('ru') else 'weatherDesc'
        values = row.get(key)
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, dict):
                value = first.get('value')
                return str(value).strip() if value else ''
        if key != 'weatherDesc':
            fallback = row.get('weatherDesc')
            if isinstance(fallback, list) and fallback:
                first = fallback[0]
                if isinstance(first, dict):
                    value = first.get('value')
                    return str(value).strip() if value else ''
        return ''

    def _load_settings(self) -> None:
        self._default_city = self._parser.normalize_city(self.get_property('defaultCity', ''))

    def _register_settings_gui(self) -> None:
        texts = self._ui_texts()
        self.setup_options_panel(
            panel_id='weather_wttr_settings',
            name=texts['panel_name'],
            msg_tag='weather:update-settings',
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
                'panel_name': 'Погода wttr.in',
                'description': (
                    'Город по умолчанию используется для запросов вроде "погода", "погода завтра".\n'
                    'Примеры: Белград, Москва, Нью-Йорк'
                ),
                'default_city_label': 'Город по умолчанию',
            }
        return {
            'panel_name': 'Weather wttr.in',
            'description': (
                'Default city is used for queries like "weather" or "weather tomorrow".\n'
                'Examples: Belgrade, Moscow, New York'
            ),
            'default_city_label': 'Default city',
        }

    def _to_text(self, value: Any) -> str:
        if value is None:
            return '?'
        text = str(value).strip()
        return text if text else '?'


if __name__ == '__main__':
    run_plugin(WeatherWttrPlugin)
