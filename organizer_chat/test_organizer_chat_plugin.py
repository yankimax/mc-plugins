import importlib.machinery
import importlib.util
import pathlib
import unittest
from datetime import datetime


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('organizer_chat_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for organizer_chat plugin')
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
OrganizerChatPlugin = _MODULE.OrganizerChatPlugin


class OrganizerChatPluginLogicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = OrganizerChatPlugin()
        self.spoken = []
        self.replies = []
        self.plugin.request_say_direct = lambda text, **kwargs: self.spoken.append(text)  # type: ignore[method-assign]
        self.plugin.reply = lambda sender, data=None: self.replies.append((sender, data))  # type: ignore[method-assign]

    def test_period_window_for_current_week_is_monday_to_sunday(self) -> None:
        start_ms, end_ms, title = self.plugin._period_window('week', datetime(2026, 3, 18, 12, 0, 0))
        start_dt = datetime.fromtimestamp(start_ms / 1000.0)
        end_dt = datetime.fromtimestamp(end_ms / 1000.0)

        self.assertEqual(title, 'Текущая неделя')
        self.assertEqual(start_dt.strftime('%Y-%m-%d %H:%M:%S'), '2026-03-16 00:00:00')
        self.assertEqual(end_dt.strftime('%Y-%m-%d %H:%M:%S'), '2026-03-22 23:59:59')

    def test_on_list_queries_unfinished_due_tasks_for_today(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # type: ignore[method-assign]
        calls = []

        def fake_request(command, payload, callback):
            calls.append((command, payload))
            callback(
                {
                    'ok': True,
                    'total': 1,
                    'items': [
                        {
                            'id': 7,
                            'title': 'Оплатить хостинг',
                            'dueAtMs': int(datetime(2026, 3, 18, 21, 0, 0).timestamp() * 1000),
                        }
                    ],
                }
            )

        self.plugin._request_core = fake_request  # type: ignore[method-assign]

        self.plugin.on_list('tester', {'period': 'сегодня'}, OrganizerChatPlugin.CMD_LIST)

        self.assertEqual(len(calls), 1)
        command, payload = calls[0]
        self.assertEqual(command, 'organizer-core:list-items')
        self.assertEqual(payload.get('includeTerminal'), False)
        self.assertEqual(payload.get('hasDue'), True)
        self.assertEqual(payload.get('sort'), 'due_asc')
        self.assertTrue(any('Оплатить хостинг' in text for text in self.spoken))
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(self.replies[0][0], 'tester')

    def test_on_next_fallbacks_to_task_without_due(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 9, 0, 0)  # type: ignore[method-assign]
        calls = []

        def fake_request(command, payload, callback):
            calls.append((command, payload))
            if len(calls) == 1:
                callback({'ok': True, 'items': []})
                return
            callback({'ok': True, 'items': [{'id': 12, 'title': 'Разобрать почту'}]})

        self.plugin._request_core = fake_request  # type: ignore[method-assign]

        self.plugin.on_next('tester', {}, OrganizerChatPlugin.CMD_NEXT)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1].get('hasDue'), True)
        self.assertEqual(calls[1][1].get('hasDue'), False)
        self.assertTrue(any('Разобрать почту' in text for text in self.spoken))

    def test_on_create_parses_title_from_phrase_and_calls_core(self) -> None:
        calls = []

        def fake_request(command, payload, callback):
            calls.append((command, payload))
            callback({'ok': True, 'item': {'id': 99, 'title': payload.get('title')}})

        self.plugin._request_core = fake_request  # type: ignore[method-assign]

        self.plugin.on_create(
            'tester',
            {'msgData': 'создай задачу Купить молоко и хлеб'},
            OrganizerChatPlugin.CMD_CREATE,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'organizer-core:create-item')
        self.assertEqual(calls[0][1].get('title'), 'Купить молоко и хлеб')
        self.assertTrue(any('создана' in text.lower() for text in self.spoken))

    def test_build_create_payload_parses_priority_state_and_due(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'создай задачу Подготовить отчёт срочно в работе до завтра 18:30'}
        )

        self.assertEqual(payload.get('title'), 'Подготовить отчёт')
        self.assertEqual(payload.get('priority'), 'high')
        self.assertEqual(payload.get('state'), 'in_progress')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 19, 18, 30, 0).timestamp() * 1000))

    def test_build_create_payload_parses_relative_due_expression(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'добавь задачу Позвонить клиенту через 2 часа'}
        )

        self.assertEqual(payload.get('title'), 'Позвонить клиенту')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 18, 12, 0, 0).timestamp() * 1000))

    def test_build_create_payload_parses_due_marker_date_of_execution(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'создай задачу положить евро на карту с датой исполнения сегодня'}
        )

        self.assertEqual(payload.get('title'), 'положить евро на карту')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 18, 18, 0, 0).timestamp() * 1000))

    def test_build_create_payload_handles_na_segodnya_without_preposition_in_title(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'Создай задачу на сегодня снять евро с карты'}
        )

        self.assertEqual(payload.get('title'), 'снять евро с карты')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 18, 18, 0, 0).timestamp() * 1000))

    def test_build_create_payload_handles_na_pyatnitsu(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # Wednesday  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'создай задачу на пятницу снять евро с карты'}
        )

        self.assertEqual(payload.get('title'), 'снять евро с карты')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 20, 18, 0, 0).timestamp() * 1000))

    def test_build_create_payload_handles_v_sleduyushchuyu_subbotu(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # Wednesday  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'создай задачу в следующую субботу нужно съездить в ТЦ Кунцево плаза'}
        )

        self.assertEqual(payload.get('title'), 'нужно съездить в ТЦ Кунцево плаза')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 28, 18, 0, 0).timestamp() * 1000))

    def test_build_create_payload_removes_today_when_due_time_present(self) -> None:
        self.plugin._now = lambda: datetime(2026, 3, 18, 10, 0, 0)  # type: ignore[method-assign]
        payload = self.plugin._build_create_payload_from_chat(
            {'msgData': 'создай задачу сходить сегодня в душ до 23:00'}
        )

        self.assertEqual(payload.get('title'), 'сходить в душ')
        self.assertEqual(payload.get('dueAtMs'), int(datetime(2026, 3, 18, 23, 0, 0).timestamp() * 1000))


if __name__ == '__main__':
    unittest.main()
