import os
import pathlib
import sys
import tempfile
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'mangalib_downloader', 'mangalib_downloader_plugin')
DownloadSelection = _MODULE.DownloadSelection
DownloadJob = _MODULE.DownloadJob
MangalibDownloaderPlugin = _MODULE.MangalibDownloaderPlugin


class MangalibDownloaderPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = MangalibDownloaderPlugin()
        self.spoken = []
        self.replies = []
        self.alt_calls = []
        self.plugin.request_say_intent = (  # type: ignore[method-assign]
            lambda intent, template_vars=None, emotion=None, extra=None: self.spoken.append(
                (intent, template_vars or {}, extra or {})
            )
        )
        self.plugin.request_say_direct = lambda text, **kwargs: self.spoken.append(('DIRECT', {'text': text}, {}))  # type: ignore[method-assign]
        self.plugin.reply = lambda sender, data=None: self.replies.append((sender, data))  # type: ignore[method-assign]
        self.plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.register_speech_rule = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.setup_options_panel = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.get_property = lambda key, default=None: default  # type: ignore[method-assign]
        self.plugin.set_property = lambda *args, **kwargs: None  # type: ignore[method-assign]
        self.plugin.save_properties = lambda: None  # type: ignore[method-assign]
        self.plugin.send_message_with_response = lambda *args, **kwargs: 1  # type: ignore[method-assign]
        self.plugin.call_next_alternative = (  # type: ignore[method-assign]
            lambda sender, tag, current, data=None: self.alt_calls.append((sender, tag, current, data))
        )

    def test_parse_ranobelib_target(self) -> None:
        target = self.plugin._parse_download_target('https://ranobelib.me/ru/book/overlord')

        self.assertIsNotNone(target)
        self.assertEqual(target.kind, 'ranobe')
        self.assertEqual(target.slug, 'overlord')
        self.assertEqual(target.url, 'https://ranobelib.me/ru/book/overlord')

    def test_parse_ranobelib_target_from_full_phrase_keeps_clean_url(self) -> None:
        target = self.plugin._parse_download_target(
            'скачай ранобе https://ranobelib.me/ru/book/39562--otonari-no-tenshi?section=info&ui=9273500'
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.kind, 'ranobe')
        self.assertEqual(target.slug, '39562--otonari-no-tenshi')
        self.assertEqual(
            target.url,
            'https://ranobelib.me/ru/book/39562--otonari-no-tenshi?section=info&ui=9273500',
        )

    def test_parse_mangalib_target(self) -> None:
        target = self.plugin._parse_download_target('https://mangalib.me/ru/manga/berserk')

        self.assertIsNotNone(target)
        self.assertEqual(target.kind, 'manga')
        self.assertEqual(target.slug, 'berserk')

    def test_parse_ranobe_volume_selection_supports_mixed_ranges(self) -> None:
        selection, error = self.plugin._parse_ranobe_selection(
            'скачай ранобе https://ranobelib.me/ru/book/overlord том 1,2, 5-7'
        )

        self.assertEqual(error, '')
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.kind, 'volume')
        self.assertEqual(selection.values, ('1', '2'))
        self.assertEqual(selection.ranges, ((5, 7),))

    def test_parse_ranobe_chapter_selection_supports_list(self) -> None:
        selection, error = self.plugin._parse_ranobe_selection(
            'скачай ранобе https://ranobelib.me/ru/book/overlord главы 3,4'
        )

        self.assertEqual(error, '')
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.kind, 'chapter')
        self.assertEqual(selection.values, ('3', '4'))
        self.assertEqual(selection.ranges, ())

    def test_parse_ranobe_selection_reports_invalid_syntax(self) -> None:
        selection, error = self.plugin._parse_ranobe_selection(
            'скачай ранобе https://ranobelib.me/ru/book/overlord том 1,,2'
        )

        self.assertIsNone(selection)
        self.assertTrue(error)

    def test_resolve_target_from_speech_payload_prefers_full_text_url(self) -> None:
        target, request_text = self.plugin._resolve_target_from_data(
            {
                'request': (
                    'https ranobelib me ru book '
                    '39562--otonari-no-tenshi-sama-ni-itsu-no-ma-ni-ka-dame-ningen-ni-sareteita-ken-wn '
                    'section info ui 9273500'
                ),
                'text': (
                    'Скачай ранобе '
                    'https://ranobelib.me/ru/book/'
                    '39562--otonari-no-tenshi-sama-ni-itsu-no-ma-ni-ka-dame-ningen-ni-sareteita-ken-wn'
                    '?section=info&ui=9273500'
                ),
            }
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.kind, 'ranobe')
        self.assertIn('https://ranobelib.me/ru/book/', request_text)

    def test_resolve_ranobe_selection_from_data_prefers_full_text(self) -> None:
        selection, error = self.plugin._resolve_ranobe_selection_from_data(
            {
                'request': 'https://ranobelib.me/ru/book/39562--otonari-no-tenshi',
                'text': (
                    'Скачай ранобе '
                    'https://ranobelib.me/ru/book/39562--otonari-no-tenshi '
                    'том 1, 2,5'
                ),
            }
        )

        self.assertEqual(error, '')
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.kind, 'volume')
        self.assertEqual(selection.values, ('1', '2', '5'))

    def test_request_headers_follow_target_page(self) -> None:
        target = self.plugin._parse_download_target(
            'https://mangalib.me/ru/manga/249868--body?section=chapters&ui=9273500'
        )

        self.assertIsNotNone(target)
        headers = self.plugin._request_headers_for_target(target, auth_token='secret-token')

        self.assertEqual(headers['Origin'], 'https://mangalib.me')
        self.assertEqual(
            headers['Referer'],
            'https://mangalib.me/ru/manga/249868--body?section=chapters&ui=9273500',
        )
        self.assertIn('application/json', headers['Accept'])
        self.assertEqual(headers['Authorization'], 'Bearer secret-token')

    def test_extract_access_token_from_storage_response(self) -> None:
        token = self.plugin._extract_access_token_from_storage_response(
            {
                'localStorage': {
                    'auth': '{"token":{"access_token":"abc123"}}',
                },
            }
        )

        self.assertEqual(token, 'abc123')

    def test_normalize_request_url_encodes_spaces_in_path(self) -> None:
        normalized = self.plugin._normalize_request_url(
            'https://ranobelib.me/uploads/ranobe/title/chapters/723356/1 - MG9K6NU_n1LE.jpeg'
        )

        self.assertEqual(
            normalized,
            'https://ranobelib.me/uploads/ranobe/title/chapters/723356/1%20-%20MG9K6NU_n1LE.jpeg',
        )

    def test_resolve_target_title_prefers_browser_title_hint(self) -> None:
        target = self.plugin._parse_download_target('https://mangalib.me/ru/manga/249868--body')

        self.assertIsNotNone(target)
        job = DownloadJob(
            token=1,
            kind='manga',
            target=target,
            download_dir='/tmp',
            download_dir_label='Downloads',
            title_hint='BODY - MangaLib',
        )

        self.assertEqual(self.plugin._resolve_target_title(job), 'BODY')

    def test_supported_site_hint_is_emitted_once(self) -> None:
        payload = {
            'ok': True,
            'tabs': [
                {'url': 'https://mangalib.me/ru/manga/berserk'},
            ],
        }

        self.plugin.on_browser_tabs_snapshot('browser', payload, _MODULE.EVENT_TABS_SNAPSHOT)
        self.plugin.on_browser_tabs_snapshot('browser', payload, _MODULE.EVENT_TABS_SNAPSHOT)

        self.assertEqual(
            self.spoken,
            [
                ('MANGALIB_DOWNLOADER_SITE_HINT', {}, {}),
            ],
        )

    def test_group_chapters_by_volume_sorts_rows(self) -> None:
        grouped = self.plugin._group_chapters_by_volume(
            [
                {'volume': '2', 'number': '3'},
                {'volume': '1', 'number': '10'},
                {'volume': '1', 'number': '2'},
            ]
        )

        self.assertEqual([item[0] for item in grouped], ['1', '2'])
        self.assertEqual(
            [row['number'] for row in grouped[0][1]],
            ['2', '10'],
        )

    def test_handle_item_saved_event_uses_volume_intent(self) -> None:
        self.plugin._handle_item_saved_event(
            {
                'itemKind': 'volume',
                'itemLabel': 'Том 1',
                'targetDir': 'Загрузки',
            }
        )

        self.assertEqual(
            self.spoken,
            [
                (
                    'MANGALIB_DOWNLOADER_VOLUME_SAVED',
                    {'item': 'Том 1', 'targetDir': 'Загрузки'},
                    {'item': 'Том 1', 'targetDir': 'Загрузки'},
                ),
            ],
        )

    def test_handle_item_saved_event_uses_chapter_intent(self) -> None:
        self.plugin._handle_item_saved_event(
            {
                'itemKind': 'chapter',
                'itemLabel': 'Глава 7',
                'targetDir': 'Загрузки',
            }
        )

        self.assertEqual(
            self.spoken,
            [
                (
                    'MANGALIB_DOWNLOADER_CHAPTER_SAVED',
                    {'item': 'Глава 7', 'targetDir': 'Загрузки'},
                    {'item': 'Глава 7', 'targetDir': 'Загрузки'},
                ),
            ],
        )

    def test_repeat_manga_download_skips_existing_chapter_file(self) -> None:
        target = self.plugin._parse_download_target('https://mangalib.me/ru/manga/249868--body')
        self.assertIsNotNone(target)

        with tempfile.TemporaryDirectory() as tmp_dir:
            job = DownloadJob(
                token=1,
                kind='manga',
                target=target,
                download_dir=tmp_dir,
                download_dir_label='Загрузки',
                title_hint='BODY - MangaLib',
            )
            _, _, target_dir_path = self.plugin._prepare_target_directory(job)
            existing_path = os.path.join(
                target_dir_path,
                self.plugin._chapter_file_name(self.plugin._chapter_label('1', '1')),
            )
            with open(existing_path, 'wb') as fh:
                fh.write(b'cbz')

            requested_urls = []

            def fake_request_json(url, headers=None):
                requested_urls.append(url)
                if url.endswith('/chapters'):
                    return {
                        'data': [
                            {'volume': '1', 'number': '1'},
                        ]
                    }
                raise AssertionError('chapter endpoint should not be called for an existing file')

            self.plugin._request_json = fake_request_json  # type: ignore[method-assign]
            result = self.plugin._download_manga(job)

        self.assertEqual(result.saved_items, 0)
        self.assertEqual(result.skipped_items, 1)
        self.assertEqual(len(requested_urls), 1)

    def test_repeat_ranobe_download_skips_existing_volume_file(self) -> None:
        target = self.plugin._parse_download_target('https://ranobelib.me/ru/book/49959--daemabeobsaui-tta')
        self.assertIsNotNone(target)

        with tempfile.TemporaryDirectory() as tmp_dir:
            job = DownloadJob(
                token=1,
                kind='ranobe',
                target=target,
                download_dir=tmp_dir,
                download_dir_label='Загрузки',
                title_hint='Daemabeobsaui Tta - RanobeLib',
            )
            _, _, target_dir_path = self.plugin._prepare_target_directory(job)
            existing_path = os.path.join(
                target_dir_path,
                self.plugin._volume_file_name(self.plugin._volume_label('1')),
            )
            with open(existing_path, 'wb') as fh:
                fh.write(b'fb2')

            requested_urls = []

            def fake_request_json(url, headers=None):
                requested_urls.append(url)
                if url.endswith('/chapters'):
                    return {
                        'data': [
                            {'volume': '1', 'number': '1'},
                        ]
                    }
                raise AssertionError('chapter endpoint should not be called for an existing file')

            self.plugin._request_json = fake_request_json  # type: ignore[method-assign]
            result = self.plugin._download_ranobe(job)

        self.assertEqual(result.saved_items, 0)
        self.assertEqual(result.skipped_items, 1)
        self.assertEqual(len(requested_urls), 1)

    def test_ranobe_download_keeps_fractional_volume_as_separate_book(self) -> None:
        target = self.plugin._parse_download_target('https://ranobelib.me/ru/book/39562--otonari-no-tenshi')
        self.assertIsNotNone(target)

        with tempfile.TemporaryDirectory() as tmp_dir:
            job = DownloadJob(
                token=1,
                kind='ranobe',
                target=target,
                download_dir=tmp_dir,
                download_dir_label='Загрузки',
                title_hint='Otonari no tenshi',
            )
            requested_urls = []

            def fake_request_json(url, headers=None):
                requested_urls.append(url)
                if url.endswith('/chapters'):
                    return {
                        'data': [
                            {'volume': '11.5', 'number': '1'},
                        ]
                    }
                return {
                    'data': {
                        'content': '<p>Test chapter</p>',
                    }
                }

            self.plugin._request_json = fake_request_json  # type: ignore[method-assign]
            self.plugin._request_bytes = lambda url, headers=None: b''  # type: ignore[method-assign]
            result = self.plugin._download_ranobe(job)
            files = os.listdir(os.path.join(tmp_dir, 'Otonari no tenshi'))

        self.assertEqual(result.saved_items, 1)
        self.assertEqual(result.skipped_items, 0)
        self.assertEqual(len(requested_urls), 2)
        self.assertIn('Том 11.5.fb2', files)

    def test_ranobe_download_filters_selected_volumes(self) -> None:
        target = self.plugin._parse_download_target('https://ranobelib.me/ru/book/overlord')
        self.assertIsNotNone(target)

        with tempfile.TemporaryDirectory() as tmp_dir:
            job = DownloadJob(
                token=1,
                kind='ranobe',
                target=target,
                download_dir=tmp_dir,
                download_dir_label='Загрузки',
                title_hint='Overlord',
                selection=DownloadSelection(kind='volume', values=('2',), raw='2'),
            )
            requested_urls = []

            def fake_request_json(url, headers=None):
                requested_urls.append(url)
                if url.endswith('/chapters'):
                    return {
                        'data': [
                            {'volume': '1', 'number': '1'},
                            {'volume': '2', 'number': '1'},
                        ]
                    }
                if 'volume=2' in url and 'number=1' in url:
                    return {
                        'data': {
                            'content': '<p>Volume 2 chapter 1</p>',
                        }
                    }
                raise AssertionError(f'unexpected chapter request: {url}')

            self.plugin._request_json = fake_request_json  # type: ignore[method-assign]
            self.plugin._request_bytes = lambda url, headers=None: b''  # type: ignore[method-assign]
            result = self.plugin._download_ranobe(job)
            files = os.listdir(os.path.join(tmp_dir, 'Overlord'))

        self.assertEqual(result.saved_items, 1)
        self.assertEqual(result.skipped_items, 0)
        self.assertEqual(len(requested_urls), 2)
        self.assertEqual(files, ['Том 2.fb2'])

    def test_ranobe_download_selected_chapters_are_saved_as_separate_fb2(self) -> None:
        target = self.plugin._parse_download_target('https://ranobelib.me/ru/book/overlord')
        self.assertIsNotNone(target)

        with tempfile.TemporaryDirectory() as tmp_dir:
            job = DownloadJob(
                token=1,
                kind='ranobe',
                target=target,
                download_dir=tmp_dir,
                download_dir_label='Загрузки',
                title_hint='Overlord',
                selection=DownloadSelection(kind='chapter', ranges=((3, 4),), raw='3-4'),
            )
            requested_urls = []

            def fake_request_json(url, headers=None):
                requested_urls.append(url)
                if url.endswith('/chapters'):
                    return {
                        'data': [
                            {'volume': '1', 'number': '2'},
                            {'volume': '1', 'number': '3'},
                            {'volume': '1', 'number': '4'},
                            {'volume': '2', 'number': '4'},
                        ]
                    }
                if 'number=3' in url and 'volume=1' in url:
                    return {'data': {'content': '<p>Chapter 3</p>', 'name': 'Three'}}
                if 'number=4' in url and 'volume=1' in url:
                    return {'data': {'content': '<p>Chapter 4</p>', 'name': 'Four A'}}
                if 'number=4' in url and 'volume=2' in url:
                    return {'data': {'content': '<p>Chapter 4</p>', 'name': 'Four B'}}
                raise AssertionError(f'unexpected chapter request: {url}')

            self.plugin._request_json = fake_request_json  # type: ignore[method-assign]
            self.plugin._request_bytes = lambda url, headers=None: b''  # type: ignore[method-assign]
            result = self.plugin._download_ranobe(job)
            files = sorted(os.listdir(os.path.join(tmp_dir, 'Overlord')))

        self.assertEqual(result.saved_items, 3)
        self.assertEqual(result.skipped_items, 0)
        self.assertEqual(len(requested_urls), 4)
        self.assertEqual(
            files,
            [
                'Том 1 Глава 3.fb2',
                'Том 1 Глава 4.fb2',
                'Том 2 Глава 4.fb2',
            ],
        )

    def test_download_mismatch_reports_error(self) -> None:
        self.plugin.on_download_ranobe(
            'tester',
            {'request': 'https://mangalib.me/ru/manga/berserk'},
            _MODULE.CMD_DOWNLOAD_RANOBE,
        )

        self.assertTrue(self.replies)
        self.assertFalse(self.replies[-1][1]['ok'])
        self.assertEqual(self.replies[-1][1]['error'], 'kind_mismatch')

    def test_generic_download_handler_hands_off_unsupported_link(self) -> None:
        payload = {'request': 'https://example.com/file.zip'}

        self.plugin.on_download_by_link('tester', payload, _MODULE.CMD_DOWNLOAD_BY_LINK)

        self.assertEqual(
            self.alt_calls,
            [('tester', _MODULE.GENERIC_DOWNLOAD_BY_LINK_TAG, _MODULE.CMD_DOWNLOAD_BY_LINK, payload)],
        )
        self.assertEqual(self.replies, [])

    def test_generic_download_handler_starts_supported_link_with_detected_kind(self) -> None:
        calls = []

        self.plugin._start_download_from_resolved = (  # type: ignore[method-assign]
            lambda sender, data, **kwargs: calls.append((sender, data, kwargs))
        )

        payload = {'request': 'https://mangalib.me/ru/manga/berserk'}
        self.plugin.on_download_by_link('tester', payload, _MODULE.CMD_DOWNLOAD_BY_LINK)

        self.assertEqual(self.alt_calls, [])
        self.assertEqual(len(calls), 1)
        sender, data, kwargs = calls[0]
        self.assertEqual(sender, 'tester')
        self.assertEqual(data, payload)
        self.assertEqual(kwargs['expected_kind'], 'manga')
        self.assertIsNotNone(kwargs['target'])


if __name__ == '__main__':
    unittest.main()
