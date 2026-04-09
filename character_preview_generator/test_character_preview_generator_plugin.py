import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import unittest

_SDK_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / 'minachan_app'
    / 'plugins'
    / 'sdk_python'
)
os.environ.setdefault('MINACHAN_SDK_PYTHON_DIR', str(_SDK_DIR))


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'character_preview_generator' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('character_preview_generator_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for character_preview_generator plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
CharacterPreviewGeneratorPlugin = _MODULE.CharacterPreviewGeneratorPlugin


class CharacterPreviewGeneratorPluginStartupTest(unittest.TestCase):
    def test_on_init_syncs_menu_link_without_loading_complete_barrier(self) -> None:
        plugin = CharacterPreviewGeneratorPlugin()
        links = []

        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.set_event_link = lambda event, command, rule=None, msg_data=None: links.append((event, command, rule))  # type: ignore[method-assign]
        plugin.remove_event_link = lambda *args, **kwargs: None  # type: ignore[method-assign]

        plugin.on_init()

        self.assertEqual(plugin._menu_rule, 'Отладка/Генерация превью')
        self.assertEqual(
            links,
            [('gui:menu-action', 'character-preview:generate-all', 'Отладка/Генерация превью')],
        )


if __name__ == '__main__':
    unittest.main()
