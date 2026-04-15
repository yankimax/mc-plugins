#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import random
import re
import sys
from datetime import datetime
from typing import Any, Callable, List

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


MAGIC_BALL_ANSWERS = [
    "Бесспорно",
    "Предрешено",
    "Никаких сомнений",
    "Определённо да",
    "Можешь быть уверен в этом",
    "Мне кажется, да",
    "Вероятнее всего",
    "Хорошие перспективы",
    "Знаки говорят, да",
    "Да",
    "Пока не ясно, попробуй снова",
    "Спроси позже",
    "Лучше не рассказывать",
    "Сейчас нельзя предсказать",
    "Сконцентрируйся и спроси опять",
    "Даже не думай",
    "Мой ответ, нет",
    "По моим данным, нет",
    "Перспективы не очень хорошие",
    "Весьма сомнительно",
]

MONTHS_RU = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

WEEKDAYS_RU = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]

_CHOOSE_PREFIX_RE = re.compile(
    r"^(?:выбери(?:\s+из)?|реши(?:\s+между)?|решай(?:\s+между)?|"
    r"choose(?:\s+from)?|decide(?:\s+between)?)\s+"
)

_CHOOSE_SPLIT_RE = re.compile(r"(?:,|\bи\b|\bили\b|\bor\b)")


class ChatSugarPlugin(MinaChanPlugin):
    CMD_MAGIC_BALL = "chat_sugar:magic-ball"
    CMD_CHOOSE = "chat_sugar:choose"
    CMD_RANDOM_NUMBER = "chat_sugar:random-number"
    CMD_DICE = "chat_sugar:dice"
    CMD_DATE = "chat_sugar:date"
    CMD_TIME = "chat_sugar:time"
    CMD_ROULETTE = "chat_sugar:roulette"
    CMD_LEGACY_MAGIC_BALL = "chat:magic-ball"
    CMD_LEGACY_DICE = "chat:roll-dice"

    def __init__(self) -> None:
        super().__init__()
        self._recent_answers = self.text_buffer(50)

    def on_init(self) -> None:
        self.add_listener(self.CMD_MAGIC_BALL, self.on_magic_ball, listener_id="chat_sugar_magic_ball")
        self.add_listener(self.CMD_CHOOSE, self.on_choose, listener_id="chat_sugar_choose")
        self.add_listener(
            self.CMD_RANDOM_NUMBER,
            self.on_random_number,
            listener_id="chat_sugar_random_number",
        )
        self.add_listener(self.CMD_DICE, self.on_dice, listener_id="chat_sugar_dice")
        self.add_listener(self.CMD_DATE, self.on_date, listener_id="chat_sugar_date")
        self.add_listener(self.CMD_TIME, self.on_time, listener_id="chat_sugar_time")
        self.add_listener(self.CMD_ROULETTE, self.on_roulette, listener_id="chat_sugar_roulette")
        self.add_listener(
            self.CMD_LEGACY_MAGIC_BALL,
            self.on_magic_ball,
            listener_id="chat_sugar_legacy_magic_ball",
        )
        self.add_listener(
            self.CMD_LEGACY_DICE,
            self.on_dice,
            listener_id="chat_sugar_legacy_dice",
        )

        self.register_command(
            self.CMD_MAGIC_BALL,
            {"en": "Magic 8-ball style answer", "ru": "Ответ «магического шара»"},
        )
        self.register_command(
            self.CMD_CHOOSE,
            {"en": "Choose one option from message text", "ru": "Выбрать один вариант из текста"},
        )
        self.register_command(
            self.CMD_RANDOM_NUMBER,
            {"en": "Generate random number in range", "ru": "Сгенерировать случайное число в диапазоне"},
        )
        self.register_command(
            self.CMD_DICE,
            {"en": "Roll a dice", "ru": "Бросить кубик"},
        )
        self.register_command(
            self.CMD_DATE,
            {"en": "Tell date for timestamp", "ru": "Сказать дату по timestamp"},
        )
        self.register_command(
            self.CMD_TIME,
            {"en": "Tell time for timestamp", "ru": "Сказать время по timestamp"},
        )
        self.register_command(
            self.CMD_ROULETTE,
            {"en": "Play russian roulette", "ru": "Сыграть в русскую рулетку"},
        )
        self.register_command(
            self.CMD_LEGACY_MAGIC_BALL,
            {"en": "Legacy alias: magic ball answer", "ru": "Legacy-алиас: ответ «магического шара»"},
        )
        self.register_command(
            self.CMD_LEGACY_DICE,
            {"en": "Legacy alias: roll dice", "ru": "Legacy-алиас: бросок кубика"},
        )

        self.register_speech_rule(
            self.CMD_MAGIC_BALL,
            {"ru": "ответь {text:List}", "en": "answer {text:List}"},
        )
        self.register_speech_rule(
            self.CMD_MAGIC_BALL,
            {"ru": "магический шар {text:List}", "en": "magic ball {text:List}"},
        )
        self.register_speech_rule(
            self.CMD_CHOOSE,
            {"ru": "?(выбери ?из) {left:List} или {right:List}", "en": "?(choose ?from) {left:List} or {right:List}"},
        )
        self.register_speech_rule(
            self.CMD_RANDOM_NUMBER,
            {
                "ru": "(случайное|сгенерируй) число ?от {from:Integer} ?до {to:Integer}",
                "en": "(random|generate) number ?from {from:Integer} ?to {to:Integer}",
            },
        )
        self.register_speech_rule(
            self.CMD_DICE,
            {"ru": "?(брось|кинь) кубик {to:Integer}", "en": "?(roll|throw) dice {to:Integer}"},
        )
        self.register_speech_rule(
            self.CMD_DICE,
            {"ru": "?(брось|кинь) кубик", "en": "?(roll|throw) dice"},
        )
        self.register_speech_rule(
            self.CMD_DATE,
            {"ru": "какой (число|день) {date:DateTime}", "en": "what (date|day) {date:DateTime}"},
        )
        self.register_speech_rule(
            self.CMD_TIME,
            {"ru": "(сколько|какое) (время|времени) {time:DateTime}", "en": "what time {time:DateTime}"},
        )
        self.register_speech_rule(
            self.CMD_ROULETTE,
            {"ru": "рулетка", "en": "roulette"},
        )

    def on_magic_ball(self, sender: str, data: Any, tag: str) -> None:
        question = self.message_text(data)
        if not question:
            self._say("Но ты же ничего не спросил!")
            return
        self._answer_from_history(question, lambda: random.choice(MAGIC_BALL_ANSWERS))

    def on_choose(self, sender: str, data: Any, tag: str) -> None:
        text = self.message_text(data)
        if not text:
            self._say("Но ты же ничего не спросил!")
            return

        cleaned = text.lower().replace("?", " ")
        cleaned = _CHOOSE_PREFIX_RE.sub("", cleaned, count=1)
        choices = [
            item.strip()
            for item in _CHOOSE_SPLIT_RE.split(cleaned)
            if item and item.strip()
        ]
        if len(choices) < 2:
            self._say("Слишком мало вариантов для выбора.")
            return
        self._answer_from_history(text, lambda: random.choice(choices))

    def on_dice(self, sender: str, data: Any, tag: str) -> None:
        sides = self.int_field(data, "to", 6)
        sides = max(2, min(sides, 1000))
        self._say(str(random.randint(1, sides)))

    def on_random_number(self, sender: str, data: Any, tag: str) -> None:
        to_value = self.int_field(data, "to", 0)
        if to_value == 0:
            self._say(f"Я не поняла, что ты написал, так что вот тебе: {random.random()}")
            return

        from_value = self.int_field(data, "from", 0)
        if to_value < from_value:
            from_value, to_value = to_value, from_value

        self._say(str(random.randint(from_value, to_value)))

    def on_date(self, sender: str, data: Any, tag: str) -> None:
        millis = self.timestamp_field(data, "date")
        now = datetime.now().astimezone()
        target = datetime.fromtimestamp(millis / 1000, tz=now.tzinfo) if millis is not None else now

        result = (
            f"{WEEKDAYS_RU[target.weekday()]}, "
            f"{target.day} {MONTHS_RU[target.month - 1]}, {target.year}"
        )
        delta_days = (target.date() - now.date()).days

        if delta_days == 0:
            self._say(f"Сегодня {result}")
            return
        if delta_days == 1:
            self._say(f"Завтра будет {result}")
            return
        if delta_days == 2:
            self._say(f"Послезавтра будет {result}")
            return
        if delta_days == -1:
            self._say(f"Вчера был {result}")
            return
        if delta_days == -2:
            self._say(f"Позавчера был {result}")
            return
        if target > now:
            self._say(f"Это будет {result}")
            return
        self._say(f"Это был {result}")

    def on_time(self, sender: str, data: Any, tag: str) -> None:
        millis = self.timestamp_field(data, "time")
        now = datetime.now().astimezone()
        if millis is None:
            self._say(f"Сейчас {now.strftime('%H:%M:%S')}")
            return

        target = datetime.fromtimestamp(millis / 1000, tz=now.tzinfo)
        if target >= now:
            self._say(f"Это будет {target.strftime('%H:%M:%S')}")
            return
        self._say(f"Это был {target.strftime('%H:%M:%S')}")

    def on_roulette(self, sender: str, data: Any, tag: str) -> None:
        if random.randrange(6) == 0:
            self._say("Упс. Ты мёртв.")
            return
        self._say("На этот раз тебе повезло. На этот раз.")

    def _answer_from_history(self, query: str, generator: Callable[[], str]) -> None:
        cached = self._recent_answers.get(query)
        if cached is not None:
            self._say(f"Зачем ты снова меня это спрашиваешь? Я же ответила - {cached}")
            return

        answer = str(generator()).strip()
        self._recent_answers.put(query, answer)
        self._say(answer)

    def _say(self, text: str) -> None:
        self.request_say_direct(text)


if __name__ == "__main__":
    run_plugin(ChatSugarPlugin)
