#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from currency_tools import build_default_parser, contains_nbs_marker
from minachan_sdk import MinaChanPlugin, run_plugin


NBS_RATES_URL = 'https://kurs.resenje.org/api/v1/rates/today'
CACHE_TTL_SEC = 15 * 60
CONVERSION_QUERY_TAG = 'conversion:query'
CMD_ROUTE_CONVERSION = 'currency:nbs:route-conversion'
CONVERSION_ROUTE_PRIORITY = 200


class CurrencyNbsPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._cache_ts = 0.0
        self._cache_payload: Dict[str, Any] = {}
        self._parser = build_default_parser()

    def on_init(self) -> None:
        self.add_listener('currency:nbs:convert', self.on_convert, listener_id='currency_nbs_convert')
        self.add_listener(
            CMD_ROUTE_CONVERSION,
            self.on_route_conversion,
            listener_id='currency_nbs_route_conversion',
        )

        self.register_command(
            'currency:nbs:convert',
            {
                'en': 'Convert currency by National Bank of Serbia rates',
                'ru': 'Конвертировать валюту по курсам НБС',
            },
            {
                'amount': 'Numeric amount, default 1',
                'from': 'Source currency code (RSD, EUR, USD...)',
                'to': 'Target currency code (RSD, EUR, USD...)',
                'text': 'Free-form request text',
            },
        )
        self.set_alternative(
            CONVERSION_QUERY_TAG,
            CMD_ROUTE_CONVERSION,
            CONVERSION_ROUTE_PRIORITY,
        )

    def on_route_conversion(self, sender: str, data: Any, tag: str) -> None:
        text = self._parser.extract_text(data)
        if not text or not contains_nbs_marker(text):
            self.call_next_alternative(
                sender,
                CONVERSION_QUERY_TAG,
                CMD_ROUTE_CONVERSION,
                data,
            )
            return
        parsed = self._parser.parse_payload(data)
        if parsed is None:
            self.call_next_alternative(
                sender,
                CONVERSION_QUERY_TAG,
                CMD_ROUTE_CONVERSION,
                data,
            )
            return
        self._process(parsed, say_on_fail=True)

    def on_convert(self, sender: str, data: Any, tag: str) -> None:
        parsed = self._parser.parse_payload(data)
        if parsed is None:
            self.request_say_intent('CURRENCY_BAD_QUERY')
            return
        self._process(parsed, say_on_fail=True)

    def _process(self, parsed: Tuple[float, str, str], say_on_fail: bool) -> None:
        amount, src, dst = parsed
        try:
            payload = self._load_rates()
            converted, unit_rate = self._convert(amount, src, dst, payload.get('rates', {}))
        except Exception as error:
            self.log(f'currency_nbs error: {error}')
            if say_on_fail:
                self.request_say_intent('CURRENCY_FETCH_FAIL')
            return

        date = str(payload.get('date') or '')
        amount_text = self._fmt(amount)
        converted_text = self._fmt(converted)
        unit_rate_text = self._fmt(unit_rate)
        template_vars: Dict[str, Any] = {
            'provider': 'NBS',
            'provider_name': 'НБС',
            'amount': amount_text,
            'src': src,
            'dst': dst,
            'converted': converted_text,
            'unit_rate': unit_rate_text,
            'date': date,
        }
        extra: Dict[str, Any] = {
            'provider': 'NBS',
            'provider_name': 'НБС',
            'amount': amount_text,
            'src': src,
            'dst': dst,
            'converted': converted_text,
            'unit_rate': unit_rate_text,
        }
        if date:
            extra['date'] = date
        self.request_say_intent(
            'CURRENCY_RESULT',
            template_vars=template_vars,
            extra=extra,
        )

    def _load_rates(self) -> Dict[str, Any]:
        now = time.time()
        if self._cache_payload and (now - self._cache_ts) < CACHE_TTL_SEC:
            return self._cache_payload

        request = urllib.request.Request(NBS_RATES_URL, headers={'User-Agent': 'MinaChan/1.0'})
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read().decode('utf-8', errors='replace')
        payload = json.loads(raw)

        rows = self._extract_rows(payload)
        rates: Dict[str, float] = {'RSD': 1.0}
        date = ''

        for row in rows:
            code = str(row.get('code') or '').strip().upper()
            if not code:
                continue
            parity = self._to_float(row.get('parity'), 1.0)
            middle = self._to_float(row.get('exchange_middle'), 0.0)
            if parity <= 0 or middle <= 0:
                continue
            rates[code] = middle / parity
            if not date:
                date = str(row.get('date') or '')

        out = {'date': date, 'rates': rates}
        self._cache_payload = out
        self._cache_ts = now
        return out

    def _extract_rows(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            rates = payload.get('rates')
            if isinstance(rates, list):
                return [row for row in rates if isinstance(row, dict)]
            if payload.get('code') is not None:
                return [payload]
        raise ValueError('NBS payload does not have rates list/object')

    def _convert(self, amount: float, source: str, target: str, rates: Dict[str, float]) -> Tuple[float, float]:
        if source not in rates or target not in rates:
            raise ValueError(f'unsupported currency pair {source}->{target}')
        return self._convert_amount(amount, source, target, rates), self._convert_amount(1.0, source, target, rates)

    def _convert_amount(self, amount: float, source: str, target: str, rates: Dict[str, float]) -> float:
        rsd_amount = amount if source == 'RSD' else amount * float(rates[source])
        return rsd_amount if target == 'RSD' else rsd_amount / float(rates[target])

    def _to_float(self, value: Any, default: float) -> float:
        try:
            return float(str(value).replace(',', '.'))
        except Exception:
            return default

    def _fmt(self, number: float) -> str:
        value = f'{number:.6f}'.rstrip('0').rstrip('.')
        return value if value else '0'


if __name__ == '__main__':
    run_plugin(CurrencyNbsPlugin)
