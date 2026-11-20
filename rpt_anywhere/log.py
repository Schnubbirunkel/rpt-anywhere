"""Structured event log: ~/.cache/rpt-anywhere/app.log"""
import logging

from . import __version__
from .i18n import _
from .config import CACHE_DIR

log = logging.getLogger("rpt")


def setup() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / "app.log"
    try:
        if f.exists() and f.stat().st_size > 2_000_000:
            f.replace(f.with_suffix(".log.1"))
    except OSError:
        pass
    h = logging.FileHandler(f, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.info(_('──── RPT Anywhere %s gestartet ────'), __version__)
