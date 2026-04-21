import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'emotion_menu', 'emotion_menu_plugin')
EmotionMenuPlugin = _MODULE.EmotionMenuPlugin


class EmotionMenuPluginStartupTest(unittest.TestCase):
    def test_on_init_requests_emotions_without_loading_complete_barrier(self) -> None:
        plugin = EmotionMenuPlugin()
        messages = []

        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.send_message = lambda tag, data=None: messages.append((tag, data))  # type: ignore[method-assign]

        plugin.on_init()

        self.assertIn(('gui:get-emotions', None), messages)


if __name__ == '__main__':
    unittest.main()
