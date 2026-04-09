import importlib.machinery
import importlib.util
import pathlib
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).resolve().parent / 'files' / 'organizer_ui' / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader('organizer_ui_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for organizer_ui plugin')
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
OrganizerUiPlugin = _MODULE.OrganizerUiPlugin


class OrganizerUiPluginLogicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = OrganizerUiPlugin()
        self.plugin._dictionaries = {
            'state': [
                {'id': 'planned', 'name': 'Planned', 'isDefault': True, 'terminal': False},
                {'id': 'done', 'name': 'Done', 'isDefault': False, 'terminal': True},
            ],
            'priority': [
                {'id': 'normal', 'name': 'Normal', 'isDefault': True},
                {'id': 'high', 'name': 'High', 'isDefault': False},
            ],
            'source': [
                {'id': 'manual', 'name': 'Manual', 'isDefault': True},
                {'id': 'sync', 'name': 'Sync', 'isDefault': False},
            ],
            'tag': [
                {'id': 'home', 'name': 'Home'},
                {'id': 'work', 'name': 'Work'},
            ],
        }
        self.plugin._ensure_defaults_from_dictionaries()

    def test_build_list_items_payload(self) -> None:
        self.plugin._filters.update(
            {
                'search': 'rent',
                'state': 'planned',
                'priority': 'high',
                'source': 'sync',
                'tag': 'home',
                'tagsAny': ['home', 'work'],
                'externalUid': 'ext-99',
                'hasStart': True,
                'startFromMs': 1_700_000_100_000,
                'startToMs': 1_700_000_900_000,
                'includeTerminal': True,
                'sort': 'updated_desc',
                'limit': 50,
            }
        )
        payload = self.plugin._build_list_items_payload()

        self.assertEqual(payload.get('search'), 'rent')
        self.assertEqual(payload.get('state'), 'planned')
        self.assertEqual(payload.get('priority'), 'high')
        self.assertEqual(payload.get('source'), 'sync')
        self.assertEqual(payload.get('tag'), 'home')
        self.assertEqual(payload.get('tagsAny'), ['home', 'work'])
        self.assertEqual(payload.get('externalUid'), 'ext-99')
        self.assertEqual(payload.get('hasStart'), True)
        self.assertEqual(payload.get('startFromMs'), 1_700_000_100_000)
        self.assertEqual(payload.get('startToMs'), 1_700_000_900_000)
        self.assertEqual(payload.get('includeTerminal'), True)
        self.assertEqual(payload.get('sort'), 'updated_desc')
        self.assertEqual(payload.get('limit'), 50)

    def test_build_list_items_payload_with_overview_drilldown_fields(self) -> None:
        self.plugin._filters.update(
            {
                'dueFromMs': 1_700_000_000_000,
                'dueToMs': 1_700_086_400_000,
                'hasDue': False,
                'completedOnly': True,
            }
        )
        payload = self.plugin._build_list_items_payload()

        self.assertEqual(payload.get('dueFromMs'), 1_700_000_000_000)
        self.assertEqual(payload.get('dueToMs'), 1_700_086_400_000)
        self.assertEqual(payload.get('hasDue'), False)
        self.assertEqual(payload.get('states'), ['done'])

    def test_build_create_item_payload(self) -> None:
        self.plugin._form.update(
            {
                'title': 'Pay bills',
                'description': 'card only',
                'state': 'planned',
                'priority': 'high',
                'source': 'manual',
                'externalUid': 'ext-123',
                'startAt': '2026-04-05T10:30',
                'dueAt': '2026-04-05T11:15',
                'upcomingLeadMin': '20',
                'tags': ['home', 'work'],
                'payload': '{"x": 1}',
            }
        )
        payload = self.plugin._build_create_item_payload()

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.get('title'), 'Pay bills')
        self.assertEqual(payload.get('description'), 'card only')
        self.assertEqual(payload.get('state'), 'planned')
        self.assertEqual(payload.get('priority'), 'high')
        self.assertEqual(payload.get('source'), 'manual')
        self.assertEqual(payload.get('externalUid'), 'ext-123')
        self.assertEqual(payload.get('upcomingLeadMs'), 20 * 60000)
        self.assertEqual(payload.get('tags'), ['home', 'work'])
        self.assertEqual(payload.get('payload'), {'x': 1})
        self.assertIsInstance(payload.get('startAtMs'), int)
        self.assertIsInstance(payload.get('dueAtMs'), int)

    def test_build_update_item_payload_requires_id_and_title(self) -> None:
        self.plugin._form.update({'itemId': '', 'title': ''})
        self.assertIsNone(self.plugin._build_update_item_payload())

        self.plugin._form.update({'itemId': '12', 'title': 'Updated'})
        payload = self.plugin._build_update_item_payload()
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.get('id'), 12)
        self.assertEqual(payload.get('title'), 'Updated')

    def test_dictionary_payloads(self) -> None:
        self.plugin._dictionary_editor.update(
            {
                'kind': 'tag',
                'id': 'infra',
                'name': 'Infrastructure',
                'order': '5',
                'color': '#00aa00',
                'icon': 'tag',
                'isDefault': False,
                'isSystem': False,
            }
        )
        upsert = self.plugin._build_dictionary_upsert_payload()
        self.assertIsNotNone(upsert)
        assert upsert is not None
        self.assertEqual(upsert.get('kind'), 'tag')
        self.assertEqual(upsert.get('id'), 'infra')
        self.assertEqual(upsert.get('name'), 'Infrastructure')
        self.assertEqual(upsert.get('order'), 5)

        self.plugin._dictionary_editor.update(
            {
                'kind': 'state',
                'id': 'blocked',
                'force': True,
                'replaceWith': 'planned',
            }
        )
        delete_payload = self.plugin._build_dictionary_delete_payload()
        self.assertIsNotNone(delete_payload)
        assert delete_payload is not None
        self.assertEqual(delete_payload.get('kind'), 'state')
        self.assertEqual(delete_payload.get('id'), 'blocked')
        self.assertEqual(delete_payload.get('force'), True)
        self.assertEqual(delete_payload.get('replaceWith'), 'planned')

    def test_apply_item_to_form(self) -> None:
        self.plugin._apply_item_to_form(
            {
                'id': 99,
                'title': 'Demo',
                'description': 'Desc',
                'state': 'planned',
                'priority': 'normal',
                'source': 'manual',
                'externalUid': 'uid-1',
                'startAtMs': 1_900_000_000_000,
                'dueAtMs': 1_900_000_360_000,
                'upcomingLeadMs': 900000,
                'tags': ['work'],
                'payload': {'a': 1},
            }
        )

        self.assertEqual(self.plugin._form.get('itemId'), '99')
        self.assertEqual(self.plugin._form.get('title'), 'Demo')
        self.assertEqual(self.plugin._form.get('state'), 'planned')
        self.assertEqual(self.plugin._form.get('upcomingLeadMin'), '15')
        self.assertEqual(self.plugin._form.get('tags'), ['work'])
        self.assertIn('"a": 1', self.plugin._form.get('payload'))

    def test_extract_item_id_aliases(self) -> None:
        self.assertEqual(self.plugin._extract_item_id({'item_id': '7'}), 7)
        self.assertEqual(self.plugin._extract_item_id({'itemId': 8}), 8)
        self.assertEqual(self.plugin._extract_item_id({'id': '9'}), 9)
        self.plugin._form['itemId'] = '42'
        self.assertEqual(self.plugin._extract_item_id_from_payload_only({}), None)

    def test_build_controls_contains_refresh_button_tag(self) -> None:
        controls = self.plugin._build_controls()
        refresh_controls = [item for item in controls if item.get('id') == 'btn_refresh']
        self.assertEqual(len(refresh_controls), 1)
        self.assertEqual(refresh_controls[0].get('msgTag'), OrganizerUiPlugin.CMD_REFRESH)
        self.assertEqual(refresh_controls[0].get('variant'), 'outlined')

    def test_build_controls_adds_sections_and_metrics(self) -> None:
        controls = self.plugin._build_controls()
        controls_by_id = {item.get('id'): item for item in controls}

        self.assertIn('metric_today_planned', controls_by_id)
        self.assertEqual(controls_by_id['metric_today_planned'].get('section'), 'overview')
        self.assertEqual(controls_by_id['metric_today_planned'].get('span'), 1)
        self.assertEqual(controls_by_id['metric_today_planned'].get('minWidth'), 200)
        self.assertIn('metric_all_completion_rate', controls_by_id)
        self.assertEqual(controls_by_id['btn_overview_open_editor'].get('section'), 'overview')
        self.assertEqual(controls_by_id['btn_overview_open_editor'].get('variant'), 'primary')
        self.assertEqual(controls_by_id['btn_overview_refresh'].get('variant'), 'outlined')
        self.assertEqual(controls_by_id['filter_search'].get('span'), 2)
        self.assertEqual(controls_by_id['filter_search'].get('section'), 'tasks')
        self.assertEqual(controls_by_id['filter_state'].get('span'), 1)
        self.assertIn('filter_tags_any', controls_by_id)
        self.assertEqual(controls_by_id['filter_tags_any'].get('section'), 'tasks')
        self.assertIn('filter_external_uid', controls_by_id)
        self.assertEqual(controls_by_id['filter_external_uid'].get('span'), 2)
        self.assertIn('filter_start_from', controls_by_id)
        self.assertEqual(controls_by_id['filter_start_from'].get('span'), 1)
        self.assertIn('filter_start_to', controls_by_id)
        self.assertIn('filter_due_from', controls_by_id)
        self.assertIn('filter_due_to', controls_by_id)
        self.assertIn('filter_has_start', controls_by_id)
        self.assertIn('filter_has_due', controls_by_id)
        self.assertIn('filter_completed_only', controls_by_id)
        self.assertTrue(bool(controls_by_id['items_table'].get('fullWidth')))
        self.assertNotIn('btn_open_editor', controls_by_id)
        self.assertNotIn('status_label', controls_by_id)
        self.assertEqual(controls_by_id['metric_today_planned'].get('msgTag'), OrganizerUiPlugin.TAG_APPLY_FILTERS)
        self.assertEqual(controls_by_id['metric_today_planned'].get('targetSection'), 'tasks')
        metric_today_preset = controls_by_id['metric_today_planned'].get('setValues')
        self.assertIsInstance(metric_today_preset, dict)
        assert isinstance(metric_today_preset, dict)
        self.assertEqual(metric_today_preset.get('filter_drilldown'), True)
        self.assertIn('filter_due_from_ms', metric_today_preset)

    def test_build_editor_controls_contains_item_actions(self) -> None:
        controls = self.plugin._build_editor_controls()
        controls_by_id = {item.get('id'): item for item in controls}

        self.assertIn('btn_item_create', controls_by_id)
        self.assertEqual(controls_by_id['btn_item_create'].get('section'), 'editor')
        self.assertEqual(controls_by_id['btn_item_create'].get('variant'), 'primary')
        self.assertEqual(controls_by_id['btn_item_delete'].get('variant'), 'danger')
        self.assertIn('item_pick', controls_by_id)
        self.assertEqual(controls_by_id['item_pick'].get('section'), 'editor')


class OrganizerUiPluginStartupTest(unittest.TestCase):
    def test_on_init_publishes_ui_and_refreshes_without_loading_complete_barrier(self) -> None:
        plugin = OrganizerUiPlugin()
        published = []
        refreshed = []

        plugin._load_state = lambda: None  # type: ignore[method-assign]
        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin._register_contract = lambda: None  # type: ignore[method-assign]
        plugin._publish_ui = lambda force_set=False: published.append(force_set)  # type: ignore[method-assign]
        plugin._refresh = lambda reason='': refreshed.append(reason)  # type: ignore[method-assign]

        plugin.on_init()

        self.assertEqual(published, [True])
        self.assertEqual(refreshed, ['init'])


class OrganizerUiPluginLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = OrganizerUiPlugin()

    def test_panel_extra_defines_responsive_section_layout(self) -> None:
        extra = self.plugin._panel_extra()
        self.assertEqual(extra.get('scope'), 'window')
        sections = extra.get('sections')
        self.assertIsInstance(sections, list)
        assert isinstance(sections, list)
        overview = next(item for item in sections if item.get('id') == 'overview')
        tasks = next(item for item in sections if item.get('id') == 'tasks')

        self.assertEqual(overview.get('columns'), 4)
        self.assertEqual(overview.get('minTileWidth'), 200)
        self.assertEqual(overview.get('compact'), False)
        self.assertEqual(tasks.get('hint'), '')
        self.assertEqual(tasks.get('columns'), 3)
        self.assertEqual(tasks.get('minTileWidth'), 220)

    def test_editor_panel_extra_contains_editor_section(self) -> None:
        extra = self.plugin._editor_panel_extra()
        self.assertEqual(extra.get('scope'), 'window')
        sections = extra.get('sections')
        self.assertIsInstance(sections, list)
        assert isinstance(sections, list)
        editor = next(item for item in sections if item.get('id') == 'editor')
        self.assertEqual(editor.get('columns'), 2)
        self.assertEqual(editor.get('minTileWidth'), 240)


if __name__ == '__main__':
    unittest.main()
