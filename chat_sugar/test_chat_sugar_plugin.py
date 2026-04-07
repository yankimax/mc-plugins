import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name("plugin.py3")
    loader = importlib.machinery.SourceFileLoader("chat_sugar_plugin", str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("failed to create import spec for chat_sugar plugin")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
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
