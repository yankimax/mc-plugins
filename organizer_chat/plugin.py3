#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


class OrganizerChatPlugin(MinaChanPlugin):
    CMD_LIST = 'organizer-chat:list'
    CMD_NEXT = 'organizer-chat:next'
    CMD_CREATE = 'organizer-chat:create'

    _PERIOD_ALIASES = {
        'today': 'today',
        'сегодня': 'today',
        'день': 'today',
        'week': 'week',
        'неделя': 'week',
        'неделю': 'week',
        'недели': 'week',
        'month': 'month',
        'месяц': 'month',
        'месяца': 'month',
        'месяце': 'month',
        'three_days': 'three_days',
        '3 дня': 'three_days',
        '3дня': 'three_days',
        'три дня': 'three_days',
    }
    _PRIORITY_PATTERNS: List[Tuple[str, str]] = [
        ('critical', r'\b(критич\w*|critical)\b'),
        ('high', r'\b(срочно|срочн\w*|важно|высок\w*|high)\b'),
        ('normal', r'\b(обычно|нормальн\w*|средн\w*|normal)\b'),
        ('low', r'\b(низк\w*|low)\b'),
    ]
    _STATE_PATTERNS: List[Tuple[str, str]] = [
        ('in_progress', r'\b(в\s+работе|делаю|делается|in\s*progress|started)\b'),
        ('done', r'\b(сделано|выполнено|готово|закрыто|done|complete[sd]?)\b'),
        ('planned', r'\b(заплан\w*|planned|todo|to\s*do)\b'),
        ('canceled', r'\b(отмен\w*|cancel+ed?)\b'),
    ]
    _WEEKDAY_PATTERNS: List[Tuple[int, str]] = [
        (0, r'\bпонедельник(?:а|у)?\b'),
        (1, r'\bвторник(?:а|у)?\b'),
        (2, r'\bсред(?:а|у|ы)\b'),
        (3, r'\bчетверг(?:а|у)?\b'),
        (4, r'\bпятниц(?:а|у|ы)\b'),
        (5, r'\bсуббот(?:а|у|ы)\b'),
        (6, r'\bвоскресень(?:е|я)\b'),
    ]
    _TEMPORAL_SPAN_PATTERNS: Tuple[str, ...] = (
        r'через\s+\d+\s*(минут|мин|часов|часа|час|дней|дня|дн|неделю|недели|недел)',
        r'\b(?:на\s+)?(сегодня|завтра|послезавтра)\b(?:\s+\d{1,2}:\d{2})?',
        r'\b(?:на|в)\s+(?:(?:следующ\w*|эт\w*)\s+)?'
        r'(понедельник(?:а|у)?|вторник(?:а|у)?|сред(?:а|у|ы)|четверг(?:а|у)?|пятниц(?:а|у|ы)|суббот(?:а|у|ы)|воскресень(?:е|я))'
        r'\b(?:\s+\d{1,2}:\d{2})?',
        r'\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?(?:\s+\d{1,2}:\d{2})?\b',
        r'\b\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?\b',
        r'\b\d{1,2}:\d{2}\b',
    )

    def on_init(self) -> None:
        self.add_listener(self.CMD_LIST, self.on_list, listener_id='organizer_chat_list')
        self.add_listener(self.CMD_NEXT, self.on_next, listener_id='organizer_chat_next')
        self.add_listener(self.CMD_CREATE, self.on_create, listener_id='organizer_chat_create')

        self.register_command(
            self.CMD_LIST,
            {
                'en': 'List unfinished tasks by period (today/week/month/three days)',
                'ru': 'Показать невыполненные задачи на период (сегодня/неделя/месяц/три дня)',
            },
            {
                'period': 'today | week | month | three_days',
                'text': 'Natural phrase, e.g. "какие задачи на сегодня"',
            },
        )
        self.register_command(
            self.CMD_NEXT,
            {
                'en': 'Suggest what to do next from organizer',
                'ru': 'Подсказать следующую задачу из органайзера',
            },
        )
        self.register_command(
            self.CMD_CREATE,
            {
                'en': 'Create organizer task from chat',
                'ru': 'Создать задачу органайзера из чата',
            },
            {
                'title': 'Task title',
                'text': 'Natural phrase, e.g. "создай задачу Купить молоко"',
            },
        )

        self.register_speech_rule(self.CMD_LIST, {'ru': 'какие задачи на {period:String}'})
        self.register_speech_rule(self.CMD_LIST, {'ru': 'покажи задачи на {period:String}'})
        self.register_speech_rule(self.CMD_LIST, {'ru': 'какие задачи за {period:String}'})
        self.register_speech_rule(self.CMD_NEXT, {'ru': 'что делать дальше'})
        self.register_speech_rule(self.CMD_CREATE, {'ru': 'создай задачу {title:String}'})
        self.register_speech_rule(self.CMD_CREATE, {'ru': 'добавь задачу {title:String}'})

    def on_list(self, sender: str, data: Any, tag: str) -> None:
        try:
            period = self._resolve_period(data)
        except Exception as error:
            self.log(f'organizer_chat on_list parse error: {error}')
            self._say('Не смогла разобрать запрос на список задач.')
            self._reply(sender, {'ok': False, 'error': 'list_parse_error'})
            return
        if not period:
            self._say(
                'Не поняла период. Доступно: сегодня, неделя, месяц, три дня.',
            )
            self._reply(sender, {'ok': False, 'error': 'unknown_period'})
            return

        now = self._now()
        start_ms, end_ms, title = self._period_window(period, now)
        payload = {
            'includeTerminal': False,
            'hasDue': True,
            'sort': 'due_asc',
            'limit': 200,
            'dueFromMs': start_ms,
            'dueToMs': end_ms,
        }

        def _on_response(response: Dict[str, Any]) -> None:
            if not response.get('ok'):
                message = f'Не смогла получить задачи: {self._format_error(response)}'
                self._say(message)
                self._reply(sender, {'ok': False, 'error': self._format_error(response)})
                return

            items = self._as_items(response.get('items'))
            if not items:
                self._say(f'На период "{title}" невыполненных задач со сроком нет.')
                self._reply(sender, {'ok': True, 'period': period, 'count': 0, 'items': []})
                return

            text = self._format_task_list(title, items, total=int(response.get('total') or len(items)))
            self._say(text)
            self._reply(
                sender,
                {
                    'ok': True,
                    'period': period,
                    'count': len(items),
                    'total': int(response.get('total') or len(items)),
                    'items': items,
                },
            )

        self._request_core('organizer-core:list-items', payload, _on_response)

    def on_next(self, sender: str, data: Any, tag: str) -> None:
        try:
            now = self._now()
        except Exception as error:
            self.log(f'organizer_chat on_next time error: {error}')
            self._say('Не смогла определить следующую задачу.')
            self._reply(sender, {'ok': False, 'error': 'next_parse_error'})
            return
        start_today = datetime(year=now.year, month=now.month, day=now.day)
        end_today = start_today + timedelta(days=1) - timedelta(milliseconds=1)

        today_payload = {
            'includeTerminal': False,
            'hasDue': True,
            'sort': 'due_asc',
            'limit': 1,
            'dueFromMs': self._to_ms(start_today),
            'dueToMs': self._to_ms(end_today),
        }

        def _on_today(response: Dict[str, Any]) -> None:
            if not response.get('ok'):
                message = f'Не смогла получить следующую задачу: {self._format_error(response)}'
                self._say(message)
                self._reply(sender, {'ok': False, 'error': self._format_error(response)})
                return

            items = self._as_items(response.get('items'))
            if items:
                item = items[0]
                self._say(f'Дальше: {self._format_single_task(item)}')
                self._reply(sender, {'ok': True, 'source': 'today_due', 'item': item})
                return

            fallback_payload = {
                'includeTerminal': False,
                'hasDue': False,
                'sort': 'created_asc',
                'limit': 1,
            }

            def _on_fallback(fallback_response: Dict[str, Any]) -> None:
                if not fallback_response.get('ok'):
                    message = f'Не смогла получить следующую задачу: {self._format_error(fallback_response)}'
                    self._say(message)
                    self._reply(sender, {'ok': False, 'error': self._format_error(fallback_response)})
                    return

                fallback_items = self._as_items(fallback_response.get('items'))
                if fallback_items:
                    item = fallback_items[0]
                    self._say(f'Дальше: {self._format_single_task(item)}')
                    self._reply(sender, {'ok': True, 'source': 'no_due', 'item': item})
                    return

                self._say('Сейчас нет невыполненных задач. Можно создать новую.')
                self._reply(sender, {'ok': True, 'source': 'empty'})

            self._request_core('organizer-core:list-items', fallback_payload, _on_fallback)

        self._request_core('organizer-core:list-items', today_payload, _on_today)

    def on_create(self, sender: str, data: Any, tag: str) -> None:
        try:
            create_payload = self._build_create_payload_from_chat(data)
        except Exception as error:
            self.log(f'organizer_chat on_create parse error: {error}')
            fallback_title = self._normalize_title(self._extract_title(data))
            create_payload = {'title': fallback_title or 'Новая задача'}
        title = str(create_payload.get('title') or '').strip()
        if not title:
            self._say('Нужно название задачи. Пример: "создай задачу Купить молоко".')
            self._reply(sender, {'ok': False, 'error': 'empty_title'})
            return

        def _on_response(response: Dict[str, Any]) -> None:
            if not response.get('ok'):
                message = f'Не удалось создать задачу: {self._format_error(response)}'
                self._say(message)
                self._reply(sender, {'ok': False, 'error': self._format_error(response)})
                return

            item = self._as_map(response.get('item'))
            item_id = self._int(item.get('id'), 0)
            created_title = str(item.get('title') or title).strip()
            suffix = f' #{item_id}' if item_id > 0 else ''
            self._say(f'Задача{suffix} создана: {created_title}.')
            self._reply(sender, {'ok': True, 'item': item})

        self._request_core('organizer-core:create-item', create_payload, _on_response)

    def _request_core(self, command: str, payload: Dict[str, Any], callback: Callable[[Dict[str, Any]], None]) -> None:
        responded = {'value': False}

        def _on_response(sender: str, data: Any, tag: str) -> None:
            if responded['value']:
                return
            responded['value'] = True
            if isinstance(data, dict):
                callback(dict(data))
                return
            callback({'ok': False, 'error': {'message': f'Invalid response for {command}'}})

        def _on_complete(sender: str, data: Any, tag: str) -> None:
            if responded['value']:
                return
            responded['value'] = True
            callback({'ok': False, 'error': {'message': f'No response for {command}'}})

        seq = self.send_message_with_response(
            command,
            payload,
            on_response=_on_response,
            on_complete=_on_complete,
        )
        if seq < 0 and not responded['value']:
            responded['value'] = True
            callback({'ok': False, 'error': {'message': f'Failed to send {command}'}})

    def _resolve_period(self, data: Any) -> Optional[str]:
        payload = self._as_map(data)
        explicit = str(payload.get('period') or '').strip()
        if explicit:
            normalized = self._normalize_period_text(explicit)
            if normalized:
                return normalized

        text = self._extract_text(data)
        return self._normalize_period_text(text)

    def _normalize_period_text(self, raw: str) -> Optional[str]:
        text = str(raw or '').strip().lower()
        if not text:
            return None

        if text in self._PERIOD_ALIASES:
            return self._PERIOD_ALIASES[text]

        compact = re.sub(r'\s+', ' ', text)
        if compact in self._PERIOD_ALIASES:
            return self._PERIOD_ALIASES[compact]

        if 'сегодня' in compact:
            return 'today'
        if 'недел' in compact:
            return 'week'
        if 'меся' in compact:
            return 'month'
        if 'три дня' in compact or re.search(r'\b3\s*дн', compact):
            return 'three_days'
        return None

    def _period_window(self, period: str, now: datetime) -> Tuple[int, int, str]:
        start_today = datetime(year=now.year, month=now.month, day=now.day)

        if period == 'today':
            end = start_today + timedelta(days=1) - timedelta(milliseconds=1)
            return self._to_ms(start_today), self._to_ms(end), 'Сегодня'

        if period == 'three_days':
            end = start_today + timedelta(days=3) - timedelta(milliseconds=1)
            return self._to_ms(start_today), self._to_ms(end), 'Три дня'

        if period == 'week':
            week_start = start_today - timedelta(days=start_today.weekday())
            week_end = week_start + timedelta(days=7) - timedelta(milliseconds=1)
            return self._to_ms(week_start), self._to_ms(week_end), 'Текущая неделя'

        if period == 'month':
            month_start = datetime(year=now.year, month=now.month, day=1)
            if now.month == 12:
                next_month = datetime(year=now.year + 1, month=1, day=1)
            else:
                next_month = datetime(year=now.year, month=now.month + 1, day=1)
            month_end = next_month - timedelta(milliseconds=1)
            return self._to_ms(month_start), self._to_ms(month_end), 'Текущий месяц'

        end = start_today + timedelta(days=1) - timedelta(milliseconds=1)
        return self._to_ms(start_today), self._to_ms(end), 'Сегодня'

    def _extract_title(self, data: Any) -> str:
        payload = self._as_map(data)
        for key in ('title', 'task', 'name'):
            value = str(payload.get(key) or '').strip()
            if value:
                return value

        text = self._extract_text(data)
        if not text:
            return ''
        lowered = text.lower()
        match = re.search(r'(создай|добавь)\s+задач[ауи]?\s+(.+)$', lowered, flags=re.IGNORECASE)
        if match:
            original_match = re.search(r'(создай|добавь)\s+задач[ауи]?\s+(.+)$', text, flags=re.IGNORECASE)
            if original_match:
                return str(original_match.group(2) or '').strip()
        return text.strip()

    def _build_create_payload_from_chat(self, data: Any) -> Dict[str, Any]:
        payload = self._as_map(data)
        text = self._extract_text(data)
        now = self._now()

        title = self._extract_title(data)
        title = self._strip_create_prefix(title)

        parsed_priority, priority_spans = self._extract_priority(text)
        parsed_state, state_spans = self._extract_state(text)
        due_ms, due_spans = self._extract_due_ms(text, now)
        start_ms, start_spans = self._extract_start_ms(text, now)

        explicit_priority = str(payload.get('priority') or '').strip()
        explicit_state = str(payload.get('state') or '').strip()
        explicit_due = self._int_or_none(payload.get('dueAtMs'))
        explicit_start = self._int_or_none(payload.get('startAtMs'))

        priority = explicit_priority or parsed_priority
        state = explicit_state or parsed_state
        due_at_ms = explicit_due if explicit_due is not None else due_ms
        start_at_ms = explicit_start if explicit_start is not None else start_ms

        title_cleanup_spans = priority_spans + state_spans + due_spans + start_spans
        if due_at_ms is not None or start_at_ms is not None:
            title_cleanup_spans.extend(self._find_all_temporal_spans(text))
        if text:
            candidate = self._strip_by_spans(text, title_cleanup_spans)
            candidate = self._strip_create_prefix(candidate)
            if candidate:
                title = candidate
        title = self._normalize_title(title)

        out: Dict[str, Any] = {'title': title or 'Новая задача'}
        if priority:
            out['priority'] = priority
        if state:
            out['state'] = state
        if due_at_ms is not None:
            out['dueAtMs'] = due_at_ms
        if start_at_ms is not None:
            out['startAtMs'] = start_at_ms
        return out

    def _extract_priority(self, text: str) -> Tuple[str, List[Tuple[int, int]]]:
        source = str(text or '')
        lowered = source.lower()
        spans: List[Tuple[int, int]] = []
        for value, pattern in self._PRIORITY_PATTERNS:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                spans.append((match.start(), match.end()))
                return value, spans
        return '', spans

    def _extract_state(self, text: str) -> Tuple[str, List[Tuple[int, int]]]:
        source = str(text or '')
        lowered = source.lower()
        spans: List[Tuple[int, int]] = []
        for value, pattern in self._STATE_PATTERNS:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                spans.append((match.start(), match.end()))
                return value, spans
        return '', spans

    def _extract_due_ms(self, text: str, now: datetime) -> Tuple[Optional[int], List[Tuple[int, int]]]:
        source = str(text or '')
        lowered = source.lower()
        spans: List[Tuple[int, int]] = []
        for pattern in (
            r'\bдо\s+([^,;]+)',
            r'\bдедлайн\s+([^,;]+)',
            r'\bк\s+сроку\s+([^,;]+)',
            r'\bсрок\s+([^,;]+)',
            r'\bс\s+дат[ао]й?\s+исполнени[яе]\s+([^,;]+)',
            r'\bдата\s+исполнени[яе]\s+([^,;]+)',
        ):
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if not match:
                continue
            fragment = source[match.start(1):match.end(1)].strip()
            parsed = self._parse_datetime_fragment(fragment, now, default_hour=18, default_minute=0)
            if parsed is None:
                continue
            spans.append((match.start(), match.end()))
            return self._to_ms(parsed), spans
        parsed_any = self._parse_datetime_fragment(source, now, default_hour=18, default_minute=0)
        if parsed_any is not None:
            temporal_span = self._find_temporal_span(lowered)
            if temporal_span is not None:
                spans.append(temporal_span)
            return self._to_ms(parsed_any), spans
        return None, spans

    def _find_temporal_span(self, text: str) -> Optional[Tuple[int, int]]:
        for pattern in self._TEMPORAL_SPAN_PATTERNS:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return (match.start(), match.end())
        return None

    def _find_all_temporal_spans(self, text: str) -> List[Tuple[int, int]]:
        source = str(text or '')
        spans: List[Tuple[int, int]] = []
        if not source:
            return spans
        for pattern in self._TEMPORAL_SPAN_PATTERNS:
            for match in re.finditer(pattern, source, flags=re.IGNORECASE):
                spans.append((match.start(), match.end()))
        return spans

    def _extract_start_ms(self, text: str, now: datetime) -> Tuple[Optional[int], List[Tuple[int, int]]]:
        source = str(text or '')
        lowered = source.lower()
        spans: List[Tuple[int, int]] = []
        for pattern in (
            r'\bнач(ать|ни)\s+([^,;]+)',
            r'\bстарт\s+([^,;]+)',
            r'\bстартовать\s+([^,;]+)',
            r'\bначало\s+([^,;]+)',
        ):
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if not match:
                continue
            group_index = 2 if match.lastindex and match.lastindex >= 2 else 1
            fragment = source[match.start(group_index):match.end(group_index)].strip()
            parsed = self._parse_datetime_fragment(fragment, now, default_hour=9, default_minute=0)
            if parsed is None:
                continue
            spans.append((match.start(), match.end()))
            return self._to_ms(parsed), spans
        return None, spans

    def _parse_datetime_fragment(
        self,
        fragment: str,
        now: datetime,
        default_hour: int,
        default_minute: int,
    ) -> Optional[datetime]:
        text = str(fragment or '').strip().lower()
        if not text:
            return None

        rel = re.search(
            r'через\s+(\d+)\s*(минут|мин|часов|часа|час|дней|дня|дн|неделю|недели|недел)',
            text,
        )
        if rel:
            amount = self._int(rel.group(1), 0)
            unit = str(rel.group(2) or '').lower()
            if amount > 0:
                if unit.startswith('мин'):
                    return now + timedelta(minutes=amount)
                if unit.startswith('час'):
                    return now + timedelta(hours=amount)
                if unit.startswith('нед'):
                    return now + timedelta(days=7 * amount)
                return now + timedelta(days=amount)

        base_day: Optional[datetime] = None
        if 'послезавтра' in text:
            base_day = datetime(now.year, now.month, now.day) + timedelta(days=2)
        elif 'завтра' in text:
            base_day = datetime(now.year, now.month, now.day) + timedelta(days=1)
        elif 'сегодня' in text:
            base_day = datetime(now.year, now.month, now.day)
        else:
            weekday_target = self._extract_weekday_target(text)
            if weekday_target is not None:
                base_day = self._next_weekday(datetime(now.year, now.month, now.day), weekday_target)
                if re.search(r'\bследующ\w*\b', text, flags=re.IGNORECASE):
                    base_day = base_day + timedelta(days=7)

        absolute = re.search(
            r'\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?(?:\s+(\d{1,2}):(\d{2}))?\b',
            text,
        )
        if absolute:
            day = self._int(absolute.group(1), 1)
            month = self._int(absolute.group(2), 1)
            year = self._int(absolute.group(3), now.year) if absolute.group(3) else now.year
            if year < 100:
                year = 2000 + year
            hour = self._int(absolute.group(4), default_hour)
            minute = self._int(absolute.group(5), default_minute)
            try:
                return datetime(year, month, day, hour, minute)
            except Exception:
                return None

        iso = re.search(r'\b(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?\b', text)
        if iso:
            year = self._int(iso.group(1), now.year)
            month = self._int(iso.group(2), now.month)
            day = self._int(iso.group(3), now.day)
            hour = self._int(iso.group(4), default_hour)
            minute = self._int(iso.group(5), default_minute)
            try:
                return datetime(year, month, day, hour, minute)
            except Exception:
                return None

        only_time = re.search(r'\b(\d{1,2}):(\d{2})\b', text)
        if only_time:
            hour = self._int(only_time.group(1), default_hour)
            minute = self._int(only_time.group(2), default_minute)
            base = base_day or datetime(now.year, now.month, now.day)
            try:
                return datetime(base.year, base.month, base.day, hour, minute)
            except Exception:
                return None

        if base_day is not None:
            return datetime(base_day.year, base_day.month, base_day.day, default_hour, default_minute)
        return None

    def _extract_weekday_target(self, text: str) -> Optional[int]:
        source = str(text or '').lower()
        for weekday_index, pattern in self._WEEKDAY_PATTERNS:
            if re.search(pattern, source, flags=re.IGNORECASE):
                return weekday_index
        return None

    def _next_weekday(self, base_day: datetime, weekday_index: int) -> datetime:
        current = int(base_day.weekday())
        delta = (weekday_index - current) % 7
        return base_day + timedelta(days=delta)

    def _strip_by_spans(self, text: str, spans: List[Tuple[int, int]]) -> str:
        if not spans:
            return str(text or '').strip()
        source = str(text or '')
        ordered = sorted(spans, key=lambda item: item[0])
        cursor = 0
        parts: List[str] = []
        for start, end in ordered:
            if start > cursor:
                parts.append(source[cursor:start])
            cursor = max(cursor, end)
        if cursor < len(source):
            parts.append(source[cursor:])
        return self._normalize_title(' '.join(parts))

    def _strip_create_prefix(self, text: str) -> str:
        source = str(text or '').strip()
        if not source:
            return ''
        cleaned = re.sub(r'^\s*(создай|добавь)\s+задач[ауи]?\s*', '', source, flags=re.IGNORECASE)
        return cleaned.strip()

    def _normalize_title(self, text: str) -> str:
        source = str(text or '').strip()
        source = re.sub(r'\s+', ' ', source)
        source = re.sub(r'^[,;:\-]+', '', source).strip()
        source = re.sub(r'[,;:\-]+$', '', source).strip()
        return source

    def _extract_text(self, data: Any) -> str:
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            for key in ('text', 'msgData', 'speech', 'value'):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ''

    def _format_task_list(self, title: str, items: List[Dict[str, Any]], total: int) -> str:
        lines: List[str] = [f'{title}: {min(len(items), 20)} задач(и).']
        shown = items[:20]
        for index, item in enumerate(shown, start=1):
            lines.append(f'{index}. {self._format_single_task(item)}')
        if total > len(shown):
            lines.append(f'... и еще {total - len(shown)}')
        return '\n'.join(lines)

    def _format_single_task(self, item: Dict[str, Any]) -> str:
        title = str(item.get('title') or 'Без названия').strip()
        due_ms = self._int(item.get('dueAtMs'), 0)
        if due_ms > 0:
            return f'{title} (до {self._format_due(due_ms)})'
        return f'{title} (без срока)'

    def _format_due(self, value_ms: int) -> str:
        try:
            dt = datetime.fromtimestamp(float(value_ms) / 1000.0)
        except Exception:
            return 'неизвестно'
        return dt.strftime('%d.%m.%Y %H:%M')

    def _format_error(self, response: Dict[str, Any]) -> str:
        err = response.get('error')
        if isinstance(err, dict):
            message = str(err.get('message') or '').strip()
            if message:
                return message
        if isinstance(err, str) and err.strip():
            return err.strip()
        return 'unknown error'

    def _say(self, text: str) -> None:
        message = str(text or '').strip()
        if not message:
            return
        self.request_say_direct(message)

    def _reply(self, sender: str, payload: Any) -> None:
        sender_tag = str(sender or '').strip()
        if not sender_tag:
            return
        self.reply(sender_tag, payload)

    def _as_items(self, raw: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
        return out

    def _as_map(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        return {}

    def _to_ms(self, dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    def _int(self, raw: Any, default: int) -> int:
        try:
            if raw is None:
                return int(default)
            return int(float(raw))
        except Exception:
            return int(default)

    def _int_or_none(self, raw: Any) -> Optional[int]:
        if raw is None:
            return None
        try:
            return int(float(raw))
        except Exception:
            return None

    def _now(self) -> datetime:
        return datetime.now()


if __name__ == '__main__':
    run_plugin(OrganizerChatPlugin)
