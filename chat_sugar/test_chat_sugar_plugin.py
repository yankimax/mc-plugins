import pathlib
import sys
import unittest
from unittest import mock

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'chat_sugar', 'chat_sugar_plugin')
ChatSugarPlugin = _MODULE.ChatSugarPlugin


class ChatSugarPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = ChatSugarPlugin()
        self.spoken = []
        self.plugin.request_say_direct = self.spoken.append  # type: ignore[method-assign]

    def test_magic_ball_reuses_cached_answer(self) -> None:
        with mock.patch.object(_MODULE.random, "choice", return_value="Да"):
            self.plugin.on_magic_ball("user", {"msgData": "Ты уверена?"}, "chat_sugar:magic-ball")
            self.plugin.on_magic_ball("user", {"msgData": "Ты уверена?"}, "chat_sugar:magic-ball")

        self.assertEqual(self.spoken[0], "Да")
        self.assertEqual(
            self.spoken[1],
            "Зачем ты снова меня это спрашиваешь? Я же ответила - Да",
        )

    def test_choose_uses_split_variants(self) -> None:
        with mock.patch.object(_MODULE.random, "choice", return_value="чай"):
            self.plugin.on_choose(
                "user",
                {"msgData": "Выбери чай или кофе"},
                "chat_sugar:choose",
            )

        self.assertEqual(self.spoken, ["чай"])

    def test_random_number_swaps_range_bounds(self) -> None:
        with mock.patch.object(_MODULE.random, "randint", return_value=7) as randint:
            self.plugin.on_random_number(
                "user",
                {"from": 10, "to": 5},
                "chat_sugar:random-number",
            )

        randint.assert_called_once_with(5, 10)
        self.assertEqual(self.spoken, ["7"])


if __name__ == "__main__":
    unittest.main()
