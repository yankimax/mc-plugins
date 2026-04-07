import importlib.machinery
import importlib.util
import pathlib
import shutil
import tempfile
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('organizer_core_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for organizer_core plugin')
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
OrganizerCorePlugin = _MODULE.OrganizerCorePlugin


class OrganizerCorePluginContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix='organizer_core_test_')
        self.plugin = OrganizerCorePlugin()
        self.plugin.info = {'id': 'organizer_core', 'dataDirPath': self.temp_dir}
        self.calls = []
        self.plugin.send_message = lambda tag, data=None: self.calls.append((tag, data))  # type: ignore[method-assign]
        self.plugin._init_db()
        self.plugin._load_settings()
        self.plugin._ensure_default_dictionaries()

    def tearDown(self) -> None:
        if getattr(self.plugin, '_db', None) is not None:
            self.plugin._db.close()
            self.plugin._db = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_dictionaries_present(self) -> None:
        config = self.plugin._build_config_payload()
        dictionaries = config.get('dictionaries')
        self.assertIsInstance(dictionaries, dict)

        states = dictionaries.get('state')
        priorities = dictionaries.get('priority')
        sources = dictionaries.get('source')

        self.assertIsInstance(states, list)
        self.assertIsInstance(priorities, list)
        self.assertIsInstance(sources, list)

        self.assertTrue(any(item.get('id') == 'planned' for item in states))
        self.assertTrue(any(item.get('id') == 'normal' for item in priorities))
        self.assertTrue(any(item.get('id') == 'manual' for item in sources))

    def test_dictionary_upsert_and_default_switch(self) -> None:
        created = self.plugin._upsert_dictionary_entry(
            {
                'kind': 'priority',
                'id': 'urgent',
                'name': 'Urgent',
                'isDefault': True,
                'order': 5,
            },
            emit=False,
        )
        self.assertEqual(created.get('id'), 'urgent')
        self.assertTrue(created.get('isDefault'))

        entries = self.plugin._list_dictionary_entries('priority')
        default_ids = [item.get('id') for item in entries if item.get('isDefault')]
        self.assertEqual(default_ids, ['urgent'])

    def test_item_crud_with_external_uid_and_tags(self) -> None:
        item = self.plugin._create_item(
            {
                'title': 'Pay rent',
                'description': 'Bank transfer',
                'source': 'billing',
                'externalUid': 'ext-123',
                'tags': ['finance', 'monthly'],
                'startAtMs': 1_900_000_000_000,
            }
        )
        self.assertEqual(item.get('title'), 'Pay rent')
        self.assertEqual(item.get('externalUid'), 'ext-123')
        self.assertEqual(item.get('source'), 'billing')
        self.assertEqual(set(item.get('tags') or []), {'finance', 'monthly'})

        updated = self.plugin._update_item({'id': item.get('id'), 'state': 'done', 'tags': ['finance']})
        self.assertEqual(updated.get('state'), 'done')
        self.assertEqual(updated.get('tags'), ['finance'])

        listing = self.plugin._list_items({'state': 'done'})
        self.assertEqual(listing.get('count'), 1)
        listed = listing.get('items')[0]
        self.assertEqual(listed.get('id'), item.get('id'))

        deleted = self.plugin._delete_item({'id': item.get('id')})
        self.assertEqual(deleted.get('id'), item.get('id'))
        self.assertEqual(self.plugin._list_items({}).get('count'), 0)

    def test_upsert_external_item_updates_existing_record(self) -> None:
        first = self.plugin._upsert_external_item(
            {
                'title': 'External task',
                'source': 'icalendar',
                'externalUid': 'uid-42',
                'startAtMs': 1_900_000_100_000,
            }
        )
        self.assertEqual(first.get('op'), 'created')
        item = first.get('item') or {}
        item_id = item.get('id')
        self.assertIsNotNone(item_id)

        second = self.plugin._upsert_external_item(
            {
                'title': 'External task updated',
                'source': 'icalendar',
                'externalUid': 'uid-42',
                'priority': 'high',
            }
        )
        self.assertEqual(second.get('op'), 'updated')
        updated_item = second.get('item') or {}
        self.assertEqual(updated_item.get('id'), item_id)
        self.assertEqual(updated_item.get('title'), 'External task updated')
        self.assertEqual(updated_item.get('priority'), 'high')

    def test_list_items_supports_has_due_filter(self) -> None:
        with_due = self.plugin._create_item({'title': 'Has due', 'dueAtMs': 1_900_000_000_000})
        no_due = self.plugin._create_item({'title': 'No due'})

        with_due_list = self.plugin._list_items({'hasDue': True})
        no_due_list = self.plugin._list_items({'hasDue': False})

        with_due_ids = {item.get('id') for item in (with_due_list.get('items') or [])}
        no_due_ids = {item.get('id') for item in (no_due_list.get('items') or [])}

        self.assertIn(with_due.get('id'), with_due_ids)
        self.assertNotIn(no_due.get('id'), with_due_ids)
        self.assertIn(no_due.get('id'), no_due_ids)
        self.assertNotIn(with_due.get('id'), no_due_ids)

    def test_process_notifications_emits_upcoming_and_started(self) -> None:
        self.plugin._settings['notifyViaCore'] = False
        base_now = 1_900_000_000_000
        start_at = base_now + 60_000

        self.plugin._create_item(
            {
                'title': 'Soon event',
                'startAtMs': start_at,
                'upcomingLeadMs': 120_000,
            }
        )

        self.plugin._now_ms = lambda: base_now  # type: ignore[method-assign]
        first = self.plugin._process_notifications()
        self.assertEqual(first.get('upcoming'), 1)
        self.assertEqual(first.get('started'), 0)

        self.plugin._now_ms = lambda: start_at + 1000  # type: ignore[method-assign]
        second = self.plugin._process_notifications()
        self.assertEqual(second.get('upcoming'), 0)
        self.assertEqual(second.get('started'), 1)

        tags = [tag for tag, _ in self.calls]
        self.assertIn(OrganizerCorePlugin.EVT_ITEM_UPCOMING, tags)
        self.assertIn(OrganizerCorePlugin.EVT_ITEM_STARTED, tags)


if __name__ == '__main__':
    unittest.main()
