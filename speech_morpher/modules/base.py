#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import Any, Dict, List


class MorpherBridge:
    _SENTENCE_SPLIT_RE = re.compile(r'([.!?]+)')

    def split_sentences(self, text: str) -> List[str]:
        value = str(text or '').strip()
        if not value:
            return []
        parts = self._SENTENCE_SPLIT_RE.split(value)
        out: List[str] = []
        current = ''
        for part in parts:
            if not part:
                continue
            if self._SENTENCE_SPLIT_RE.fullmatch(part):
                chunk = f'{current}{part}'.strip()
                if chunk:
                    out.append(chunk)
                current = ''
            else:
                if current:
                    current += part
                else:
                    current = part
        tail = current.strip()
        if tail:
            out.append(tail)
        return out

    def inject_token(self, text: str, token: str, position: str = 'end') -> str:
        value = str(text or '').strip()
        piece = str(token or '').strip()
        if not value or not piece:
            return value
        if position == 'start':
            return f'{piece}, {value}'
        if position == 'middle':
            words = value.split()
            if len(words) < 4:
                return f'{value}, {piece}'
            index = max(1, len(words) // 2)
            words.insert(index, f'{piece},')
            return ' '.join(words)
        return f'{value}, {piece}'


class SpeechMorpherModule:
    module_id = 'base'
    priority = 0
    display_names = {
        'en': 'Base module',
        'ru': 'Базовый модуль',
    }

    def __init__(self) -> None:
        self.bridge = MorpherBridge()

    def initialize(self, bridge: MorpherBridge) -> None:
        self.bridge = bridge

    def display_name(self, locale: str) -> str:
        lang = str(locale or '').strip().lower()
        if lang in self.display_names:
            return self.display_names[lang]
        return self.display_names.get('en') or self.module_id

    def is_active(self, context: Dict[str, Any]) -> bool:
        return True

    def apply(self, text: str, payload: Dict[str, Any], context: Dict[str, Any]) -> str:
        return str(text or '')

    def trait(self, context: Dict[str, Any], key: str, default: float = 0.0) -> float:
        traits = context.get('traits')
        if isinstance(traits, dict):
            try:
                return float(traits.get(key, default))
            except Exception:
                return float(default)
        return float(default)

    def emotion(self, context: Dict[str, Any], key: str, default: float = 0.0) -> float:
        emotions = context.get('emotions')
        if isinstance(emotions, dict):
            try:
                return float(emotions.get(key, default))
            except Exception:
                return float(default)
        return float(default)

    def locale(self, context: Dict[str, Any]) -> str:
        return str(context.get('locale') or 'en').strip().lower()


__all__ = ['MorpherBridge', 'SpeechMorpherModule']
