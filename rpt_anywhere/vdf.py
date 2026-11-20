"""Minimal reader/writer for Valve's KeyValues formats.

* Text VDF  (libraryfolders.vdf, appmanifest_*.acf, localconfig.vdf, config.vdf)
* Binary VDF (shortcuts.vdf, the per-app blobs inside appinfo.vdf)
* appinfo.vdf container (versions 27, 28 and 29)
"""
from __future__ import annotations

import mmap
import struct
from typing import Any, Iterable

__all__ = [
    "loads", "load", "dumps", "dump", "iget", "ipath",
    "parse_binary", "dumps_binary", "read_appinfo",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def iget(d: Any, key: str, default: Any = None) -> Any:
    """Case-insensitive dict lookup (Steam is not consistent with key case)."""
    if not isinstance(d, dict):
        return default
    if key in d:
        return d[key]
    lk = key.lower()
    for k, v in d.items():
        if isinstance(k, str) and k.lower() == lk:
            return v
    return default


def ipath(d: Any, *keys: str, default: Any = None) -> Any:
    for k in keys:
        d = iget(d, k)
        if d is None:
            return default
    return d


def _ikey(d: dict, key: str) -> str:
    """Return the existing key in *d* that matches *key* case-insensitively."""
    lk = key.lower()
    for k in d:
        if k.lower() == lk:
            return k
    return key


def isetdefault(d: dict, key: str) -> dict:
    k = _ikey(d, key)
    if not isinstance(d.get(k), dict):
        d[k] = {}
    return d[k]


# --------------------------------------------------------------------------- #
# text format
# --------------------------------------------------------------------------- #
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens(s: str) -> Iterable[tuple[str, str]]:
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n﻿":
            i += 1
        elif c == "/" and s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c, c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and s[i] != '"':
                if s[i] == "\\" and i + 1 < n:
                    out.append(_ESCAPES.get(s[i + 1], s[i + 1]))
                    i += 2
                else:
                    out.append(s[i])
                    i += 1
            i += 1
            yield "s", "".join(out)
        elif c == "[":  # conditional like [$WIN32] – ignored
            j = s.find("]", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            while j < n and s[j] not in ' \t\r\n{}"':
                j += 1
            yield "s", s[i:j]
            i = j


def loads(s: str) -> dict:
    root: dict = {}
    stack = [root]
    key = None
    for kind, val in _tokens(s):
        if kind == "s":
            if key is None:
                key = val
            else:
                stack[-1][key] = val
                key = None
        elif kind == "{":
            new: dict = {}
            if key is None:
                key = ""
            stack[-1][key] = new
            stack.append(new)
            key = None
        elif kind == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
    return root


def load(path) -> dict:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return loads(fh.read())


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def dumps(d: dict, level: int = 0) -> str:
    out = []
    tab = "\t" * level
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f'{tab}"{_esc(k)}"\n{tab}{{\n')
            out.append(dumps(v, level + 1))
            out.append(f"{tab}}}\n")
        else:
            out.append(f'{tab}"{_esc(k)}"\t\t"{_esc(v)}"\n')
    return "".join(out)


def dump(d: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(d))


# --------------------------------------------------------------------------- #
# binary format
# --------------------------------------------------------------------------- #
T_MAP, T_STR, T_INT, T_FLOAT, T_PTR, T_WSTR, T_COLOR, T_U64, T_END, T_I64, T_END2 = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11)


def _cstr(buf, pos: int) -> tuple[str, int]:
    end = buf.find(b"\x00", pos)
    if end < 0:
        raise ValueError("unterminated string in binary VDF")
    return bytes(buf[pos:end]).decode("utf-8", "replace"), end + 1


def parse_binary(buf, pos: int = 0, strings: list[str] | None = None) -> tuple[dict, int]:
    root: dict = {}
    stack = [root]
    n = len(buf)
    while pos < n:
        t = buf[pos]
        pos += 1
        if t in (T_END, T_END2):
            if len(stack) == 1:
                return root, pos
            stack.pop()
            continue
        if strings is not None:
            (idx,) = struct.unpack_from("<I", buf, pos)
            pos += 4
            key = strings[idx] if idx < len(strings) else str(idx)
        else:
            key, pos = _cstr(buf, pos)
        if t == T_MAP:
            new: dict = {}
            stack[-1][key] = new
            stack.append(new)
        elif t == T_STR:
            stack[-1][key], pos = _cstr(buf, pos)
        elif t in (T_INT, T_PTR, T_COLOR):
            (stack[-1][key],) = struct.unpack_from("<i", buf, pos)
            pos += 4
        elif t == T_FLOAT:
            (stack[-1][key],) = struct.unpack_from("<f", buf, pos)
            pos += 4
        elif t == T_U64:
            (stack[-1][key],) = struct.unpack_from("<Q", buf, pos)
            pos += 8
        elif t == T_I64:
            (stack[-1][key],) = struct.unpack_from("<q", buf, pos)
            pos += 8
        elif t == T_WSTR:
            end = pos
            while bytes(buf[end:end + 2]) != b"\x00\x00":
                end += 2
            stack[-1][key] = bytes(buf[pos:end]).decode("utf-16-le", "replace")
            pos = end + 2
        else:
            raise ValueError(f"unknown binary VDF type {t} at {pos - 1}")
    return root, pos


def dumps_binary(d: dict) -> bytes:
    out = bytearray()
    for k, v in d.items():
        kb = str(k).encode() + b"\x00"
        if isinstance(v, dict):
            out += bytes([T_MAP]) + kb + dumps_binary(v)
        elif isinstance(v, bool) or isinstance(v, int):
            out += bytes([T_INT]) + kb + struct.pack("<i", int(v))
        else:
            out += bytes([T_STR]) + kb + str(v).encode() + b"\x00"
    out += bytes([T_END])
    return bytes(out)


# --------------------------------------------------------------------------- #
# appinfo.vdf
# --------------------------------------------------------------------------- #
def _read_appinfo_v42(mm, wanted: set[int] | None) -> dict[int, dict]:
    """appinfo v42 ("*DV\\x07", Steam 2026): sharded files appcache/appinfo/appinfo_XX.vdf.

    Header (44 bytes): magic, universe, u32 ?, u32 shard count, u32 app count,
    u32 index size, u32 string count, u32 string table size, u32 data size, 8-byte hash.
    Then: index (app count × fixed-size records: appid, data offset, data size, …),
    string table (NUL-terminated keys), data (per app: 20-byte hash + binary KeyValues
    whose keys are u32 indices into the string table)."""
    (_magic, _uni, _unk, _shards, count, index_size, nstrings, strtab_size,
     data_size) = struct.unpack_from("<9I", mm, 0)
    index_off = 44
    strtab_off = index_off + index_size
    data_off = strtab_off + strtab_size
    if count == 0:
        return {}
    if index_size % count or data_off + data_size > len(mm):
        raise ValueError(f"appinfo v42: unexpected header (apps={count}, index={index_size}, "
                         f"strings={nstrings}/{strtab_size}, data={data_size}, file={len(mm)})")
    rec_size = index_size // count
    strings: list[str] = []
    p = strtab_off
    for _i in range(nstrings):
        st, p = _cstr(mm, p)
        strings.append(st)
    try:
        root_idx = strings.index("appinfo")
    except ValueError:
        root_idx = -1
    result: dict[int, dict] = {}
    for i in range(count):
        appid, off, size = struct.unpack_from("<3I", mm, index_off + i * rec_size)
        if wanted is not None and appid not in wanted:
            continue
        blob = data_off + off
        if off + size > data_size:
            continue
        for skip in (20, 0, 40, 4, 24):   # 20-byte hash precedes the KeyValues
            q = blob + skip
            if q + 5 > blob + size or mm[q] != T_MAP:
                continue
            (k,) = struct.unpack_from("<I", mm, q + 1)
            if k < len(strings) and (k == root_idx or root_idx < 0):
                try:
                    kv, _unused = parse_binary(mm, q, strings)
                    result[appid] = kv
                except Exception:
                    pass
                break
    return result


def read_appinfo(path, wanted: set[int] | None = None) -> dict[int, dict]:
    """Return {appid: keyvalues} for the requested app ids (all if None)."""
    result: dict[int, dict] = {}
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            magic, _universe = struct.unpack_from("<II", mm, 0)
            version = magic & 0xFF
            if (magic >> 8) == 0x075644 and version >= 0x2A:
                return _read_appinfo_v42(mm, wanted)
            if (magic >> 8) != 0x075644 or version < 0x27:
                raise ValueError(f"unsupported appinfo.vdf magic {magic:#x}")
            # newer, unknown versions: assume the latest known layout (v41 / 0x29)
            pos = 8
            strings = None
            if version >= 0x29:
                (table_off,) = struct.unpack_from("<q", mm, pos)
                if not 0 < table_off <= len(mm) - 4:
                    raise ValueError(f"string table offset out of range ({table_off})")
                pos += 8
                (count,) = struct.unpack_from("<I", mm, table_off)
                p = table_off + 4
                strings = []
                for _i in range(count):
                    s, p = _cstr(mm, p)
                    strings.append(s)
            header = 40 if version == 0x27 else 60  # bytes after the size field
            end_of_entries = table_off if version >= 0x29 else len(mm)
            while pos + 8 <= end_of_entries:
                appid, size = struct.unpack_from("<II", mm, pos)
                if appid == 0:
                    break
                data_start = pos + 8
                nxt = data_start + size
                if wanted is None or appid in wanted:
                    try:
                        kv, _unused = parse_binary(mm, data_start + header, strings)
                        result[appid] = kv
                    except Exception:  # corrupt entry – skip
                        pass
                pos = nxt
        finally:
            mm.close()
    return result


# --------------------------------------------------------------------------- #
#  surgical text edit (leaves every other byte of the file untouched)
# --------------------------------------------------------------------------- #
def _raw_tokens(s: str):
    """Yield (kind, value, start, end) without touching escapes. kind: s, {, }."""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n\ufeff":
            i += 1
        elif c == "/" and s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c, c, i, i + 1
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            yield "s", s[i + 1:j], i, j + 1
            i = j + 1
        elif c == "[":
            j = s.find("]", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            while j < n and s[j] not in ' \t\r\n{}"':
                j += 1
            yield "s", s[i:j], i, j
            i = j


def _escape_value(v: str) -> str:
    return (str(v).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t"))


def set_text_value(text: str, path: list[str], key: str, value: str) -> str:
    """Set ``path/.../key = value`` in VDF *text*, changing only what is necessary.
    Missing blocks along *path* are created at the end of their parent block."""
    toks = list(_raw_tokens(text))
    # find blocks: list of (keypath tuple(lower), open_idx, close_idx) – indices into toks
    stack: list[tuple[str, int]] = []
    blocks: dict[tuple, tuple[int, int]] = {}
    values: dict[tuple, int] = {}       # keypath -> token index of the value string
    pending = None
    for idx, (kind, val, _a, _b) in enumerate(toks):
        if kind == "s":
            if pending is None:
                pending = (val, idx)
            else:
                kp = tuple(k for k, _ in stack) + (pending[0].lower(),)
                values.setdefault(kp, idx)
                pending = None
        elif kind == "{":
            name = pending[0].lower() if pending else ""
            stack.append((name, idx))
            pending = None
        elif kind == "}":
            if stack:
                kp = tuple(k for k, _ in stack)
                blocks.setdefault(kp, (stack[-1][1], idx))
                stack.pop()
            pending = None

    lpath = tuple(p.lower() for p in path)
    vkey = lpath + (key.lower(),)
    if vkey in values:                       # replace existing value only
        _k, _v, a, b = toks[values[vkey]]
        return text[:a] + '"' + _escape_value(value) + '"' + text[b:]

    # deepest existing block along the path
    depth = len(lpath)
    while depth > 0 and lpath[:depth] not in blocks:
        depth -= 1
    if depth == 0:
        raise ValueError("Grundstruktur der VDF-Datei nicht gefunden")
    _open, close = blocks[lpath[:depth]]
    close_pos = toks[close][2]
    # indentation of the closing brace
    line_start = text.rfind("\n", 0, close_pos) + 1
    indent = text[line_start:close_pos]
    if indent.strip():
        indent = "\t" * depth
    inner = indent + "\t"
    snippet = ""
    pad = inner
    for name in path[depth:]:
        snippet += f'{pad}"{name}"\n{pad}{{\n'
        pad += "\t"
    snippet += f'{pad}"{key}"\t\t"{_escape_value(value)}"\n'
    for _i in path[depth:]:
        pad = pad[:-1]
        snippet += f"{pad}}}\n"
    return text[:line_start] + snippet + text[line_start:]
