"""Tiny ctypes bridge to the *official* Steamworks flat API (libsteam_api.so).

Only works while this process runs "as" a Steam app, i.e. when it was started by
Steam through a donor game (SteamAppId is then set in the environment). In that
situation we can use the public ISteamRemotePlay interface to invite friends –
no undocumented client internals involved, so Steam updates won't break it.
"""
from __future__ import annotations

import ctypes as C
import os
from dataclasses import dataclass
from pathlib import Path
from .i18n import N_, _

PERSONA_STATES = {
    0: N_("Offline"), 1: N_("Online"), 2: N_("Beschäftigt"), 3: N_("Abwesend"), 4: N_("Schlummern"),
    5: N_("Will handeln"), 6: N_("Will spielen"), 7: N_("Unsichtbar"),
}
FRIEND_FLAG_IMMEDIATE = 0x04


CB_REMOTEPLAY_CONNECTED = 5701
CB_REMOTEPLAY_DISCONNECTED = 5702
CB_REMOTEPLAY_GUEST_INVITE = 5703


class CallbackMsg(C.Structure):
    _pack_ = 4
    _fields_ = [("user", C.c_int32), ("callback", C.c_int32),
                ("param", C.POINTER(C.c_uint8)), ("size", C.c_int32)]


# --- classic callback registration (CCallbackBase emulation) -------------------
# class CCallbackBase { virtual void Run(void*); virtual void Run(void*, bool, SteamAPICall_t);
#                       virtual int GetCallbackSizeBytes(); uint8 m_nCallbackFlags; int m_iCallback; }
# GCC/Itanium ABI: vtable entries in declaration order, vptr at offset 0.
RUN_FN = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
RUN_CALL_FN = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_bool, C.c_uint64)
SIZE_FN = C.CFUNCTYPE(C.c_int, C.c_void_p)


class _VTable(C.Structure):
    _fields_ = [("run", RUN_FN), ("run_call", RUN_CALL_FN), ("size", SIZE_FN)]


class _CallbackObj(C.Structure):
    _fields_ = [("vtable", C.POINTER(_VTable)), ("flags", C.c_uint8), ("icallback", C.c_int)]


CALLBACK_SIZES = {CB_REMOTEPLAY_CONNECTED: 4, CB_REMOTEPLAY_DISCONNECTED: 4,
                  CB_REMOTEPLAY_GUEST_INVITE: 1024}


class FriendGameInfo(C.Structure):
    _pack_ = 4
    _fields_ = [("game_id", C.c_uint64), ("ip", C.c_uint32), ("game_port", C.c_uint16),
                ("query_port", C.c_uint16), ("lobby", C.c_uint64)]


@dataclass
class Friend:
    steamid: int
    name: str
    state: int
    playing_appid: int = 0
    avatar_handle: int = 0

    @property
    def online(self) -> bool:
        return self.state not in (0, 7)

    @property
    def state_text(self) -> str:
        return _('Im Spiel') if self.playing_appid else _(PERSONA_STATES.get(self.state, "?"))


@dataclass
class Session:
    session_id: int
    steamid: int
    client_name: str


class SteamworksError(RuntimeError):
    pass


class Steamworks:
    def __init__(self, lib_path: str | os.PathLike):
        self.lib_path = Path(lib_path)
        self.lib = None
        self.friends = self.remote = self.utils = self.user = None
        self.app_id = 0
        self.can_start_rpt = False
        self._found_version = 0
        self.remote_version = 0
        self.callback_mode = "none"
        self.seen_callbacks: set[int] = set()
        self._cb_keep: list = []
        self._unregister = None

    # ------------------------------------------------------------------ init
    def _fn(self, name, restype, *argtypes, required=True):
        try:
            f = getattr(self.lib, name)
        except AttributeError:
            if required:
                raise SteamworksError(_('{name} hat kein Symbol {name1}').format(name=self.lib_path.name, name1=name))
            return None
        f.restype = restype
        f.argtypes = list(argtypes)
        return f

    def _accessor(self, base: str, versions):
        for v in versions:
            f = self._fn(f"SteamAPI_{base}_v{v:03d}", C.c_void_p, required=False)
            if f:
                ptr = f()
                if ptr:
                    self._found_version = v
                    return ptr
        raise SteamworksError(_('Interface {base} nicht verfügbar').format(base=base))

    def init(self) -> None:
        if not os.environ.get("SteamAppId") and not os.environ.get("SteamGameId"):
            raise SteamworksError(_('Nicht über Steam gestartet (SteamAppId fehlt)'))
        try:
            self.lib = C.CDLL(str(self.lib_path))
        except OSError as e:
            raise SteamworksError(_('Konnte {lib_path} nicht laden: {e}').format(lib_path=self.lib_path, e=e)) from e

        # Callbacks (guest invite URL, players joining): classic SteamAPI_RegisterCallback
        # works with every SDK version. Manual dispatch only if that is missing – mixing
        # both breaks SteamAPI_RunCallbacks.
        self._register = self._fn("SteamAPI_RegisterCallback", None, C.c_void_p, C.c_int,
                                  required=False)
        self._unregister = self._fn("SteamAPI_UnregisterCallback", None, C.c_void_p,
                                    required=False)
        md_init = None
        if not self._register:
            md_init = self._fn("SteamAPI_ManualDispatch_Init", None, required=False)
            if md_init:
                md_init()
        init_flat = self._fn("SteamAPI_InitFlat", C.c_int, C.c_char_p, required=False)
        if init_flat:
            err = C.create_string_buffer(1024)
            res = init_flat(err)
            if res != 0:
                raise SteamworksError(_('SteamAPI_InitFlat fehlgeschlagen ({res}): {v1}').format(res=res, v1=err.value.decode(errors='replace')))
        else:
            init = self._fn("SteamAPI_Init", C.c_bool)
            if not init():
                raise SteamworksError(_('SteamAPI_Init fehlgeschlagen – läuft Steam?'))

        self._run_callbacks = self._fn("SteamAPI_RunCallbacks", None)
        self._shutdown = self._fn("SteamAPI_Shutdown", None)
        self._is_running = self._fn("SteamAPI_IsSteamRunning", C.c_bool, required=False)
        self._md = None
        if md_init:
            pipe_fn = self._fn("SteamAPI_GetHSteamPipe", C.c_int32, required=False)
            frame = self._fn("SteamAPI_ManualDispatch_RunFrame", None, C.c_int32, required=False)
            nxt = self._fn("SteamAPI_ManualDispatch_GetNextCallback", C.c_bool, C.c_int32,
                           C.POINTER(CallbackMsg), required=False)
            free = self._fn("SteamAPI_ManualDispatch_FreeLastCallback", None, C.c_int32,
                            required=False)
            if pipe_fn and frame and nxt and free:
                self._md = (pipe_fn(), frame, nxt, free)

        self.friends = self._accessor("SteamFriends", (18, 17, 16, 15))
        self.remote = self._accessor("SteamRemotePlay", (4, 3, 2, 1))
        self.remote_version = self._found_version
        self._events: list[tuple[str, object]] = []
        self.seen_callbacks: set[int] = set()
        self._cb_keep: list = []
        self.callback_mode = "none"
        if self._register:
            for cb in CALLBACK_SIZES:
                self._register_callback(cb)
            self.callback_mode = "register"
        elif md_init:
            self.callback_mode = "manual"
        self.utils = self._accessor("SteamUtils", (10, 9))
        self.user = self._accessor("SteamUser", (23, 22, 21, 20))

        P, U64, I, U32, S, B = C.c_void_p, C.c_uint64, C.c_int, C.c_uint32, C.c_char_p, C.c_bool
        f = self._fn
        self._get_steamid = f("SteamAPI_ISteamUser_GetSteamID", U64, P)
        self._persona = f("SteamAPI_ISteamFriends_GetPersonaName", S, P)
        self._friend_count = f("SteamAPI_ISteamFriends_GetFriendCount", I, P, I)
        self._friend_by_idx = f("SteamAPI_ISteamFriends_GetFriendByIndex", U64, P, I, I)
        self._friend_name = f("SteamAPI_ISteamFriends_GetFriendPersonaName", S, P, U64)
        self._friend_state = f("SteamAPI_ISteamFriends_GetFriendPersonaState", I, P, U64)
        self._friend_game = f("SteamAPI_ISteamFriends_GetFriendGamePlayed", B, P, U64,
                              C.POINTER(FriendGameInfo))
        self._avatar = f("SteamAPI_ISteamFriends_GetMediumFriendAvatar", I, P, U64)
        self._req_info = f("SteamAPI_ISteamFriends_RequestUserInformation", B, P, U64, B,
                           required=False)
        self._overlay = f("SteamAPI_ISteamFriends_ActivateGameOverlay", None, P, S)
        self._img_size = f("SteamAPI_ISteamUtils_GetImageSize", B, P, I,
                           C.POINTER(U32), C.POINTER(U32))
        self._img_rgba = f("SteamAPI_ISteamUtils_GetImageRGBA", B, P, I, C.c_void_p, I)
        self._get_appid = f("SteamAPI_ISteamUtils_GetAppID", U32, P)
        self._sess_count = f("SteamAPI_ISteamRemotePlay_GetSessionCount", U32, P)
        self._sess_id = f("SteamAPI_ISteamRemotePlay_GetSessionID", U32, P, I)
        self._sess_sid = f("SteamAPI_ISteamRemotePlay_GetSessionSteamID", U64, P, U32)
        self._sess_name = f("SteamAPI_ISteamRemotePlay_GetSessionClientName", S, P, U32)
        self._invite = f("SteamAPI_ISteamRemotePlay_BSendRemotePlayTogetherInvite", B, P, U64)
        self._start_rpt = f("SteamAPI_ISteamRemotePlay_BStartRemotePlayTogether", B, P, B,
                            required=False)
        self._show_rpt_ui = f("SteamAPI_ISteamRemotePlay_ShowRemotePlayTogetherUI", B, P,
                              required=False)
        self.can_start_rpt = self._start_rpt is not None or self._show_rpt_ui is not None
        self.app_id = int(self._get_appid(self.utils))

    def shutdown(self) -> None:
        if self.lib is not None and self._unregister:
            for _vt, obj, *_rest in self._cb_keep:
                try:
                    self._unregister(C.byref(obj))
                except Exception:
                    pass
        if self.lib is not None:
            try:
                self._shutdown()
            except Exception:
                pass
            self.lib = None

    # ------------------------------------------------------------------ callbacks
    def _register_callback(self, cb_id: int) -> None:
        def run(this, param):
            self.seen_callbacks.add(cb_id)
            try:
                self._handle_callback(cb_id, param, CALLBACK_SIZES[cb_id])
            except Exception:
                pass

        def run_call(this, param, io_failure, call):
            run(this, param)

        def size(this):
            return CALLBACK_SIZES[cb_id]

        vt = _VTable(RUN_FN(run), RUN_CALL_FN(run_call), SIZE_FN(size))
        obj = _CallbackObj(C.pointer(vt), 0, cb_id)
        self._cb_keep.append((vt, obj, run, run_call, size))
        self._register(C.byref(obj), cb_id)

    def _handle_callback(self, cb_id: int, param, size: int) -> None:
        if not param:
            return
        if cb_id == CB_REMOTEPLAY_GUEST_INVITE:
            raw = C.string_at(param, 1024)
            self._events.append(("guest_link", raw.split(b"\0", 1)[0].decode("utf-8", "replace")))
        elif cb_id in (CB_REMOTEPLAY_CONNECTED, CB_REMOTEPLAY_DISCONNECTED):
            sid = C.cast(param, C.POINTER(C.c_uint32))[0]
            self._events.append(("session_connected" if cb_id == CB_REMOTEPLAY_CONNECTED
                                 else "session_disconnected", int(sid)))

    def steam_running(self) -> bool:
        if not self.ready or not self._is_running:
            return True
        try:
            return bool(self._is_running())
        except Exception:
            return False

    @property
    def ready(self) -> bool:
        return self.lib is not None

    # ------------------------------------------------------------------ api
    def poll_events(self) -> list[tuple[str, object]]:
        """Pump Steam callbacks. Returns [(kind, payload), ...]."""
        if not self.ready:
            return []
        if self.callback_mode != "manual" or not self._md:
            self._run_callbacks()
            events, self._events = self._events, []
            return events
        pipe, frame, nxt, free = self._md
        events: list[tuple[str, object]] = []
        frame(pipe)
        msg = CallbackMsg()
        while nxt(pipe, C.byref(msg)):
            try:
                cb = msg.callback
                self.seen_callbacks.add(cb)
                if cb == CB_REMOTEPLAY_GUEST_INVITE and msg.size > 0:
                    raw = C.string_at(msg.param, min(msg.size, 1024))
                    events.append(("guest_link", raw.split(b"\0", 1)[0].decode("utf-8", "replace")))
                elif cb in (CB_REMOTEPLAY_CONNECTED, CB_REMOTEPLAY_DISCONNECTED) and msg.size >= 4:
                    sid = C.cast(msg.param, C.POINTER(C.c_uint32))[0]
                    events.append(("session_connected" if cb == CB_REMOTEPLAY_CONNECTED
                                   else "session_disconnected", int(sid)))
            finally:
                free(pipe)
        return events

    def my_steamid(self) -> int:
        return int(self._get_steamid(self.user))

    def playing_appid(self, steamid: int | None = None) -> int:
        """AppID Steam shows as "playing" for *steamid* (default: the signed-in user); 0 = none/unknown."""
        gi = FriendGameInfo()
        try:
            sid = steamid or self.my_steamid()
            if self._friend_game(self.friends, sid, C.byref(gi)):
                return gi.game_id & 0xFFFFFF
        except Exception:
            pass
        return 0

    def my_name(self) -> str:
        return (self._persona(self.friends) or b"").decode("utf-8", "replace")

    def list_friends(self) -> list[Friend]:
        out = []
        n = self._friend_count(self.friends, FRIEND_FLAG_IMMEDIATE)
        for i in range(max(n, 0)):
            sid = int(self._friend_by_idx(self.friends, i, FRIEND_FLAG_IMMEDIATE))
            name = (self._friend_name(self.friends, sid) or b"").decode("utf-8", "replace")
            state = int(self._friend_state(self.friends, sid))
            gi = FriendGameInfo()
            playing = 0
            if self._friend_game(self.friends, sid, C.byref(gi)):
                playing = gi.game_id & 0xFFFFFF
            av = int(self._avatar(self.friends, sid))
            if av <= 0 and self._req_info:
                self._req_info(self.friends, sid, False)
            out.append(Friend(sid, name, state, playing, max(av, 0)))
        out.sort(key=lambda f: (not f.online, f.playing_appid == 0, f.name.lower()))
        return out

    def avatar_rgba(self, handle: int) -> tuple[int, int, bytes] | None:
        if handle <= 0:
            return None
        w, h = C.c_uint32(), C.c_uint32()
        if not self._img_size(self.utils, handle, C.byref(w), C.byref(h)) or not w.value:
            return None
        size = w.value * h.value * 4
        buf = C.create_string_buffer(size)
        if not self._img_rgba(self.utils, handle, buf, size):
            return None
        return w.value, h.value, buf.raw

    def invite(self, steamid: int) -> bool:
        """Send a Remote Play Together invite. steamid=0 → Steam creates a guest link."""
        return bool(self._invite(self.remote, C.c_uint64(steamid)))

    def start_rpt_ui(self) -> bool:
        """Open Steam's own Remote Play Together window (can create guest links too)."""
        if self._show_rpt_ui and self._show_rpt_ui(self.remote):
            return True
        if self._start_rpt:
            return bool(self._start_rpt(self.remote, True))
        self._overlay(self.friends, b"Friends")
        return True

    def open_overlay(self, dialog: str = "Friends") -> None:
        self._overlay(self.friends, dialog.encode())

    def sessions(self) -> list[Session]:
        out = []
        for i in range(int(self._sess_count(self.remote))):
            sid = int(self._sess_id(self.remote, i))
            if not sid:
                continue
            out.append(Session(sid, int(self._sess_sid(self.remote, sid)),
                               (self._sess_name(self.remote, sid) or b"").decode("utf-8", "replace")))
        return out
