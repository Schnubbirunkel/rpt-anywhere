"""Colours + Qt stylesheet."""
from pathlib import Path

_CHECK = (Path(__file__).resolve().parents[1] / "assets" / "check.svg").as_posix()

BG = "#0d1016"
SURFACE = "#141922"
SURFACE_2 = "#1b2230"
SURFACE_3 = "#232c3d"
BORDER = "#263045"
TEXT = "#e8ecf3"
MUTED = "#8a94a8"
ACCENT = "#ff7a59"       # warm coral – "couch lamp"
ACCENT_2 = "#ffb86b"
OK = "#4ade80"
WARN = "#fbbf24"
ERR = "#f87171"
INFO = "#7aa2ff"

FONT_STACK = '"Inter", "Noto Sans", "Cantarell", "Segoe UI", sans-serif'

QSS = f"""
* {{
    font-family: {FONT_STACK};
    color: {TEXT};
    outline: none;
}}
QMainWindow, #Root {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}

/* ---------------------------------------------------------------- sidebar */
#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
#Brand {{ font-size: 17px; font-weight: 800; letter-spacing: .3px; }}
#BrandSub {{ color: {MUTED}; font-size: 11px; }}
QPushButton#Nav {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 10px;
    background: transparent; color: {MUTED}; font-size: 14px; font-weight: 600;
}}
QPushButton#Nav:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: {SURFACE_3}; color: {TEXT}; }}

/* ---------------------------------------------------------------- cards */
#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
#CardFlat {{ background: {SURFACE_2}; border-radius: 12px; }}
#SectionTitle {{ font-size: 12px; font-weight: 700; color: {MUTED}; letter-spacing: 1px; }}
#H1 {{ font-size: 26px; font-weight: 800; }}
#H2 {{ font-size: 17px; font-weight: 700; }}
#Muted {{ color: {MUTED}; }}
#Small {{ color: {MUTED}; font-size: 12px; }}
#Mono {{ font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace; font-size: 12px;
         background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px; }}

/* ---------------------------------------------------------------- buttons */
QPushButton {{
    background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: #2c3850; }}
QPushButton:pressed {{ background: {SURFACE_2}; }}
QPushButton:disabled {{ color: #566074; background: {SURFACE_2}; border-color: {SURFACE_2}; }}
QPushButton#Primary {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
    color: #1a0f08; border: none; font-weight: 800;
}}
QPushButton#Primary:hover {{ background: {ACCENT_2}; }}
QPushButton#Primary:disabled {{ background: {SURFACE_3}; color: #566074; }}
QPushButton#Danger {{ background: #3a1d22; border-color: #5b2a31; color: #ffc9c9; }}
QPushButton#Danger:hover {{ background: #4a232a; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton#Chip {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 15px;
    padding: 5px 13px; color: {MUTED}; font-weight: 600;
}}
QPushButton#Chip:hover {{ color: {TEXT}; }}
QPushButton#Chip:checked {{ background: {TEXT}; color: {BG}; border-color: {TEXT}; }}
QPushButton#Invite {{
    background: transparent; border: 1px solid {ACCENT}; color: {ACCENT};
    padding: 5px 12px; border-radius: 8px; font-size: 12px; font-weight: 700;
}}
QPushButton#Invite:hover {{ background: {ACCENT}; color: #1a0f08; }}
QPushButton#Invite:disabled {{ border-color: {BORDER}; color: #566074; }}

/* ---------------------------------------------------------------- inputs */
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 10px;
    selection-background-color: {ACCENT}; selection-color: #1a0f08;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#Search {{ padding-left: 34px; border-radius: 11px; background: {SURFACE}; font-size: 14px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    selection-background-color: {SURFACE_3}; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER};
    background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{_CHECK}"); }}

/* ---------------------------------------------------------------- lists / scroll */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #33405a; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    height: 0; background: none; }}
QScrollBar:horizontal {{ height: 0; }}
QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ border-radius: 10px; padding: 2px; }}
QListWidget::item:selected {{ background: {SURFACE_3}; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QDialog {{ background: {SURFACE}; }}
QTabWidget::pane {{ border: none; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}

#Toast {{ background: {SURFACE_3}; border: 1px solid {BORDER}; border-radius: 12px; padding: 10px 16px;
          font-weight: 600; }}
#Badge {{ border-radius: 6px; padding: 2px 7px; font-size: 10px; font-weight: 800; letter-spacing: .5px; }}
"""
