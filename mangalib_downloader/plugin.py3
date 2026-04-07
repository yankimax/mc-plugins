#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


EVENT_TABS_SNAPSHOT = 'browser-extension:tabs-snapshot'
EVENT_ACTIVE_TAB_SNAPSHOT = 'browser-extension:active-tab-snapshot'
COMMAND_GET_TAB_LOCAL_STORAGE = 'browser-extension:get-tab-local-storage'
TAG_GUI_REQUEST_PANELS = 'gui:request-panels'
TAG_UPDATE_SETTINGS = 'mangalib-downloader:update-settings'
GENERIC_DOWNLOAD_BY_LINK_TAG = 'download:by-link'

CMD_DOWNLOAD_RANOBE = 'mangalib-downloader:download-ranobe'
CMD_DOWNLOAD_MANGA = 'mangalib-downloader:download-manga'
CMD_DOWNLOAD_BY_LINK = 'mangalib-downloader:download-by-link'
DOWNLOAD_BY_LINK_PRIORITY = 100

INTENT_SITE_HINT = 'MANGALIB_DOWNLOADER_SITE_HINT'
INTENT_VOLUME_SAVED = 'MANGALIB_DOWNLOADER_VOLUME_SAVED'
INTENT_CHAPTER_SAVED = 'MANGALIB_DOWNLOADER_CHAPTER_SAVED'

USER_AGENT = 'MinaChan/1.0'
QUEUE_POLL_DELAY_MS = 350
RATELIMIT_DELAY_SEC = 60.3
RANOBE_CHAPTER_DELAY_SEC = 0.56
MANGA_PAGE_DELAY_SEC = 0.15
REQUEST_RETRY_DELAY_SEC = 0.35
API_BASE_URL = 'https://api.cdnlibs.org/api/manga'
MANGA_CDN_URL = 'https://img3.mixlib.me'

_RANOBE_URL_RE = re.compile(
    r'(https?://(?:www\.)?ranobelib\.me/[^?#\s]*/book/([A-Za-z0-9_-]+)(?:\?[^#\s]*)?)',
    re.IGNORECASE,
)
_MANGA_URL_RE = re.compile(
    r'(https?://(?:www\.)?mangalib\.me/[^?#\s]*/manga/([A-Za-z0-9_-]+)(?:\?[^#\s]*)?)',
    re.IGNORECASE,
)
_RANOBE_IMAGE_RE = re.compile(r'(<img [^>]*src=")https://ranobelib\.me/[^"]+/([^/"]+)("[^>]*>)', re.IGNORECASE)
_RANOBE_SELECTION_RE = re.compile(
    r'\b(том(?:а|ов)?|глава|главы|глав)\b\s+(.+)$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DownloadTarget:
    kind: str
    slug: str
    url: str
    origin: str
    site_name: str


@dataclass(frozen=True)
class DownloadSelection:
    kind: str
    values: Tuple[str, ...] = ()
    ranges: Tuple[Tuple[int, int], ...] = ()
    raw: str = ''


@dataclass(frozen=True)
class DownloadJob:
    token: int
    kind: str
    target: DownloadTarget
    download_dir: str
    download_dir_label: str
    title_hint: str = ''
    auth_token: str = ''
    sender: str = ''
    selection: Optional[DownloadSelection] = None


@dataclass(frozen=True)
class DownloadResult:
    saved_items: int
    skipped_items: int
    target_dir_path: str
    target_dir_name: str


class MangalibDownloaderPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._download_dir = self._default_download_dir()
        self._hint_emitted = False
        self._job_token = 0
        self._job_preparing = False
        self._job_running = False
        self._job_sender = ''
        self._job_kind = ''
        self._job_target_slug = ''
        self._job_thread: Optional[threading.Thread] = None
        self._job_events: 'queue.Queue[Dict[str, Any]]' = queue.Queue()
        self._poll_timer_id = -1

    def on_init(self) -> None:
        self._load_settings()
        self.add_listener(
            CMD_DOWNLOAD_RANOBE,
            self.on_download_ranobe,
            listener_id='mangalib_downloader_ranobe',
        )
        self.add_listener(
            CMD_DOWNLOAD_MANGA,
            self.on_download_manga,
            listener_id='mangalib_downloader_manga',
        )
        self.add_listener(
            CMD_DOWNLOAD_BY_LINK,
            self.on_download_by_link,
            listener_id='mangalib_downloader_by_link',
        )
        self.add_listener(
            EVENT_TABS_SNAPSHOT,
            self.on_browser_tabs_snapshot,
            listener_id='mangalib_downloader_tabs_snapshot',
        )
        self.add_listener(
            EVENT_ACTIVE_TAB_SNAPSHOT,
            self.on_browser_active_tab_snapshot,
            listener_id='mangalib_downloader_active_tab_snapshot',
        )
        self.add_listener(
            TAG_GUI_REQUEST_PANELS,
            self.on_request_panels,
            listener_id='mangalib_downloader_request_panels',
        )
        self.add_listener(
            TAG_UPDATE_SETTINGS,
            self.on_update_settings,
            listener_id='mangalib_downloader_update_settings',
        )

        self.register_command(
            CMD_DOWNLOAD_RANOBE,
            {
                'en': 'Download ranobe by link into configured folder',
                'ru': 'Скачать ранобэ по ссылке в настроенную папку',
            },
            {
                'url': 'Ranobelib URL',
                'request': 'URL text',
                'text': 'Free-form link text',
            },
        )
        self.register_command(
            CMD_DOWNLOAD_MANGA,
            {
                'en': 'Download manga by link into configured folder',
                'ru': 'Скачать мангу по ссылке в настроенную папку',
            },
            {
                'url': 'Mangalib URL',
                'request': 'URL text',
                'text': 'Free-form link text',
            },
        )
        self.set_alternative(
            GENERIC_DOWNLOAD_BY_LINK_TAG,
            CMD_DOWNLOAD_BY_LINK,
            DOWNLOAD_BY_LINK_PRIORITY,
        )

        self.register_speech_rule(
            CMD_DOWNLOAD_RANOBE,
            {'ru': 'скачай ранобе {request:Text}', 'en': 'download ranobe {request:Text}'},
        )
        self.register_speech_rule(
            CMD_DOWNLOAD_RANOBE,
            {'ru': 'скачай ранобэ {request:Text}', 'en': 'download novel {request:Text}'},
        )
        self.register_speech_rule(
            CMD_DOWNLOAD_MANGA,
            {'ru': 'скачай мангу {request:Text}', 'en': 'download manga {request:Text}'},
        )

        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

    def on_download_ranobe(self, sender: str, data: Any, tag: str) -> None:
        self._start_download_from_request(sender, data, expected_kind='ranobe')

    def on_download_manga(self, sender: str, data: Any, tag: str) -> None:
        self._start_download_from_request(sender, data, expected_kind='manga')

    def on_download_by_link(self, sender: str, data: Any, tag: str) -> None:
        target, request_text = self._resolve_target_from_data(data)
        if target is None:
            self.call_next_alternative(
                sender,
                GENERIC_DOWNLOAD_BY_LINK_TAG,
                CMD_DOWNLOAD_BY_LINK,
                data,
            )
            return
        self._start_download_from_resolved(
            sender,
            data,
            target=target,
            request_text=request_text,
            expected_kind=target.kind,
        )

    def on_browser_tabs_snapshot(self, sender: str, data: Any, tag: str) -> None:
        self._maybe_emit_site_hint(data)

    def on_browser_active_tab_snapshot(self, sender: str, data: Any, tag: str) -> None:
        self._maybe_emit_site_hint(data)

    def on_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._register_settings_gui()

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return
        raw_value = str(data.get('download_dir') or '').strip()
        next_dir = self._normalize_download_dir(raw_value or self._default_download_dir())
        self._download_dir = next_dir
        self.set_property('downloadDir', next_dir)
        self.save_properties()
        self._register_settings_gui()
        self.request_say_direct(
            self._tr(
                f'Download folder updated: {next_dir}',
                f'Папка загрузки обновлена: {next_dir}',
            )
        )

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale
        self._register_settings_gui()

    def _load_settings(self) -> None:
        stored = str(self.get_property('downloadDir', '') or '').strip()
        self._download_dir = self._normalize_download_dir(stored or self._default_download_dir())

    def _register_settings_gui(self) -> None:
        texts = self._ui_texts()
        self.setup_options_panel(
            panel_id='mangalib_downloader_settings',
            name=texts['panel_name'],
            msg_tag=TAG_UPDATE_SETTINGS,
            controls=[
                {
                    'id': 'description',
                    'type': 'label',
                    'label': texts['description'],
                },
                {
                    'id': 'download_dir',
                    'type': 'textfield',
                    'label': texts['download_dir_label'],
                    'value': self._download_dir,
                },
            ],
        )

    def _start_download_from_request(
        self,
        sender: str,
        data: Any,
        *,
        expected_kind: str,
    ) -> None:
        target, request_text = self._resolve_target_from_data(data)
        self._start_download_from_resolved(
            sender,
            data,
            target=target,
            request_text=request_text,
            expected_kind=expected_kind,
        )

    def _start_download_from_resolved(
        self,
        sender: str,
        data: Any,
        *,
        target: Optional[DownloadTarget],
        request_text: str,
        expected_kind: str,
    ) -> None:
        if self._job_running or self._job_preparing:
            message = self._tr(
                'I am already downloading another title.',
                'Я уже скачиваю другой тайтл.',
            )
            self.request_say_direct(message)
            self._reply(sender, {'ok': False, 'error': 'job_already_running'})
            return

        if not request_text:
            message = self._tr(
                'Please send a direct link to the title page.',
                'Пришли прямую ссылку на страницу тайтла.',
            )
            self.request_say_direct(message)
            self._reply(sender, {'ok': False, 'error': 'missing_url'})
            return

        if target is None:
            message = self._tr(
                'This link is not supported yet.',
                'Эта ссылка пока не поддерживается.',
            )
            self.request_say_direct(message)
            self._reply(sender, {'ok': False, 'error': 'unsupported_url'})
            return

        if target.kind != expected_kind:
            message = self._tr(
                'The link type does not match this command.',
                'Тип ссылки не совпадает с этой командой.',
            )
            self.request_say_direct(message)
            self._reply(
                sender,
                {
                    'ok': False,
                    'error': 'kind_mismatch',
                    'expected': expected_kind,
                    'actual': target.kind,
                },
            )
            return

        selection: Optional[DownloadSelection] = None
        if expected_kind == 'ranobe':
            selection, selection_error = self._resolve_ranobe_selection_from_data(data)
            if selection_error:
                self.request_say_direct(selection_error)
                self._reply(sender, {'ok': False, 'error': 'invalid_selection'})
                return

        self._job_preparing = True
        self._request_browser(
            COMMAND_GET_TAB_LOCAL_STORAGE,
            {
                'url': target.url,
                'keys': ['auth'],
            },
            lambda response: self._continue_start_download(
                sender=sender,
                expected_kind=expected_kind,
                target=target,
                response=response,
                selection=selection,
            ),
        )

    def _continue_start_download(
        self,
        *,
        sender: str,
        expected_kind: str,
        target: DownloadTarget,
        response: Dict[str, Any],
        selection: Optional[DownloadSelection],
    ) -> None:
        self._job_preparing = False
        if self._job_running:
            self.request_say_direct(
                self._tr(
                    'I am already downloading another title.',
                    'Я уже скачиваю другой тайтл.',
                )
            )
            self._reply(sender, {'ok': False, 'error': 'job_already_running'})
            return

        download_dir = self._download_dir
        dir_label = self._download_dir_label(download_dir)
        title_hint = self._extract_title_hint_from_storage_response(target, response)
        auth_token = self._extract_access_token_from_storage_response(response)

        self._job_token += 1
        token = self._job_token
        self._job_running = True
        self._job_sender = sender
        self._job_kind = expected_kind
        self._job_target_slug = target.slug

        job = DownloadJob(
            token=token,
            kind=expected_kind,
            target=target,
            download_dir=download_dir,
            download_dir_label=dir_label,
            title_hint=title_hint,
            auth_token=auth_token,
            sender=sender,
            selection=selection,
        )

        self._job_thread = threading.Thread(
            target=self._worker_run,
            args=(job,),
            name=f'mangalib_downloader_{token}',
            daemon=True,
        )
        self._job_thread.start()
        self._ensure_poll_timer()

        self.request_say_direct(
            self._tr(
                f'Starting download for {target.slug}.',
                f'Начинаю скачивание {target.slug}.',
            )
        )
        self._reply(
            sender,
            {
                'ok': True,
                'started': True,
                'kind': expected_kind,
                'slug': target.slug,
                'downloadDir': download_dir,
                'titleHint': title_hint,
                'hasAuth': bool(auth_token),
                'selectionKind': selection.kind if selection is not None else '',
                'selectionRaw': selection.raw if selection is not None else '',
            },
        )

    def _worker_run(self, job: DownloadJob) -> None:
        try:
            os.makedirs(job.download_dir, exist_ok=True)
            if job.kind == 'ranobe':
                result = self._download_ranobe(job)
            else:
                result = self._download_manga(job)
            self._job_events.put(
                {
                    'type': 'finished',
                    'token': job.token,
                    'savedItems': result.saved_items,
                    'skippedItems': result.skipped_items,
                    'targetDirPath': result.target_dir_path,
                    'targetDirName': result.target_dir_name,
                    'kind': job.kind,
                }
            )
        except Exception as error:
            self._job_events.put(
                {
                    'type': 'failed',
                    'token': job.token,
                    'message': self._format_download_error(error, job),
                }
            )

    def _download_ranobe(self, job: DownloadJob) -> DownloadResult:
        title_name, target_dir_name, target_dir_path = self._prepare_target_directory(job)
        request_headers = self._request_headers_for_target(
            job.target,
            auth_token=job.auth_token,
        )
        chapters_payload = self._request_json(
            self._chapters_url(job.target.slug),
            headers=request_headers,
        )
        chapters_raw = chapters_payload.get('data')
        if not isinstance(chapters_raw, list) or not chapters_raw:
            raise ValueError('ranobe chapters list is empty')

        chapters_selected = self._filter_ranobe_chapters(chapters_raw, job.selection)
        if job.selection is not None and not chapters_selected:
            raise ValueError(self._empty_ranobe_selection_message(job.selection))
        if job.selection is not None and job.selection.kind == 'chapter':
            return self._download_ranobe_chapters(
                job,
                chapters_selected,
                request_headers,
                title_name,
                target_dir_name,
                target_dir_path,
            )

        volumes = self._group_chapters_by_volume(chapters_selected)
        saved_items = 0
        skipped_items = 0
        for volume_value, chapter_rows in volumes:
            volume_text = self._volume_label(volume_value)
            file_name = self._volume_file_name(volume_text)
            output_path = os.path.join(target_dir_path, file_name)
            if os.path.exists(output_path):
                skipped_items += 1
                continue

            body_parts: List[str] = []
            binary_parts: List[str] = []
            written_binary_ids: Set[str] = set()

            for chapter in chapter_rows:
                chapter_document = self._load_ranobe_chapter_document(
                    job,
                    chapter,
                    request_headers,
                )
                if chapter_document is None:
                    continue
                _, body_xml, chapter_binaries = chapter_document
                body_parts.append(body_xml)

                for binary_id, content_type, encoded in chapter_binaries:
                    if binary_id in written_binary_ids:
                        continue
                    binary_parts.append(self._ranobe_binary_xml(binary_id, content_type, encoded))
                    written_binary_ids.add(binary_id)

            if not body_parts:
                continue

            book_title = self._tr(
                f'{title_name} {volume_text}',
                f'{title_name} {volume_text}',
            )
            fb2_text = self._compose_ranobe_fb2(book_title, body_parts, binary_parts)
            with open(output_path, 'w', encoding='utf-8', errors='replace') as fh:
                fh.write(fb2_text)

            self._job_events.put(
                {
                    'type': 'item_saved',
                    'token': job.token,
                    'itemKind': 'volume',
                    'itemLabel': volume_text,
                    'targetDir': job.download_dir_label,
                    'path': output_path,
                }
            )
            saved_items += 1

        return DownloadResult(
            saved_items=saved_items,
            skipped_items=skipped_items,
            target_dir_path=target_dir_path,
            target_dir_name=target_dir_name,
        )

    def _download_ranobe_chapters(
        self,
        job: DownloadJob,
        chapters: Sequence[Dict[str, Any]],
        request_headers: Dict[str, str],
        title_name: str,
        target_dir_name: str,
        target_dir_path: str,
    ) -> DownloadResult:
        saved_items = 0
        skipped_items = 0

        for chapter in sorted(chapters, key=self._chapter_sort_key):
            chapter_number = self._string_value(chapter.get('number'))
            chapter_volume = self._string_value(chapter.get('volume'))
            chapter_label = self._chapter_label(chapter_number, chapter_volume)
            output_path = os.path.join(
                target_dir_path,
                self._ranobe_chapter_file_name(chapter_label),
            )
            if os.path.exists(output_path):
                skipped_items += 1
                continue

            chapter_document = self._load_ranobe_chapter_document(
                job,
                chapter,
                request_headers,
            )
            if chapter_document is None:
                continue

            chapter_title, body_xml, chapter_binaries = chapter_document
            binary_parts = [
                self._ranobe_binary_xml(binary_id, content_type, encoded)
                for binary_id, content_type, encoded in chapter_binaries
            ]
            fb2_text = self._compose_ranobe_fb2(
                self._tr(
                    f'{title_name} {chapter_label}',
                    f'{title_name} {chapter_label}',
                ),
                [body_xml],
                binary_parts,
            )
            with open(output_path, 'w', encoding='utf-8', errors='replace') as fh:
                fh.write(fb2_text)

            self._job_events.put(
                {
                    'type': 'item_saved',
                    'token': job.token,
                    'itemKind': 'chapter',
                    'itemLabel': chapter_title,
                    'targetDir': job.download_dir_label,
                    'path': output_path,
                }
            )
            saved_items += 1

        return DownloadResult(
            saved_items=saved_items,
            skipped_items=skipped_items,
            target_dir_path=target_dir_path,
            target_dir_name=target_dir_name,
        )

    def _download_manga(self, job: DownloadJob) -> DownloadResult:
        _, target_dir_name, target_dir_path = self._prepare_target_directory(job)
        request_headers = self._request_headers_for_target(
            job.target,
            auth_token=job.auth_token,
        )
        chapters_payload = self._request_json(
            self._chapters_url(job.target.slug),
            headers=request_headers,
        )
        chapters_raw = chapters_payload.get('data')
        if not isinstance(chapters_raw, list) or not chapters_raw:
            raise ValueError('manga chapters list is empty')

        chapters_sorted = sorted(chapters_raw, key=self._chapter_sort_key)
        saved_items = 0
        skipped_items = 0

        for chapter in chapters_sorted:
            chapter_number = self._string_value(chapter.get('number'))
            chapter_volume = self._string_value(chapter.get('volume'))
            chapter_label = self._chapter_label(chapter_number, chapter_volume)
            file_name = self._chapter_file_name(chapter_label)
            output_path = os.path.join(target_dir_path, file_name)
            if os.path.exists(output_path):
                skipped_items += 1
                continue

            query = urllib.parse.urlencode(
                {'number': chapter_number, 'volume': chapter_volume}
            )
            payload = self._request_json(
                f'{self._chapters_url(job.target.slug).rsplit("/", 1)[0]}/chapter?{query}',
                headers=request_headers,
            )
            chapter_data = payload.get('data')
            if not isinstance(chapter_data, dict):
                continue

            pages = chapter_data.get('pages')
            if not isinstance(pages, list) or not pages:
                continue

            written_pages = 0

            with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    page_slug = self._int_or_none(page.get('slug')) or (written_pages + 1)
                    image_name = self._string_value(page.get('image')) or f'{page_slug:03d}.jpg'
                    image_ext = os.path.splitext(image_name)[1] or '.jpg'
                    image_path = self._string_value(page.get('url'))
                    if image_path.startswith('http://') or image_path.startswith('https://'):
                        image_url = image_path
                    else:
                        image_url = f'{MANGA_CDN_URL}{image_path}'

                    blob = self._request_bytes(image_url, headers=request_headers)
                    if blob is None:
                        continue
                    archive.writestr(f'{page_slug:03d}{image_ext}', blob)
                    written_pages += 1
                    time.sleep(MANGA_PAGE_DELAY_SEC)

            if written_pages <= 0:
                try:
                    os.remove(output_path)
                except Exception:
                    pass
                continue

            self._job_events.put(
                {
                    'type': 'item_saved',
                    'token': job.token,
                    'itemKind': 'chapter',
                    'itemLabel': chapter_label,
                    'targetDir': job.download_dir_label,
                    'path': output_path,
                }
            )
            saved_items += 1

        return DownloadResult(
            saved_items=saved_items,
            skipped_items=skipped_items,
            target_dir_path=target_dir_path,
            target_dir_name=target_dir_name,
        )

    def _ensure_poll_timer(self) -> None:
        if self._poll_timer_id >= 0:
            return
        timer_id = self.set_timer_once(QUEUE_POLL_DELAY_MS, self._on_poll_timer)
        if timer_id >= 0:
            self._poll_timer_id = timer_id

    def _on_poll_timer(
        self,
        sender: str = '',
        data: Any = None,
        tag: str = '',
    ) -> None:
        self._poll_timer_id = -1
        self._drain_worker_events()
        if self._job_running or not self._job_events.empty():
            self._ensure_poll_timer()

    def _drain_worker_events(self) -> None:
        while True:
            try:
                event = self._job_events.get_nowait()
            except queue.Empty:
                break
            if not isinstance(event, dict):
                continue
            if int(event.get('token') or -1) != self._job_token:
                continue
            kind = str(event.get('type') or '').strip()
            if kind == 'item_saved':
                self._handle_item_saved_event(event)
                continue
            if kind == 'finished':
                self._job_preparing = False
                self._job_running = False
                self._job_thread = None
                saved_items = int(event.get('savedItems') or 0)
                skipped_items = int(event.get('skippedItems') or 0)
                target_dir_path = str(event.get('targetDirPath') or '').strip()
                target_dir_name = str(event.get('targetDirName') or '').strip()
                self.request_say_direct(
                    self._finish_message(saved_items, skipped_items, target_dir_name)
                )
                self._reply(
                    self._job_sender,
                    {
                        'ok': True,
                        'finished': True,
                        'savedItems': saved_items,
                        'skippedItems': skipped_items,
                        'kind': self._job_kind,
                        'slug': self._job_target_slug,
                        'downloadDir': self._download_dir,
                        'targetDirPath': target_dir_path,
                        'targetDirName': target_dir_name,
                    },
                )
                self._job_sender = ''
                self._job_kind = ''
                self._job_target_slug = ''
                continue
            if kind == 'failed':
                self._job_preparing = False
                self._job_running = False
                self._job_thread = None
                message = str(event.get('message') or 'download_failed').strip()
                self.request_say_direct(
                    self._tr(
                        f'Download failed: {message}',
                        f'Не удалось скачать: {message}',
                    )
                )
                self._reply(
                    self._job_sender,
                    {
                        'ok': False,
                        'finished': True,
                        'error': message,
                        'kind': self._job_kind,
                        'slug': self._job_target_slug,
                    },
                )
                self._job_sender = ''
                self._job_kind = ''
                self._job_target_slug = ''

    def _handle_item_saved_event(self, event: Dict[str, Any]) -> None:
        item_kind = str(event.get('itemKind') or '').strip()
        item_label = str(event.get('itemLabel') or '').strip()
        target_dir = str(event.get('targetDir') or '').strip()
        if item_kind == 'volume':
            self.request_say_intent(
                INTENT_VOLUME_SAVED,
                template_vars={'item': item_label, 'targetDir': target_dir},
                extra={'item': item_label, 'targetDir': target_dir},
            )
            return
        self.request_say_intent(
            INTENT_CHAPTER_SAVED,
            template_vars={'item': item_label, 'targetDir': target_dir},
            extra={'item': item_label, 'targetDir': target_dir},
        )

    def _maybe_emit_site_hint(self, data: Any) -> None:
        if self._hint_emitted:
            return
        if self._payload_has_supported_site(data):
            self._hint_emitted = True
            self.request_say_intent(INTENT_SITE_HINT)

    def _payload_has_supported_site(self, data: Any) -> bool:
        for url in self._urls_from_browser_payload(data):
            if self._parse_download_target(url) is not None:
                return True
        return False

    def _urls_from_browser_payload(self, data: Any) -> List[str]:
        urls: List[str] = []
        if not isinstance(data, dict):
            return urls
        tabs = data.get('tabs')
        if isinstance(tabs, list):
            for item in tabs:
                if not isinstance(item, dict):
                    continue
                url = str(item.get('url') or '').strip()
                if url:
                    urls.append(url)
        tab = data.get('tab')
        if isinstance(tab, dict):
            url = str(tab.get('url') or '').strip()
            if url:
                urls.append(url)
        return urls

    def _parse_download_target(self, text: str) -> Optional[DownloadTarget]:
        value = str(text or '').strip()
        if not value:
            return None

        ranobe_match = _RANOBE_URL_RE.search(value)
        if ranobe_match is not None:
            matched_url = str(ranobe_match.group(1) or '').strip()
            return DownloadTarget(
                kind='ranobe',
                slug=ranobe_match.group(2),
                url=matched_url,
                origin='https://ranobelib.me',
                site_name='Ranobelib',
            )

        manga_match = _MANGA_URL_RE.search(value)
        if manga_match is not None:
            matched_url = str(manga_match.group(1) or '').strip()
            return DownloadTarget(
                kind='manga',
                slug=manga_match.group(2),
                url=matched_url,
                origin='https://mangalib.me',
                site_name='Mangalib',
            )
        return None

    def _extract_request(self, data: Any) -> str:
        if isinstance(data, dict):
            for key in ('request', 'url', 'link', 'text'):
                value = str(data.get(key) or '').strip()
                if value:
                    return value
        for key in ('request', 'url', 'link', 'text'):
            value = self.message_text(data, key=key, fallback_keys=['value', 'target']).strip()
            if value:
                return value
        if isinstance(data, dict):
            return ''
        return self.text(data).strip()

    def _resolve_target_from_data(self, data: Any) -> Tuple[Optional[DownloadTarget], str]:
        seen: Set[str] = set()
        for candidate in self._request_candidates(data):
            normalized = str(candidate or '').strip()
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            target = self._parse_download_target(normalized)
            if target is not None:
                return target, normalized
        fallback = self._extract_request(data)
        return None, fallback

    def _resolve_ranobe_selection_from_data(
        self,
        data: Any,
    ) -> Tuple[Optional[DownloadSelection], str]:
        seen: Set[str] = set()
        invalid_error = ''
        for candidate in self._request_candidates(data):
            normalized = str(candidate or '').strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            selection, error = self._parse_ranobe_selection(normalized)
            if selection is not None:
                return selection, ''
            if error:
                invalid_error = error
        return None, invalid_error

    def _parse_ranobe_selection(
        self,
        text: str,
    ) -> Tuple[Optional[DownloadSelection], str]:
        value = str(text or '').strip()
        if not value:
            return None, ''

        match = _RANOBE_SELECTION_RE.search(value)
        if match is None:
            return None, ''

        selector_token = self._string_value(match.group(1)).lower()
        selector_body = self._string_value(match.group(2))
        if not selector_body:
            return None, self._invalid_ranobe_selection_message()

        selection_kind = 'volume' if selector_token.startswith('том') else 'chapter'
        values: List[str] = []
        ranges: List[Tuple[int, int]] = []

        for raw_part in selector_body.split(','):
            part = raw_part.strip()
            if not part:
                return None, self._invalid_ranobe_selection_message()

            range_match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', part)
            if range_match is not None:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                if start > end:
                    start, end = end, start
                ranges.append((start, end))
                continue

            normalized_value = self._normalize_selection_number(part)
            if not normalized_value:
                return None, self._invalid_ranobe_selection_message()
            values.append(normalized_value)

        return (
            DownloadSelection(
                kind=selection_kind,
                values=tuple(dict.fromkeys(values)),
                ranges=tuple(ranges),
                raw=selector_body,
            ),
            '',
        )

    def _request_candidates(self, data: Any) -> List[str]:
        out: List[str] = []
        if isinstance(data, dict):
            for key in ('url', 'link', 'request', 'text', 'msgData'):
                value = str(data.get(key) or '').strip()
                if value:
                    out.append(value)
        for key in ('url', 'link', 'request', 'text', 'msgData'):
            value = self.message_text(data, key=key, fallback_keys=['value', 'target']).strip()
            if value:
                out.append(value)
        raw_text = self.text(data).strip()
        if raw_text:
            out.append(raw_text)
        return out

    def _request_browser(
        self,
        command: str,
        payload: Dict[str, Any],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        responded = {'value': False}

        def _finish(response: Dict[str, Any]) -> None:
            if responded['value']:
                return
            responded['value'] = True
            callback(response)

        def _on_response(sender: str, data: Any, tag: str) -> None:
            if isinstance(data, dict):
                _finish(dict(data))
                return
            _finish({'ok': False, 'error': f'Invalid response for {command}'})

        def _on_complete(sender: str, data: Any, tag: str) -> None:
            _finish({'ok': False, 'error': f'No response for {command}'})

        seq = self.send_message_with_response(
            command,
            payload,
            on_response=_on_response,
            on_complete=_on_complete,
        )
        if seq < 0:
            _finish({'ok': False, 'error': f'Failed to send {command}'})

    def _chapters_url(self, slug: str) -> str:
        return f'{API_BASE_URL}/{urllib.parse.quote(slug)}/chapters'

    def _request_headers_for_target(
        self,
        target: DownloadTarget,
        *,
        auth_token: str = '',
    ) -> Dict[str, str]:
        origin = str(target.origin or '').strip()
        referer = str(target.url or '').strip()
        headers: Dict[str, str] = {
            'Accept': 'application/json, text/plain, */*',
        }
        if referer:
            headers['Referer'] = referer
        if origin:
            headers['Origin'] = origin
        token = str(auth_token or '').strip()
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    def _extract_access_token_from_storage_response(self, response: Dict[str, Any]) -> str:
        if not isinstance(response, dict):
            return ''
        storage = response.get('localStorage')
        if not isinstance(storage, dict):
            return ''
        return self._extract_access_token_from_storage(storage)

    def _extract_access_token_from_storage(self, storage: Dict[str, Any]) -> str:
        raw_auth = storage.get('auth')
        if not isinstance(raw_auth, str) or not raw_auth.strip():
            return ''
        try:
            payload = json.loads(raw_auth)
        except Exception:
            return ''
        if not isinstance(payload, dict):
            return ''
        token_block = payload.get('token')
        if not isinstance(token_block, dict):
            return ''
        token = str(token_block.get('access_token') or '').strip()
        return token

    def _extract_title_hint_from_storage_response(
        self,
        target: DownloadTarget,
        response: Dict[str, Any],
    ) -> str:
        if not isinstance(response, dict):
            return ''

        page = response.get('page')
        if isinstance(page, dict):
            title_hint = self._normalize_title_hint(page.get('title'), target)
            if title_hint:
                return title_hint

        tab = response.get('tab')
        if isinstance(tab, dict):
            title_hint = self._normalize_title_hint(tab.get('title'), target)
            if title_hint:
                return title_hint
        return ''

    def _normalize_title_hint(self, raw_title: Any, target: DownloadTarget) -> str:
        text = html.unescape(str(raw_title or '').strip())
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            return ''

        text = re.sub(
            r'\s*[\-|–—|/]\s*(?:MangaLIB|RanobeLIB)\s*$',
            '',
            text,
            flags=re.IGNORECASE,
        ).strip()
        text = text.strip(' -|–—/\t')

        lowered = text.lower()
        if target.kind == 'manga':
            generic_markers = (
                'читать мангу онлайн',
                'манга онлайн',
                'манхва онлайн',
                'маньхуа онлайн',
            )
        else:
            generic_markers = (
                'читать ранобэ',
                'читать ранобе',
                'новеллы онлайн',
                'ранобэ онлайн',
            )
        if any(marker in lowered for marker in generic_markers):
            return ''
        return text

    def _prepare_target_directory(self, job: DownloadJob) -> Tuple[str, str, str]:
        title_name = self._resolve_target_title(job)
        folder_name = self._safe_file_name(title_name)
        target_dir_path = os.path.join(job.download_dir, folder_name)
        os.makedirs(target_dir_path, exist_ok=True)
        return title_name, folder_name, target_dir_path

    def _resolve_target_title(self, job: DownloadJob) -> str:
        title_hint = self._normalize_title_hint(job.title_hint, job.target)
        if title_hint:
            return title_hint
        return self._title_from_slug(job.target.slug)

    def _title_from_slug(self, slug: str) -> str:
        text = re.sub(r'^\d+--', '', str(slug or '').strip())
        text = text.replace('_', ' ').replace('-', ' ')
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            return 'download'
        return text[:1].upper() + text[1:]

    def _request_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        raw = self._request_bytes(url, headers=headers)
        if raw is None:
            raise ValueError(f'request failed: {url}')
        payload = json.loads(raw.decode('utf-8', errors='replace'))
        if not isinstance(payload, dict):
            raise ValueError(f'invalid JSON payload: {url}')
        return payload

    def _request_bytes(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[bytes]:
        normalized_url = self._normalize_request_url(url)
        if not normalized_url:
            return None
        request_headers = {'User-Agent': USER_AGENT}
        if isinstance(headers, dict):
            for key, value in headers.items():
                text_key = str(key or '').strip()
                text_value = str(value or '').strip()
                if text_key and text_value:
                    request_headers[text_key] = text_value

        last_error: Optional[Exception] = None
        for _ in range(3):
            try:
                request = urllib.request.Request(normalized_url, headers=request_headers)
                with urllib.request.urlopen(request, timeout=25) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code == 429:
                    time.sleep(RATELIMIT_DELAY_SEC)
                    continue
                if error.code == 403:
                    raise PermissionError('access_denied')
                if error.code >= 500:
                    time.sleep(REQUEST_RETRY_DELAY_SEC)
                    continue
                return None
            except Exception as error:
                last_error = error
                time.sleep(REQUEST_RETRY_DELAY_SEC)
        if last_error is not None:
            raise last_error
        return None

    def _normalize_request_url(self, url: str) -> str:
        text = str(url or '').strip()
        if not text:
            return ''

        parsed = urllib.parse.urlsplit(text)
        if not parsed.scheme or not parsed.netloc:
            return text

        path = urllib.parse.quote(
            urllib.parse.unquote(parsed.path or ''),
            safe="/%:@!$&'()*+,;=-._~",
        )
        query = urllib.parse.quote(
            urllib.parse.unquote(parsed.query or ''),
            safe="=&;%:+,/?-._~!$'()*[]",
        )
        fragment = urllib.parse.quote(
            urllib.parse.unquote(parsed.fragment or ''),
            safe="=&;%:+,/?-._~!$'()*[]",
        )
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, path, query, fragment)
        )

    def _group_chapters_by_volume(
        self,
        chapters: Sequence[Any],
    ) -> List[Tuple[str, List[Dict[str, Any]]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for raw in chapters:
            if not isinstance(raw, dict):
                continue
            volume = self._string_value(raw.get('volume')) or '1'
            grouped.setdefault(volume, []).append(dict(raw))

        ordered: List[Tuple[str, List[Dict[str, Any]]]] = []
        for volume in sorted(grouped.keys(), key=self._sort_numberish_key):
            rows = sorted(grouped[volume], key=self._chapter_sort_key)
            ordered.append((volume, rows))
        return ordered

    def _filter_ranobe_chapters(
        self,
        chapters: Sequence[Any],
        selection: Optional[DownloadSelection],
    ) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for raw in chapters:
            if not isinstance(raw, dict):
                continue
            chapter = dict(raw)
            if selection is None:
                selected.append(chapter)
                continue
            selection_value = chapter.get('volume') if selection.kind == 'volume' else chapter.get('number')
            if self._selection_matches_value(selection_value, selection):
                selected.append(chapter)
        return selected

    def _selection_matches_value(
        self,
        value: Any,
        selection: DownloadSelection,
    ) -> bool:
        normalized = self._normalize_selection_number(value)
        if not normalized:
            return False
        if normalized in selection.values:
            return True
        range_value = self._selection_range_value(normalized)
        if range_value is None:
            return False
        for start, end in selection.ranges:
            if start <= range_value <= end:
                return True
        return False

    def _normalize_selection_number(self, value: Any) -> str:
        text = self._string_value(value)
        if not text or re.fullmatch(r'\d+(?:\.\d+)?', text) is None:
            return ''
        if '.' not in text:
            return str(int(text))
        whole, fraction = text.split('.', 1)
        whole = str(int(whole))
        fraction = fraction.rstrip('0')
        if not fraction:
            return whole
        return f'{whole}.{fraction}'

    def _selection_range_value(self, value: str) -> Optional[int]:
        if re.fullmatch(r'\d+', str(value or '').strip()) is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _invalid_ranobe_selection_message(self) -> str:
        return self._tr(
            'I could not parse the volume or chapter list. Use formats like "том 1,2,5-7" or "главы 3,4".',
            'Не удалось разобрать список томов или глав. Используй форматы вроде "том 1,2,5-7" или "главы 3,4".',
        )

    def _empty_ranobe_selection_message(self, selection: DownloadSelection) -> str:
        if selection.kind == 'volume':
            return self._tr(
                f'No matching volumes found for: {selection.raw}.',
                f'Не нашла подходящих томов по запросу: {selection.raw}.',
            )
        return self._tr(
            f'No matching chapters found for: {selection.raw}.',
            f'Не нашла подходящих глав по запросу: {selection.raw}.',
        )

    def _chapter_sort_key(self, value: Any) -> Tuple[Tuple[int, Any], Tuple[int, Any]]:
        if not isinstance(value, dict):
            return ((1, ''), (1, ''))
        return (
            self._sort_numberish_key(value.get('volume')),
            self._sort_numberish_key(value.get('number')),
        )

    def _sort_numberish_key(self, value: Any) -> Tuple[int, Any]:
        text = self._string_value(value)
        if not text:
            return (1, '')
        try:
            return (0, float(text))
        except Exception:
            return (1, text)

    def _normalize_ranobe_attachments(
        self,
        raw_attachments: Any,
        origin: str,
    ) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if not isinstance(raw_attachments, list):
            return out
        for raw in raw_attachments:
            if not isinstance(raw, dict):
                continue
            filename = self._string_value(raw.get('filename') or raw.get('name'))
            url = self._string_value(raw.get('url'))
            if not filename or not url:
                continue
            if url.startswith('/uploads/'):
                resolved = origin + url
            elif url.startswith('/'):
                resolved = origin + '/uploads' + url
            else:
                resolved = url
            out.append(
                {
                    'name': self._string_value(raw.get('name')),
                    'filename': filename,
                    'url': resolved,
                }
            )
        return out

    def _parse_ranobe_content(
        self,
        content: Any,
        attachments: Sequence[Dict[str, str]],
    ) -> str:
        if isinstance(content, str):
            return _RANOBE_IMAGE_RE.sub(
                lambda match: f'<image l:href="#{html.escape(match.group(2), quote=True)}"/>',
                content,
            )

        if isinstance(content, dict) and isinstance(content.get('content'), list):
            parts: List[str] = []
            attachment_by_name = {
                self._string_value(item.get('name')): item
                for item in attachments
                if self._string_value(item.get('name'))
            }
            for node in content.get('content') or []:
                if not isinstance(node, dict):
                    continue
                node_type = self._string_value(node.get('type'))
                if node_type == 'paragraph':
                    texts = []
                    for item in node.get('content') or []:
                        if not isinstance(item, dict):
                            continue
                        text = self._string_value(item.get('text'))
                        if text:
                            texts.append(html.escape(text))
                    parts.append(f'<p>{"".join(texts)}</p>')
                    continue
                if node_type == 'image':
                    image_id = ''
                    attrs = node.get('attrs')
                    if isinstance(attrs, dict):
                        images = attrs.get('images')
                        if isinstance(images, list) and images and isinstance(images[0], dict):
                            image_id = self._string_value(images[0].get('image'))
                    attachment = attachment_by_name.get(image_id)
                    if not attachment:
                        continue
                    parts.append(
                        f'<image l:href="#{html.escape(attachment.get("filename") or "", quote=True)}"/>'
                    )
            if parts:
                return ''.join(parts)

        return '<p>Текст отсутствует</p>' if self._is_ru_locale() else '<p>Text is unavailable</p>'

    def _ranobe_chapter_title(
        self,
        chapter: Dict[str, Any],
        chapter_data: Dict[str, Any],
    ) -> str:
        volume = self._string_value(chapter.get('volume'))
        number = self._string_value(chapter.get('number'))
        name = self._string_value(chapter_data.get('name'))
        title = self._tr(
            f'Volume {volume} Chapter {number}',
            f'Том {volume} Глава {number}',
        )
        if name:
            return f'{title} — {name}'
        return title

    def _load_ranobe_chapter_document(
        self,
        job: DownloadJob,
        chapter: Dict[str, Any],
        request_headers: Dict[str, str],
    ) -> Optional[Tuple[str, str, List[Tuple[str, str, str]]]]:
        chapter_number = self._string_value(chapter.get('number'))
        chapter_volume = self._string_value(chapter.get('volume'))
        query = urllib.parse.urlencode(
            {'number': chapter_number, 'volume': chapter_volume}
        )
        payload = self._request_json(
            f'{self._chapters_url(job.target.slug).rsplit("/", 1)[0]}/chapter?{query}',
            headers=request_headers,
        )
        chapter_data = payload.get('data')
        if not isinstance(chapter_data, dict):
            return None

        attachments = self._normalize_ranobe_attachments(
            chapter_data.get('attachments'),
            job.target.origin,
        )
        content_html = self._parse_ranobe_content(
            chapter_data.get('content'),
            attachments,
        )
        chapter_title = self._ranobe_chapter_title(chapter, chapter_data)
        body_xml = (
            f'<section><title><p>{html.escape(chapter_title)}</p></title>{content_html}</section>'
        )

        binaries: List[Tuple[str, str, str]] = []
        written_binary_ids: Set[str] = set()
        for attachment in attachments:
            binary_id = attachment.get('filename') or attachment.get('name') or ''
            binary_id = str(binary_id).strip()
            if not binary_id or binary_id in written_binary_ids:
                continue
            blob = self._request_bytes(
                str(attachment.get('url') or ''),
                headers=request_headers,
            )
            if blob is None:
                continue
            binaries.append(
                (
                    binary_id,
                    self._content_type_for_filename(binary_id),
                    base64.b64encode(blob).decode('ascii'),
                )
            )
            written_binary_ids.add(binary_id)

        time.sleep(RANOBE_CHAPTER_DELAY_SEC)
        return chapter_title, body_xml, binaries

    def _compose_ranobe_fb2(
        self,
        book_title: str,
        body_parts: Sequence[str],
        binary_parts: Sequence[str],
    ) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
            'xmlns:l="http://www.w3.org/1999/xlink">\n'
            '<description><title-info><book-title>'
            f'{html.escape(book_title)}</book-title></title-info></description>\n'
            f'<body>{"".join(body_parts)}</body>'
            f'{"".join(binary_parts)}\n'
            '</FictionBook>\n'
        )

    def _ranobe_binary_xml(self, binary_id: str, content_type: str, encoded: str) -> str:
        return (
            f'<binary id="{html.escape(binary_id, quote=True)}" '
            f'content-type="{html.escape(content_type, quote=True)}">{encoded}</binary>'
        )

    def _content_type_for_filename(self, filename: str) -> str:
        guessed = mimetypes.guess_type(filename)[0]
        return guessed or 'image/jpeg'

    def _default_download_dir(self) -> str:
        home = os.path.expanduser('~')
        candidates = [
            os.path.join(home, 'Загрузки'),
            os.path.join(home, 'Downloads'),
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return candidates[-1]

    def _normalize_download_dir(self, value: str) -> str:
        text = str(value or '').strip()
        if not text:
            return self._default_download_dir()
        expanded = os.path.expanduser(text)
        if os.path.isabs(expanded):
            return os.path.normpath(expanded)
        return os.path.normpath(os.path.abspath(expanded))

    def _download_dir_label(self, path: str) -> str:
        normalized = os.path.normpath(path)
        default_dir = os.path.normpath(self._default_download_dir())
        if normalized == default_dir:
            return self._tr('Downloads', 'Загрузки')
        base = os.path.basename(normalized.rstrip(os.sep))
        return base or normalized

    def _safe_file_name(self, value: str) -> str:
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(value or '').strip())
        text = re.sub(r'\s+', ' ', text).strip().strip('.')
        return text or 'download.bin'

    def _safe_suffix(self, value: Any) -> str:
        text = self._string_value(value)
        if not text:
            return '0'
        return re.sub(r'[^A-Za-z0-9._-]+', '_', text)

    def _volume_label(self, volume: Any) -> str:
        value = self._string_value(volume) or '1'
        return self._tr(f'Volume {value}', f'Том {value}')

    def _chapter_label(self, number: Any, volume: Any = '') -> str:
        value = self._string_value(number) or '1'
        volume_value = self._string_value(volume)
        if volume_value and volume_value != '0':
            return self._tr(
                f'Volume {volume_value} Chapter {value}',
                f'Том {volume_value} Глава {value}',
            )
        return self._tr(f'Chapter {value}', f'Глава {value}')

    def _volume_file_name(self, volume_label: str) -> str:
        return self._safe_file_name(f'{volume_label}.fb2')

    def _ranobe_chapter_file_name(self, chapter_label: str) -> str:
        return self._safe_file_name(f'{chapter_label}.fb2')

    def _chapter_file_name(self, chapter_label: str) -> str:
        return self._safe_file_name(f'{chapter_label}.cbz')

    def _finish_message(self, saved_items: int, skipped_items: int, target_dir_name: str) -> str:
        folder_hint_en = f' Folder: {target_dir_name}.' if target_dir_name else ''
        folder_hint_ru = f' Папка: {target_dir_name}.' if target_dir_name else ''
        if saved_items > 0 and skipped_items > 0:
            return self._tr(
                f'Download finished. New files: {saved_items}. Already present: {skipped_items}.{folder_hint_en}',
                f'Скачивание завершено. Новых файлов: {saved_items}. Уже были скачаны: {skipped_items}.{folder_hint_ru}',
            )
        if saved_items > 0:
            return self._tr(
                f'Download finished. Saved files: {saved_items}.{folder_hint_en}',
                f'Скачивание завершено. Сохранено файлов: {saved_items}.{folder_hint_ru}',
            )
        if skipped_items > 0:
            return self._tr(
                f'Everything is already downloaded. Skipped files: {skipped_items}.{folder_hint_en}',
                f'Все файлы уже скачаны. Пропущено файлов: {skipped_items}.{folder_hint_ru}',
            )
        return self._tr(
            f'Download finished, but no files were saved.{folder_hint_en}',
            f'Скачивание завершено, но ни одного файла не сохранено.{folder_hint_ru}',
        )

    def _format_download_error(self, error: Exception, job: DownloadJob) -> str:
        error_code = getattr(error, 'code', None)
        if isinstance(error, PermissionError) or error_code == 403:
            if job.auth_token:
                return self._tr(
                    'The site denied access even with browser authorization.',
                    'Сайт отказал в доступе даже с браузерной авторизацией.',
                )
            return self._tr(
                'Access denied. Keep this site open in the browser and stay signed in.',
                'Доступ запрещен. Держи этот сайт открытым в браузере и оставайся авторизованным.',
            )
        message = str(error).strip()
        if message:
            return message
        return self._tr('download_failed', 'ошибка скачивания')

    def _ui_texts(self) -> Dict[str, str]:
        if self._is_ru_locale():
            return {
                'panel_name': 'Скачивание манги и ранобэ',
                'description': (
                    'Плагин скачивает поддерживаемые тайтлы по ссылке.\n'
                    'Манга сохраняется по главам в CBZ, ранобэ — по томам в FB2.'
                ),
                'download_dir_label': 'Каталог для скачивания',
            }
        return {
            'panel_name': 'Manga & Ranobe Downloader',
            'description': (
                'Downloads supported title links.\n'
                'Manga is saved by chapter as CBZ, ranobe is saved by volume as FB2.'
            ),
            'download_dir_label': 'Download folder',
        }

    def _string_value(self, value: Any) -> str:
        return str(value or '').strip()

    def _int_or_none(self, value: Any) -> Optional[int]:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None

    def _reply(self, sender: str, payload: Dict[str, Any]) -> None:
        if sender:
            self.reply(sender, payload)

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._is_ru_locale() else en


if __name__ == '__main__':
    run_plugin(MangalibDownloaderPlugin)
