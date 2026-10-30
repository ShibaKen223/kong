"""Windows API through ctypes: clipboard, synthetic keys, windows, monitors. Windows only."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LONG_PTR = ctypes.c_ssize_t


def _fn(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype = restype
    f.argtypes = argtypes
    return f


# --- clipboard ---------------------------------------------------------------------------

CF_TEXT = 1
CF_OEMTEXT = 7
CF_UNICODETEXT = 13
CF_LOCALE = 16
GMEM_MOVEABLE = 0x0002

_OpenClipboard = _fn(user32, "OpenClipboard", wintypes.BOOL, wintypes.HWND)
_CloseClipboard = _fn(user32, "CloseClipboard", wintypes.BOOL)
_EmptyClipboard = _fn(user32, "EmptyClipboard", wintypes.BOOL)
_GetClipboardData = _fn(user32, "GetClipboardData", wintypes.HANDLE, wintypes.UINT)
_SetClipboardData = _fn(user32, "SetClipboardData", wintypes.HANDLE, wintypes.UINT, wintypes.HANDLE)
_EnumClipboardFormats = _fn(user32, "EnumClipboardFormats", wintypes.UINT, wintypes.UINT)
_IsClipboardFormatAvailable = _fn(user32, "IsClipboardFormatAvailable", wintypes.BOOL, wintypes.UINT)
GetClipboardSequenceNumber = _fn(user32, "GetClipboardSequenceNumber", wintypes.DWORD)
_GlobalAlloc = _fn(kernel32, "GlobalAlloc", wintypes.HGLOBAL, wintypes.UINT, ctypes.c_size_t)
_GlobalLock = _fn(kernel32, "GlobalLock", wintypes.LPVOID, wintypes.HGLOBAL)
_GlobalUnlock = _fn(kernel32, "GlobalUnlock", wintypes.BOOL, wintypes.HGLOBAL)
_GlobalFree = _fn(kernel32, "GlobalFree", wintypes.HGLOBAL, wintypes.HGLOBAL)
_GlobalSize = _fn(kernel32, "GlobalSize", ctypes.c_size_t, wintypes.HGLOBAL)
_CreateWindowExW = _fn(
    user32,
    "CreateWindowExW",
    wintypes.HWND,
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
_DestroyWindow = _fn(user32, "DestroyWindow", wintypes.BOOL, wintypes.HWND)


class Clipboard:
    """`with Clipboard() as cb:` opens the clipboard with a window of our own as owner.

    SetClipboardData fails when the clipboard is opened without an owner window, and another
    app may be holding the clipboard for a moment, hence the retries.
    """

    def __enter__(self) -> "Clipboard":
        self._hwnd = _CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        for _ in range(25):
            if _OpenClipboard(self._hwnd):
                return self
            time.sleep(0.02)
        _DestroyWindow(self._hwnd)
        raise OSError("剪貼簿被其他程式占用")

    def __exit__(self, *exc) -> None:
        _CloseClipboard()
        _DestroyWindow(self._hwnd)

    def formats(self) -> list[int]:
        out, f = [], 0
        while True:
            f = _EnumClipboardFormats(f)
            if not f:
                return out
            out.append(f)

    def get_text(self) -> str | None:
        if not _IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = _GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = _GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            _GlobalUnlock(h)

    def set_text(self, text: str) -> None:
        data = text.replace("\r\n", "\n").replace("\n", "\r\n")
        buf = ctypes.create_unicode_buffer(data)
        size = ctypes.sizeof(buf)
        h = _GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            raise OSError("GlobalAlloc failed")
        p = _GlobalLock(h)
        if not p:
            _GlobalFree(h)
            raise OSError("GlobalLock failed")
        ctypes.memmove(p, buf, size)
        _GlobalUnlock(h)
        _EmptyClipboard()
        if not _SetClipboardData(CF_UNICODETEXT, h):
            _GlobalFree(h)
            raise OSError("SetClipboardData failed")

    def get_all(self, max_bytes: int) -> list[tuple[int, bytes]] | None:
        """Every format held in global memory, as bytes; None if it would exceed `max_bytes`.

        Handle formats (bitmaps, metafiles, palettes, private GDI objects) are skipped: Windows
        rebuilds CF_BITMAP from CF_DIB on its own, so an image survives as CF_DIB/CF_DIBV5.
        """
        out, total = [], 0
        for f in self.formats():
            if f in _HANDLE_FORMATS or 0x80 <= f <= 0x8F or 0x200 <= f <= 0x3FF:
                continue
            h = _GetClipboardData(f)
            if not h:
                continue
            size = _GlobalSize(h)
            total += size
            if total > max_bytes:
                return None
            p = _GlobalLock(h) if size else None
            if not p:
                continue
            try:
                out.append((f, ctypes.string_at(p, size)))
            finally:
                _GlobalUnlock(h)
        return out

    def set_all(self, items: list[tuple[int, bytes]]) -> None:
        _EmptyClipboard()
        for f, data in items:
            h = _GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
            p = _GlobalLock(h) if h else None
            if not p:
                if h:
                    _GlobalFree(h)
                continue
            ctypes.memmove(p, data, len(data))
            _GlobalUnlock(h)
            if not _SetClipboardData(f, h):
                _GlobalFree(h)


_TEXT_FORMATS = {CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE}
_HANDLE_FORMATS = {2, 3, 9, 14}  # CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE
CLIPBOARD_SAVE_MAX = 64 * 1024 * 1024


def clipboard_save() -> list[tuple[int, bytes]] | None:
    """Everything on the clipboard (images and files too), or None if too big to hold."""
    with Clipboard() as cb:
        return cb.get_all(CLIPBOARD_SAVE_MAX)


def clipboard_restore(items: list[tuple[int, bytes]]) -> None:
    with Clipboard() as cb:
        cb.set_all(items)


def clipboard_snapshot() -> tuple[str, str | None]:
    """("empty"|"text"|"other", text). "other" = an image or files we could not put back."""
    with Clipboard() as cb:
        fmts = cb.formats()
        if not fmts:
            return "empty", None
        text = cb.get_text()
        # Registered formats (>= 0xC000: HTML, RTF, …) travel with copied text; plain text is
        # what matters when putting it back. Anything else (bitmaps, files) cannot be restored.
        if text is not None and all(f in _TEXT_FORMATS or f >= 0xC000 for f in fmts):
            return "text", text
        return "other", None


def set_clipboard_text(text: str) -> int:
    with Clipboard() as cb:
        cb.set_text(text)
    return GetClipboardSequenceNumber()


# --- synthetic keyboard input ------------------------------------------------------------

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_SendInput = _fn(user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_MapVirtualKeyW = _fn(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
_GetAsyncKeyState = _fn(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)


def _key(vk: int = 0, up: bool = False, scan: int | None = None, flags: int = 0) -> INPUT:
    ki = KEYBDINPUT(
        wVk=vk,
        wScan=_MapVirtualKeyW(vk, 0) if scan is None else scan,
        dwFlags=flags | (KEYEVENTF_KEYUP if up else 0),
        time=0,
        dwExtraInfo=0,
    )
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))


def _send(inputs: list[INPUT]) -> int:
    arr = (INPUT * len(inputs))(*inputs)
    return _SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def release_modifiers() -> None:
    """Let go of modifiers still physically down so Ctrl+V does not become Ctrl+Shift+V."""
    ups = [_key(vk, up=True) for vk in (VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN) if _GetAsyncKeyState(vk) & 0x8000]
    if ups:
        _send(ups)


def send_ctrl_v() -> bool:
    n = _send([_key(VK_CONTROL), _key(VK_V), _key(VK_V, up=True), _key(VK_CONTROL, up=True)])
    return n == 4


def type_text(text: str) -> None:
    """Type `text` as Unicode key events (VK_PACKET); line breaks become Shift+Enter."""
    batch: list[INPUT] = []

    def flush():
        if batch:
            _send(batch)
            batch.clear()
            time.sleep(0.005)

    for line_no, line in enumerate(text.split("\n")):
        if line_no:
            flush()
            _send([_key(VK_SHIFT), _key(VK_RETURN), _key(VK_RETURN, up=True), _key(VK_SHIFT, up=True)])
        units = line.encode("utf-16-le")
        for i in range(0, len(units), 2):
            cu = int.from_bytes(units[i : i + 2], "little")
            batch.append(_key(0, scan=cu, flags=KEYEVENTF_UNICODE))
            batch.append(_key(0, up=True, scan=cu, flags=KEYEVENTF_UNICODE))
            if len(batch) >= 64:
                flush()
    flush()


# --- windows and processes ---------------------------------------------------------------

GetForegroundWindow = _fn(user32, "GetForegroundWindow", wintypes.HWND)
_SetForegroundWindow = _fn(user32, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
_IsWindow = _fn(user32, "IsWindow", wintypes.BOOL, wintypes.HWND)
_GetWindowThreadProcessId = _fn(
    user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
)
_GetAncestor = _fn(user32, "GetAncestor", wintypes.HWND, wintypes.HWND, wintypes.UINT)
_OpenProcess = _fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_CloseHandle = _fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_GetCurrentProcess = _fn(kernel32, "GetCurrentProcess", wintypes.HANDLE)
_QueryFullProcessImageNameW = _fn(
    kernel32,
    "QueryFullProcessImageNameW",
    wintypes.BOOL,
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
)
_OpenProcessToken = _fn(
    advapi32, "OpenProcessToken", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
)
_GetTokenInformation = _fn(
    advapi32,
    "GetTokenInformation",
    wintypes.BOOL,
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20
GA_ROOT = 2


def window_pid(hwnd) -> int:
    pid = wintypes.DWORD(0)
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_name(pid: int) -> str:
    h = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        if _QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _CloseHandle(h)


def _token_elevated(process_handle) -> bool | None:
    tok = wintypes.HANDLE()
    if not _OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(tok)):
        return None
    try:
        val = wintypes.DWORD(0)
        size = wintypes.DWORD(0)
        if not _GetTokenInformation(tok, TokenElevation, ctypes.byref(val), ctypes.sizeof(val), ctypes.byref(size)):
            return None
        return bool(val.value)
    finally:
        _CloseHandle(tok)


def self_elevated() -> bool:
    return bool(_token_elevated(_GetCurrentProcess()))


def target_blocks_input(hwnd) -> bool:
    """True when the window belongs to an elevated (系統管理員) app we are not allowed to type into.

    Windows silently drops synthetic keys sent to a more privileged window, and SendInput does
    not report it. A non-elevated process cannot read an elevated process's token, which is
    exactly the case to detect.
    """
    if not hwnd or self_elevated():
        return False
    h = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, window_pid(hwnd))
    if not h:
        return False
    try:
        elevated = _token_elevated(h)
        return elevated is None or elevated
    finally:
        _CloseHandle(h)


def focus(hwnd) -> bool:
    if not hwnd or not _IsWindow(hwnd):
        return False
    if GetForegroundWindow() == hwnd:
        return True
    _SetForegroundWindow(hwnd)
    time.sleep(0.05)
    return GetForegroundWindow() == hwnd


def toplevel_of(hwnd) -> int:
    return _GetAncestor(hwnd, GA_ROOT) or hwnd


# --- monitors, DPI, window styles --------------------------------------------------------


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_MonitorFromWindow = _fn(user32, "MonitorFromWindow", wintypes.HMONITOR, wintypes.HWND, wintypes.DWORD)
_GetMonitorInfoW = _fn(user32, "GetMonitorInfoW", wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
MONITOR_DEFAULTTONEAREST = 2


def work_area(hwnd) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the usable area on the monitor showing `hwnd`."""
    mon = _MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not _GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


def set_dpi_aware() -> None:
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)  # system-aware: Tk is not per-monitor aware
    except (OSError, AttributeError):
        try:
            user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            pass


GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

_GetWindowLongPtrW = _fn(user32, "GetWindowLongPtrW", LONG_PTR, wintypes.HWND, ctypes.c_int)
_SetWindowLongPtrW = _fn(user32, "SetWindowLongPtrW", LONG_PTR, wintypes.HWND, ctypes.c_int, LONG_PTR)
_SetWindowPos = _fn(
    user32,
    "SetWindowPos",
    wintypes.BOOL,
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
)


def make_overlay(hwnd, round_corners: bool = True) -> None:
    """Topmost, never takes focus, no taskbar button: the panel must not steal the cursor."""
    style = _GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST) & ~WS_EX_APPWINDOW
    _SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    _SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    try:
        # Windows 11 corners: 2 = round, 1 = never (a per-pixel-alpha window draws its own).
        pref = ctypes.c_int(2 if round_corners else 1)
        ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except OSError:
        pass


def set_caption_colors(hwnd, caption: str, text: str, border: str) -> None:
    """Paint the title bar to match the window ("#rrggbb"). Windows 11; a no-op elsewhere."""
    try:
        dwm = ctypes.WinDLL("dwmapi")
    except OSError:
        return
    for attr, color in ((35, caption), (36, text), (34, border)):  # DWMWA_CAPTION/TEXT/BORDER_COLOR
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        ref = wintypes.DWORD(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))


# --- per-pixel-alpha windows -------------------------------------------------------------

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
ULW_ALPHA = 0x2
AC_SRC_ALPHA = 0x1


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_GetDC = _fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
_ReleaseDC = _fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
_UpdateLayeredWindow = _fn(
    user32,
    "UpdateLayeredWindow",
    wintypes.BOOL,
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
)
_CreateCompatibleDC = _fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
_DeleteDC = _fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
_CreateDIBSection = _fn(
    gdi32,
    "CreateDIBSection",
    wintypes.HBITMAP,
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
    wintypes.DWORD,
)
_SelectObject = _fn(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_DeleteObject = _fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)


class LayeredWindow:
    """Shows a premultiplied BGRA bitmap as the whole window: soft shadow, real rounded corners.

    Clicks always pass through (WS_EX_TRANSPARENT): the panel is only something to read. Never
    set Tk's `-alpha` on this window: once SetLayeredWindowAttributes has been called,
    UpdateLayeredWindow stops working.
    """

    def __init__(self, hwnd) -> None:
        self.hwnd = hwnd
        style = _GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        # Clearing and setting WS_EX_LAYERED again forgets an earlier SetLayeredWindowAttributes.
        _SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
        _SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        self._dc = _CreateCompatibleDC(None)
        self._bmp = None
        self._old = None
        self._bits = ctypes.c_void_p()
        self._size = (0, 0)

    def _ensure(self, w: int, h: int) -> None:
        if (w, h) == self._size:
            return
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h  # negative: rows top-down, like the image
        bi.biPlanes, bi.biBitCount = 1, 32
        bmp = _CreateDIBSection(self._dc, ctypes.byref(bi), 0, ctypes.byref(self._bits), None, 0)
        if not bmp:
            raise ctypes.WinError(ctypes.get_last_error())
        old = _SelectObject(self._dc, bmp)
        if self._bmp:
            _DeleteObject(self._bmp)
        else:
            self._old = old
        self._bmp = bmp
        self._size = (w, h)

    def update(self, bgra: bytes, w: int, h: int, x: int, y: int, alpha: int = 255) -> bool:
        self._ensure(w, h)
        ctypes.memmove(self._bits, bgra, w * h * 4)
        blend = BLENDFUNCTION(0, 0, max(0, min(255, alpha)), AC_SRC_ALPHA)
        screen = _GetDC(None)
        try:
            return bool(_UpdateLayeredWindow(
                self.hwnd, screen, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(wintypes.SIZE(w, h)),
                self._dc, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA,
            ))
        finally:
            _ReleaseDC(None, screen)

    def close(self) -> None:
        if self._bmp:
            _SelectObject(self._dc, self._old)
            _DeleteObject(self._bmp)
            self._bmp = None
        if self._dc:
            _DeleteDC(self._dc)
            self._dc = None



# --- misc --------------------------------------------------------------------------------

_CreateMutexW = _fn(kernel32, "CreateMutexW", wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
ERROR_ALREADY_EXISTS = 183
_mutex = None


def single_instance(name: str) -> bool:
    """False if another copy already holds the mutex."""
    global _mutex
    _mutex = _CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


_CreateEventW = _fn(kernel32, "CreateEventW", wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_OpenEventW = _fn(kernel32, "OpenEventW", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_SetEvent = _fn(kernel32, "SetEvent", wintypes.BOOL, wintypes.HANDLE)
_WaitForSingleObject = _fn(kernel32, "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
EVENT_MODIFY_STATE = 0x0002
INFINITE = 0xFFFFFFFF


def create_event(name: str):
    """A named auto-reset event another process can signal (see signal_event)."""
    return _CreateEventW(None, False, False, name)


def wait_event(handle) -> None:
    _WaitForSingleObject(handle, INFINITE)


def signal_event(name: str) -> bool:
    h = _OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not h:
        return False
    try:
        return bool(_SetEvent(h))
    finally:
        _CloseHandle(h)


MB_OK = 0x0
MB_YESNO = 0x4
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
IDYES = 6
_MessageBoxW = _fn(user32, "MessageBoxW", ctypes.c_int, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)


def message_box(text: str, title: str, yes_no: bool = False, warning: bool = False) -> bool:
    flags = (MB_YESNO if yes_no else MB_OK) | (MB_ICONWARNING if warning else MB_ICONINFORMATION)
    return _MessageBoxW(None, text, title, flags | MB_TOPMOST | MB_SETFOREGROUND) == IDYES
