"""Tiny translation layer (standard library only – also used by the donor agent).

Source strings in the code are German; ``locale/<lang>.json`` maps them to other
languages. English is the default UI language; the choice is stored in the config.
Missing translations fall back to the German source text.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
SOURCE_LANG = "de"
DEFAULT_LANG = "en"

LANGUAGES = {          # code -> native name (shown in the language picker)
    "en": "English",
    "de": "Deutsch",
}

_table: dict[str, str] = {}
_lang = SOURCE_LANG


def set_language(lang: str | None) -> str:
    """Activate *lang* (falls back to English). Returns the active language code."""
    global _table, _lang
    lang = (lang or DEFAULT_LANG).lower()
    if lang not in LANGUAGES:
        lang = DEFAULT_LANG
    _lang = lang
    _table = {}
    if lang != SOURCE_LANG:
        try:
            _table = json.loads((LOCALE_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _table = {}
    return lang


def language() -> str:
    return _lang


def _(text: str) -> str:
    """Translate *text* (German source) into the active language."""
    return _table.get(text, text) if _table else text


def N_(text: str) -> str:
    """Mark a string for translation without translating it (module-level constants);
    translate it later with _() where it is shown."""
    return text


def init_from_config() -> str:
    """Pick the language from RPTA_LANG or the saved config (default: English)."""
    lang = os.environ.get("RPTA_LANG")
    if not lang:
        try:
            from .config import CONFIG_FILE
            lang = json.loads(CONFIG_FILE.read_text()).get("language")
        except Exception:
            lang = None
    return set_language(lang)


# The module starts in source language until init_from_config() is called, so
# imports never fail. Entry points call init_from_config() first.
