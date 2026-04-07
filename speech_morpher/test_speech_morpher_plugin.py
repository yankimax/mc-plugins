import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('speech_morpher_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for speech_morpher plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
SpeechMorpherPlugin = _MODULE.SpeechMorpherPlugin
MODE_OFF = _MODULE.MODE_OFF
MODE_ALWAYS = _MODULE.MODE_ALWAYS
MODE_BY_PRESET = _MODULE.MODE_BY_PRESET
MORPHER_ALTERNATIVE_TAG = _MODULE.MORPHER_ALTERNATIVE_TAG


class SpeechMorpherPluginContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = SpeechMorpherPlugin()
        self.calls = []
        self.alt_calls = []
        self.props = {}

        self.plugin.info = {
            'pluginDirPath': str(pathlib.Path(__file__).parent),
            'rootDirPath': str(pathlib.Path(__file__).resolve().parents[3]),
            'locale': 'ru',
            'id': 'speech_morpher',
        }
        self.plugin.send_message = lambda tag, data=None: self.calls.append((tag, data))  # type: ignore[method-assign]
        self.plugin.call_next_alternative = (  # type: ignore[method-assign]
            lambda sender, tag, current, data=None: self.alt_calls.append((sender, tag, current, data))
        )
        self.plugin.get_property = lambda key, default=None: self.props.get(key, default)  # type: ignore[method-assign]
        self.plugin.set_property = lambda key, value: self.props.__setitem__(key, value)  # type: ignore[method-assign]
        self.plugin.save_properties = lambda: self.props.__setitem__('__saved__', True)  # type: ignore[method-assign]

    def test_reload_modules_loads_builtin_set(self) -> None:
        self.plugin._reload_modules()
        loaded = set(self.plugin._module_order)
        self.assertTrue({'caps', 'confidence', 'culturing', 'neko'}.issubset(loaded))

    def test_morph_pipeline_forwards_transformed_payload(self) -> None:
        self.plugin._reload_modules()
        self.plugin._set_all_modes(MODE_ALWAYS, save=False)

        payload = {
            'text': 'привет пожалуйста',
            'intent': 'HELLO',
            'preset': {
                'traits': {
                    'energy': 0.9,
                    'confidence': 0.8,
                    'playfulness': 1.0,
                    'friendliness': 1.0,
                },
                'emotions': {'happy': 1.0},
            },
            'locale': 'ru',
        }
        self.plugin.on_request_say_morph('tester', payload, MORPHER_ALTERNATIVE_TAG)

        self.assertEqual(len(self.alt_calls), 1)
        _, _, _, forwarded = self.alt_calls[0]
        self.assertIsInstance(forwarded, dict)
        forwarded_map = forwarded
        self.assertNotEqual(forwarded_map.get('text'), 'привет пожалуйста')
        self.assertTrue(len(forwarded_map.get('_morphersApplied') or []) >= 1)

    def test_mode_off_skips_all_transformations(self) -> None:
        self.plugin._reload_modules()
        self.plugin._set_all_modes(MODE_OFF, save=False)

        payload = {
            'text': 'Hello there',
            'intent': 'HELLO',
            'preset': {'traits': {'energy': 1.0}, 'emotions': {}},
            'locale': 'en',
        }
        self.plugin.on_request_say_morph('tester', payload, MORPHER_ALTERNATIVE_TAG)

        self.assertEqual(len(self.alt_calls), 1)
        _, _, _, forwarded = self.alt_calls[0]
        self.assertEqual(forwarded.get('text'), 'Hello there')
        self.assertEqual(forwarded.get('_morphersApplied'), [])

    def test_set_module_mode_persists_value(self) -> None:
        self.plugin._reload_modules()

        self.plugin.on_set_module_mode('tester', {'id': 'caps', 'mode': 'ALWAYS'}, 'speech_morpher:set-module-mode')

        self.assertEqual(self.props.get('module.caps.mode'), MODE_ALWAYS)
        self.assertTrue(self.props.get('__saved__'))
        replies = [item for item in self.calls if item[0] == 'tester']
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0][1].get('ok'))

    def test_parse_mode_accepts_named_and_numeric_values(self) -> None:
        self.assertEqual(self.plugin._parse_mode('OFF', MODE_BY_PRESET), MODE_OFF)
        self.assertEqual(self.plugin._parse_mode('BY_PRESET', MODE_OFF), MODE_BY_PRESET)
        self.assertEqual(self.plugin._parse_mode('ALWAYS', MODE_OFF), MODE_ALWAYS)
        self.assertEqual(self.plugin._parse_mode(2, MODE_OFF), MODE_ALWAYS)


if __name__ == '__main__':
    unittest.main()
