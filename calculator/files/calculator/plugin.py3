#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import ast
import os
import operator
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin

CONVERSION_QUERY_TAG = 'conversion:query'
CMD_ROUTE_CONVERSION = 'calculator:route-conversion'
CONVERSION_ROUTE_PRIORITY = 300

WORD_NUM_RU = {
    'ноль': 0,
    'один': 1,
    'два': 2,
    'три': 3,
    'четыре': 4,
    'пять': 5,
    'шесть': 6,
    'семь': 7,
    'восемь': 8,
    'девять': 9,
    'десять': 10,
}


@dataclass(frozen=True)
class ConversionRequest:
    amount: float
    from_unit: str
    to_unit: str


class TextNormalizer:
    _NUMBER_WORDS = WORD_NUM_RU
    _WORD_BOUNDARY_REPLACEMENTS = (
        ('умножить на', '*'),
        ('разделить на', '/'),
        ('плюс', '+'),
        ('минус', '-'),
    )
    _CROSS_PATTERN = re.compile(r'(?<=\d)\s*[xх]\s*(?=\d)', flags=re.UNICODE)

    def normalize(self, text: str) -> str:
        value = str(text or '').lower().strip()
        value = value.replace('−', '-')
        for src, dst in self._WORD_BOUNDARY_REPLACEMENTS:
            value = value.replace(src, dst)
        value = self._CROSS_PATTERN.sub('*', value)
        for word, number in self._NUMBER_WORDS.items():
            value = re.sub(rf'\b{word}\b', str(number), value)
        return value


class NumberFormatter:
    def format(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        text = f'{float(value):.6f}'.rstrip('0').rstrip('.')
        return '0' if text in ('', '-0') else text


class SafeArithmeticEvaluator:
    _VALID_CHARS = re.compile(r'^[0-9+\-*/().,\s]+$')
    _BIN_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }
    _UNARY_OPS = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    def evaluate(self, expression: str) -> Optional[float]:
        source = str(expression or '').strip()
        if not source:
            return None
        if not self._VALID_CHARS.fullmatch(source):
            return None
        source = source.replace(',', '.')
        if not re.search(r'\d', source):
            return None
        try:
            tree = ast.parse(source, mode='eval')
        except Exception:
            return None
        try:
            return float(self._eval_node(tree.body))
        except Exception:
            return None

    def _eval_node(self, node: ast.AST) -> float:
        if isinstance(node, ast.BinOp):
            op = self._BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError('unsupported binary operator')
            return float(op(self._eval_node(node.left), self._eval_node(node.right)))
        if isinstance(node, ast.UnaryOp):
            op = self._UNARY_OPS.get(type(node.op))
            if op is None:
                raise ValueError('unsupported unary operator')
            return float(op(self._eval_node(node.operand)))
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError('bool is not allowed')
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError('unsupported constant')
        if isinstance(node, ast.Num):  # pragma: no cover - py<3.8 compatibility
            return float(node.n)
        raise ValueError('unsupported expression node')


class UnitCatalog:
    _TOKEN_SANITIZER = re.compile(r'[^a-zа-яё]+', flags=re.UNICODE)
    _LENGTH_FACTORS = {
        'mm': 0.001,
        'cm': 0.01,
        'm': 1.0,
        'km': 1000.0,
        'in': 0.0254,
        'ft': 0.3048,
        'yd': 0.9144,
        'mi': 1609.344,
    }
    _WEIGHT_FACTORS = {
        'mg': 0.000001,
        'g': 0.001,
        'kg': 1.0,
        't': 1000.0,
        'oz': 0.028349523125,
        'lb': 0.45359237,
    }
    _TIME_FACTORS = {
        's': 1.0,
        'min': 60.0,
        'h': 3600.0,
        'day': 86400.0,
        'week': 604800.0,
    }
    _TEMP_UNITS = {'c', 'f', 'k'}
    _UNIT_DISPLAY = {
        'mm': 'mm',
        'cm': 'cm',
        'm': 'm',
        'km': 'km',
        'in': 'in',
        'ft': 'ft',
        'yd': 'yd',
        'mi': 'mi',
        'mg': 'mg',
        'g': 'g',
        'kg': 'kg',
        't': 't',
        'oz': 'oz',
        'lb': 'lb',
        's': 'sec',
        'min': 'min',
        'h': 'h',
        'day': 'day',
        'week': 'week',
        'c': '°C',
        'f': '°F',
        'k': 'K',
    }
    _EXACT_ALIASES = {
        'мм': 'mm',
        'mm': 'mm',
        'см': 'cm',
        'cm': 'cm',
        'м': 'm',
        'm': 'm',
        'км': 'km',
        'km': 'km',
        'in': 'in',
        'ft': 'ft',
        'feet': 'ft',
        'yd': 'yd',
        'mi': 'mi',
        'мг': 'mg',
        'mg': 'mg',
        'г': 'g',
        'гр': 'g',
        'g': 'g',
        'кг': 'kg',
        'kg': 'kg',
        'т': 't',
        't': 't',
        'lb': 'lb',
        'lbs': 'lb',
        'oz': 'oz',
        'с': 's',
        'сек': 's',
        'sec': 's',
        's': 's',
        'мин': 'min',
        'min': 'min',
        'ч': 'h',
        'h': 'h',
        'hr': 'h',
        'day': 'day',
        'days': 'day',
        'week': 'week',
        'weeks': 'week',
        'c': 'c',
        'f': 'f',
        'k': 'k',
        'celsius': 'c',
        'celcius': 'c',
        'fahrenheit': 'f',
        'kelvin': 'k',
    }
    _PREFIX_ALIASES: Sequence[Tuple[str, str]] = (
        ('миллиметр', 'mm'),
        ('millimeter', 'mm'),
        ('сантиметр', 'cm'),
        ('centimeter', 'cm'),
        ('метр', 'm'),
        ('meter', 'm'),
        ('километр', 'km'),
        ('kilometer', 'km'),
        ('дюйм', 'in'),
        ('inch', 'in'),
        ('фут', 'ft'),
        ('foot', 'ft'),
        ('ярд', 'yd'),
        ('yard', 'yd'),
        ('мил', 'mi'),
        ('mile', 'mi'),
        ('миллиграм', 'mg'),
        ('milligram', 'mg'),
        ('грам', 'g'),
        ('gram', 'g'),
        ('килограмм', 'kg'),
        ('kilogram', 'kg'),
        ('тон', 't'),
        ('ton', 't'),
        ('фунт', 'lb'),
        ('pound', 'lb'),
        ('унц', 'oz'),
        ('ounce', 'oz'),
        ('секунд', 's'),
        ('second', 's'),
        ('минут', 'min'),
        ('minute', 'min'),
        ('час', 'h'),
        ('hour', 'h'),
        ('дн', 'day'),
        ('недел', 'week'),
        ('цельси', 'c'),
        ('фаренг', 'f'),
        ('кельвин', 'k'),
    )

    def resolve(self, raw: str) -> Optional[str]:
        token = self._normalize_token(raw)
        if not token:
            return None
        exact = self._EXACT_ALIASES.get(token)
        if exact is not None:
            return exact
        for prefix, code in self._PREFIX_ALIASES:
            if token.startswith(prefix):
                return code
        return None

    def category(self, unit: str) -> str:
        if unit in self._LENGTH_FACTORS:
            return 'length'
        if unit in self._WEIGHT_FACTORS:
            return 'weight'
        if unit in self._TIME_FACTORS:
            return 'time'
        if unit in self._TEMP_UNITS:
            return 'temp'
        return ''

    def factors(self, category: str) -> Dict[str, float]:
        if category == 'length':
            return self._LENGTH_FACTORS
        if category == 'weight':
            return self._WEIGHT_FACTORS
        if category == 'time':
            return self._TIME_FACTORS
        return {}

    def is_temperature(self, unit: str) -> bool:
        return unit in self._TEMP_UNITS

    def display(self, unit: str) -> str:
        return self._UNIT_DISPLAY.get(unit, unit)

    def _normalize_token(self, raw: str) -> str:
        token = str(raw or '').strip().lower().replace('°', '')
        return self._TOKEN_SANITIZER.sub('', token)


class UnitConverter:
    def __init__(self, catalog: UnitCatalog) -> None:
        self._catalog = catalog

    def compatible(self, from_unit: str, to_unit: str) -> bool:
        return self._catalog.category(from_unit) == self._catalog.category(to_unit)

    def convert(self, amount: float, from_unit: str, to_unit: str) -> Optional[float]:
        from_category = self._catalog.category(from_unit)
        to_category = self._catalog.category(to_unit)
        if not from_category or from_category != to_category:
            return None
        if from_unit == to_unit:
            return amount
        if from_category in ('length', 'weight', 'time'):
            factors = self._catalog.factors(from_category)
            base = amount * float(factors[from_unit])
            return base / float(factors[to_unit])
        return self._convert_temperature(amount, from_unit, to_unit)

    def _convert_temperature(self, amount: float, from_unit: str, to_unit: str) -> Optional[float]:
        if not self._catalog.is_temperature(from_unit) or not self._catalog.is_temperature(to_unit):
            return None
        celsius = amount
        if from_unit == 'f':
            celsius = (amount - 32.0) * 5.0 / 9.0
        elif from_unit == 'k':
            celsius = amount - 273.15

        if to_unit == 'c':
            return celsius
        if to_unit == 'f':
            return celsius * 9.0 / 5.0 + 32.0
        return celsius + 273.15


class ConversionParser:
    _NUMBER_PATTERN = re.compile(r'[-+]?\d+(?:[.,]\d+)?')
    _TOKEN_PATTERN = re.compile(r'[A-Za-zА-Яа-яЁё°]+', flags=re.UNICODE)
    _QUESTION_PATTERN = re.compile(r'\b(сколько|how much|how many)\b', flags=re.UNICODE)
    _CONNECTOR_PATTERN = re.compile(r'\b(в|to|in)\b', flags=re.UNICODE)
    _CONVERT_KEYWORDS = ('конвертируй', 'переведи', 'convert')

    def __init__(self, normalizer: TextNormalizer, catalog: UnitCatalog, converter: UnitConverter) -> None:
        self._normalizer = normalizer
        self._catalog = catalog
        self._converter = converter

    def parse_payload(self, data: Any) -> Optional[ConversionRequest]:
        if not isinstance(data, dict):
            return None
        amount_raw = data.get('amount')
        from_raw = data.get('from')
        to_raw = data.get('to')
        if amount_raw is None or from_raw is None or to_raw is None:
            return None
        amount = self._to_float(amount_raw)
        from_unit = self._catalog.resolve(str(from_raw))
        to_unit = self._catalog.resolve(str(to_raw))
        if amount is None or from_unit is None or to_unit is None:
            return None
        if from_unit != to_unit and not self._converter.compatible(from_unit, to_unit):
            return None
        return ConversionRequest(amount=amount, from_unit=from_unit, to_unit=to_unit)

    def parse_text(self, text: str) -> Optional[ConversionRequest]:
        lowered = self._normalizer.normalize(text)
        amount = self._extract_amount(lowered)
        if amount is None:
            return None

        units = self._extract_units_with_positions(lowered)
        if len(units) < 2:
            return None

        connector_pos = self._find_connector_position(lowered)
        from_unit, to_unit = self._pick_units(lowered, units, connector_pos)
        if from_unit is None or to_unit is None:
            return None
        if from_unit != to_unit and not self._converter.compatible(from_unit, to_unit):
            return None
        return ConversionRequest(amount=amount, from_unit=from_unit, to_unit=to_unit)

    def has_intent(self, text: str) -> bool:
        lowered = self._normalizer.normalize(text)
        units = self._extract_units_with_positions(lowered)
        if len(units) >= 2:
            return True
        return any(keyword in lowered for keyword in self._CONVERT_KEYWORDS)

    def looks_like_unit_query(self, text: str) -> bool:
        lowered = self._normalizer.normalize(text)
        return len(self._extract_units_with_positions(lowered)) >= 2

    def _extract_amount(self, lowered: str) -> Optional[float]:
        number_match = self._NUMBER_PATTERN.search(lowered)
        if number_match:
            return self._to_float(number_match.group(0))
        if self._QUESTION_PATTERN.search(lowered):
            return 1.0
        return None

    def _pick_units(
        self,
        lowered: str,
        units: List[Tuple[int, str]],
        connector_pos: int,
    ) -> Tuple[Optional[str], Optional[str]]:
        from_unit = None
        to_unit = None
        is_question = bool(self._QUESTION_PATTERN.search(lowered))
        if connector_pos >= 0:
            before = [item for item in units if item[0] < connector_pos]
            after = [item for item in units if item[0] > connector_pos]
            if before and after:
                if is_question:
                    to_unit = before[-1][1]
                    from_unit = after[0][1]
                else:
                    from_unit = before[-1][1]
                    to_unit = after[0][1]
        if from_unit is None or to_unit is None:
            if is_question:
                to_unit = units[0][1]
                from_unit = units[1][1]
            else:
                from_unit = units[0][1]
                to_unit = units[1][1]
        return from_unit, to_unit

    def _extract_units_with_positions(self, text: str) -> List[Tuple[int, str]]:
        out: List[Tuple[int, str]] = []
        for match in self._TOKEN_PATTERN.finditer(text):
            resolved = self._catalog.resolve(match.group(0))
            if resolved is None:
                continue
            out.append((match.start(), resolved))
        return out

    def _find_connector_position(self, text: str) -> int:
        match = self._CONNECTOR_PATTERN.search(text)
        return match.start() if match else -1

    def _to_float(self, value: Any) -> Optional[float]:
        try:
            return float(str(value).strip().replace(',', '.'))
        except Exception:
            return None


class CalculatorEngine:
    _PERCENT_PATTERN = re.compile(
        r'(-?\d+(?:[.,]\d+)?)\s*(?:%|процент(?:а|ов)?)\s*(?:от|of)\s*(-?\d+(?:[.,]\d+)?)',
        flags=re.UNICODE,
    )
    _EXPRESSION_ALLOWED_CHARS = set('0123456789+-*/()., ')

    def __init__(self) -> None:
        self._normalizer = TextNormalizer()
        self._formatter = NumberFormatter()
        self._catalog = UnitCatalog()
        self._converter = UnitConverter(self._catalog)
        self._conversion_parser = ConversionParser(self._normalizer, self._catalog, self._converter)
        self._arithmetic = SafeArithmeticEvaluator()

    def evaluate(self, data: Any) -> Optional[str]:
        conversion_payload = self._conversion_parser.parse_payload(data)
        if conversion_payload is not None:
            return self._evaluate_conversion(conversion_payload)

        text = self.extract_text(data)
        if not text:
            return None

        conversion_text = self._conversion_parser.parse_text(text)
        if conversion_text is not None:
            return self._evaluate_conversion(conversion_text)
        if self._conversion_parser.has_intent(text):
            return None

        normalized = self._normalizer.normalize(text)
        percent = self._evaluate_percentage(normalized)
        if percent is not None:
            return self._formatter.format(percent)

        expression = ''.join(ch for ch in normalized if ch in self._EXPRESSION_ALLOWED_CHARS).strip()
        value = self._arithmetic.evaluate(expression)
        if value is None:
            return None
        return self._formatter.format(value)

    def extract_text(self, data: Any) -> str:
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            value = data.get('value') or data.get('text') or data.get('msgData')
            return str(value).strip() if value is not None else ''
        return ''

    def can_route_conversion(self, data: Any) -> bool:
        if self._conversion_parser.parse_payload(data) is not None:
            return True

        text = self.extract_text(data)
        if not text:
            return False
        if self._conversion_parser.parse_text(text) is not None:
            return True
        return self._conversion_parser.looks_like_unit_query(text)

    def _evaluate_conversion(self, request: ConversionRequest) -> Optional[str]:
        value = self._converter.convert(request.amount, request.from_unit, request.to_unit)
        if value is None:
            return None
        return f'{self._formatter.format(value)} {self._catalog.display(request.to_unit)}'

    def _evaluate_percentage(self, text: str) -> Optional[float]:
        match = self._PERCENT_PATTERN.search(text)
        if not match:
            return None
        left = self._to_float(match.group(1))
        right = self._to_float(match.group(2))
        if left is None or right is None:
            return None
        return right * left / 100.0

    def _to_float(self, value: Any) -> Optional[float]:
        try:
            return float(str(value).strip().replace(',', '.'))
        except Exception:
            return None


class CalculatorPlugin(MinaChanPlugin):
    _SPEECH_RULES = (
        'посчитай',
        'вычисли',
        'сколько будет',
        'калькулятор',
        r'regex:^\s*[-+]?\d+(?:[.,]\d+)?(?:\s*[+\-*/xх]\s*[-+]?\d+(?:[.,]\d+)?)+\s*$',
        r'regex:^\s*[-+]?\d+(?:[.,]\d+)?\s*(?:%|процент(?:а|ов)?)\s*(?:от|of)\s*[-+]?\d+(?:[.,]\d+)?\s*$',
    )
    _SPEECH_RULES_EN = (
        'calculate',
        'calculator',
        'how much is',
        r'regex:^\s*[-+]?\d+(?:[.,]\d+)?(?:\s*[+\-*/x]\s*[-+]?\d+(?:[.,]\d+)?)+\s*$',
        r'regex:^\s*[-+]?\d+(?:[.,]\d+)?\s*(?:%|percent)\s*(?:of)\s*[-+]?\d+(?:[.,]\d+)?\s*$',
    )

    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._speech_rules: List[str] = []
        self._engine = CalculatorEngine()

    def on_init(self) -> None:
        self.add_listener('calculator:eval', self.on_eval, listener_id='on_eval')
        self.add_listener(
            CMD_ROUTE_CONVERSION,
            self.on_route_conversion,
            listener_id='calculator_route_conversion',
        )
        self.register_command(
            'calculator:eval',
            'Evaluate math expression or convert units',
        )
        self.set_alternative(
            CONVERSION_QUERY_TAG,
            CMD_ROUTE_CONVERSION,
            CONVERSION_ROUTE_PRIORITY,
        )
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

    def on_unload(self) -> None:
        self._clear_speech_links()

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        if locale != self._ui_locale or not self._speech_rules:
            self._ui_locale = locale
            self._sync_speech_links()

    def on_eval(self, sender: str, data, tag: str) -> None:
        text = self._engine.extract_text(data)
        if not text and not isinstance(data, dict):
            return
        result = self._engine.evaluate(data)
        if result is None:
            self.request_say_intent('CALC_ERROR')
            return
        self.request_say_direct(result)

    def on_route_conversion(self, sender: str, data: Any, tag: str) -> None:
        if not self._engine.can_route_conversion(data):
            self.call_next_alternative(
                sender,
                CONVERSION_QUERY_TAG,
                CMD_ROUTE_CONVERSION,
                data,
            )
            return
        self.on_eval(sender, data, 'calculator:eval')

    def _speech_rules_for_locale(self) -> List[str]:
        if self._is_ru_locale():
            return list(self._SPEECH_RULES)
        return list(self._SPEECH_RULES_EN)

    def _sync_speech_links(self) -> None:
        new_rules = self._speech_rules_for_locale()
        if new_rules == self._speech_rules:
            return
        self._clear_speech_links()
        for rule in new_rules:
            self.register_speech_rule('calculator:eval', rule)
        self._speech_rules = new_rules

    def _clear_speech_links(self) -> None:
        for rule in self._speech_rules:
            self.remove_event_link(
                'speech:get',
                'calculator:eval',
                rule=rule,
            )
        self._speech_rules = []

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')


if __name__ == '__main__':
    run_plugin(CalculatorPlugin)
