import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'reactions', 'reactions_plugin')
ReactionsPlugin = _MODULE.ReactionsPlugin


class ReactionsPluginLive2DTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = ReactionsPlugin()
        self.calls = []
        self.plugin.send_message = lambda tag, data=None: self.calls.append((tag, data))  # type: ignore[method-assign]

    def _request_payloads(self):
        return [payload for tag, payload in self.calls if tag == 'MinaChan:request-say']

    def test_left_click_triggers_live2d_feature_and_say_intent(self) -> None:
        self.plugin._consume_gui_state(
            {
                'skinType': 'live2d',
                'live2dProfileId': 'frieren',
                'emotions': ['normal', 'anya2'],
                'live2dFeatures': [
                    {
                        'id': 'soft_smile',
                        'title': 'Soft Smile',
                        'description': 'Subtle smile variant',
                        'emotion': 'anya2',
                    }
                ],
            }
        )

        self.plugin.on_gui_event('', {'x': 120, 'y': 240}, 'gui-events:character-left-click')

        feature_calls = [payload for tag, payload in self.calls if tag == 'gui:trigger-live2d-feature']
        self.assertEqual(len(feature_calls), 1)
        self.assertEqual(feature_calls[0].get('id'), 'soft_smile')

        request_payloads = self._request_payloads()
        self.assertEqual(len(request_payloads), 1)
        payload = request_payloads[0]
        self.assertEqual(payload.get('intent'), 'TOUCH_LEFT_CLICK')
        self.assertIsInstance(payload.get('vars'), dict)
        self.assertEqual(payload.get('vars', {}).get('reactionKind'), 'left_click')
        self.assertEqual(payload.get('vars', {}).get('live2dFeature'), 'soft_smile')
        self.assertEqual(payload.get('vars', {}).get('live2dEmotion'), 'anya2')
        self.assertEqual(payload.get('vars', {}).get('live2dProfile'), 'frieren')

    def test_live2d_emotion_fallback_when_no_feature_matches(self) -> None:
        self.plugin._consume_gui_state(
            {
                'skinType': 'live2d',
                'emotions': ['normal', 'worried'],
                'live2dFeatures': [],
            }
        )

        self.plugin.on_gui_event('', {'x': 20, 'y': 30}, 'gui-events:character-right-click')

        emotion_calls = [payload for tag, payload in self.calls if tag == 'gui:set-emotion']
        self.assertEqual(len(emotion_calls), 1)
        self.assertEqual(emotion_calls[0].get('emotion'), 'worried')

        request_payloads = self._request_payloads()
        self.assertEqual(len(request_payloads), 1)
        self.assertEqual(request_payloads[0].get('intent'), 'TOUCH_RIGHT_CLICK')
        self.assertEqual(request_payloads[0].get('vars', {}).get('live2dEmotion'), 'worried')

    def test_image_skin_does_not_emit_live2d_commands(self) -> None:
        self.plugin._consume_gui_state({'skinType': 'image', 'emotions': ['normal']})

        self.plugin.on_gui_event('', {'x': 1, 'y': 2}, 'gui-events:character-left-click')

        tags = [tag for tag, _ in self.calls]
        self.assertNotIn('gui:trigger-live2d-feature', tags)
        self.assertNotIn('gui:set-emotion', tags)
        self.assertIn('MinaChan:request-say', tags)


if __name__ == '__main__':
    unittest.main()
