import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('emotion_menu_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for emotion_menu plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
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
