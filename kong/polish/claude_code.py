"""Tidy-up through the user's own Claude Code login (Pro/Max subscription).

Like Talky, Kong never touches credentials: it runs the official `claude` CLI, which signs in
by itself. Talky starts a fresh `claude -p` for every sentence, and that start-up costs several
seconds each time. Kong keeps one Claude Code process started and waiting in stream-json mode,
gives it exactly one sentence, takes the answer the moment the model stops writing (without
waiting for Claude Code's post-turn bookkeeping), kills it, and starts the next one in the
background. Every sentence still gets a clean, empty context.

The process runs isolated: no tools, no MCP servers, no CLAUDE.md or plugins (--safe-mode), an
empty working folder, nothing saved to session history, ANTHROPIC_API_KEY removed so the
subscription login is used.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .. import paths
from ..config import Config
from . import prompt

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_IS_WIN = sys.platform == "win32"

# A dictation normally starts streaming within ~2 s, but the first one after hours without
# using Claude has gone past 8 s even on a fresh process. Past this, paste the transcript as is.
FIRST_TOKEN_S = 25.0
# A waiting process left idle this long is not trusted (seen: 2 s after 6 min, 20 s after 14).
WARM_MAX_AGE_S = 300.0
# So the waiting one is swapped for a new one this often, and a dictation never finds it stale.
REFRESH_S = 240.0


class PolishError(Exception):
    pass


# --- finding the CLI ---------------------------------------------------------------------


def _candidates(explicit: str) -> list[Path]:
    out: list[Path] = []
    if explicit:
        out.append(Path(explicit))
    if w := shutil.which("claude"):
        out.append(Path(w))
    home = Path.home()
    if _IS_WIN:
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        out += [
            home / ".local" / "bin" / "claude.exe",
            appdata / "npm" / "claude.cmd",
            local / "Programs" / "claude" / "claude.exe",
        ]
    else:
        out += [
            home / ".local" / "bin" / "claude",
            home / ".claude" / "local" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
        ]
    return out


def _argv_for(path: Path) -> list[str]:
    """npm installs a claude.cmd shim; run its node script directly so no cmd.exe is involved."""
    if path.suffix.lower() in (".cmd", ".bat"):
        js = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node = path.parent / "node.exe"
        node_s = str(node) if node.is_file() else shutil.which("node")
        if js.is_file() and node_s:
            return [node_s, str(js)]
    return [str(path)]


def find_claude(explicit: str = "") -> list[str] | None:
    for c in _candidates(explicit):
        if c.is_file():
            return _argv_for(c)
    return None


def _version(argv: list[str]) -> str:
    try:
        out = subprocess.run(
            [*argv, "--version"], capture_output=True, timeout=30, creationflags=_NO_WINDOW
        )
        return out.stdout.decode("utf-8", "replace").strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


# --- one Claude Code process -------------------------------------------------------------

_UNKNOWN_OPTION = re.compile(r"unknown option '?(--[a-z0-9-]+)")
# Claude Code reports some failures as if the model had said them.
_ERROR_TEXT = re.compile(
    r"^(API Error|Invalid API key|Not logged in|Please run /login|Credit balance is too low|"
    r"Claude AI usage limit|Prompt is too long|OAuth token)",
    re.I,
)


def _explain(raw: str) -> str:
    low = raw.lower()
    # The Claude desktop app keeps its own login, so "but I am logged in" is common here.
    if "expired" in low and ("oauth" in low or "authenticate" in low or "token" in low):
        return "Claude Code 的登入過期了：打開終端機執行 claude 重新登入（Claude 桌面版的登入是分開的）"
    if "login" in low or "not logged in" in low or "invalid api key" in low or "oauth" in low:
        return "Claude Code 還沒登入：打開終端機執行 claude，登入你的帳號後再試"
    if "git-bash" in low or "git bash" in low:
        return "Claude Code 在 Windows 需要 Git for Windows：裝好 Git 後再試"
    if "rate limit" in low or "usage limit" in low or "limit reached" in low:
        return "Claude 訂閱的用量到上限了，稍後再試"
    if "overloaded" in low:
        return "Claude 目前忙碌，稍後再試"
    if m := _UNKNOWN_OPTION.search(raw):
        return f"Claude Code 版本太舊（不認得 {m.group(1)}），請更新 Claude Code"
    line = raw.strip().splitlines()[-1] if raw.strip() else ""
    return f"Claude Code 出錯：{line[:120]}" if line else "Claude Code 意外結束"


class _Worker:
    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.argv = argv
        self.started = time.monotonic()
        self.prompt_text = ""
        self.proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        self._events: queue.Queue = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    self._events.put(json.loads(line))
                except ValueError:
                    self._stderr.append(line)
        except (OSError, ValueError):
            pass  # pipe closed by kill()
        self._events.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        try:
            for raw in self.proc.stderr:
                self._stderr.append(raw.decode("utf-8", "replace").rstrip())
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stderr_text(self) -> str:
        return "\n".join(self._stderr)

    def ask(
        self, text: str, timeout: float, on_text: Callable[[str], None] | None = None, first_token: float | None = None
    ) -> str:
        """`first_token`: give up early when the model has not started writing by then; once it
        has, `timeout` counts from the first word, so a slow start still leaves time to write."""
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            assert self.proc.stdin is not None
            # ASCII-only JSON: nothing for a Windows code page to mangle on the way in.
            self.proc.stdin.write((json.dumps(msg, ensure_ascii=True) + "\n").encode("ascii"))
            self.proc.stdin.flush()
        except OSError:
            self.proc.wait(timeout=2)
            raise PolishError(_explain(self.stderr_text()))

        t0 = time.monotonic()
        # Until the first word only `first_token` applies; `timeout` restarts when it comes.
        deadline = t0 + (timeout if first_token is None else first_token)
        streamed = ""
        while True:
            now = time.monotonic()
            if first_token is not None and not streamed and now - t0 >= first_token:
                raise PolishError(f"Claude 這次 {first_token:.0f} 秒還沒開始回應，先貼原稿")
            remaining = deadline - now
            if remaining <= 0:
                raise PolishError(f"Claude Code 超過 {timeout:.0f} 秒沒有回應")
            try:
                ev = self._events.get(timeout=remaining)
            except queue.Empty:
                continue
            if ev is None:
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                raise PolishError(_explain(self.stderr_text()))
            kind = ev.get("type")
            if kind == "stream_event":
                e = ev.get("event") or {}
                d = e.get("delta") or {}
                if e.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    if first_token is not None and not streamed:
                        deadline = time.monotonic() + timeout
                    streamed += d.get("text", "")
                    if on_text is not None:
                        on_text(streamed)
                elif e.get("type") == "message_stop":
                    return streamed  # also the "nothing to say" case: no assistant text comes
            elif kind == "assistant":
                message = ev.get("message") or {}
                content = message.get("content") or []
                text_out = "".join(c.get("text", "") for c in content if c.get("type") == "text")
                if message.get("error") or ev.get("error") or _ERROR_TEXT.match(text_out.strip()):
                    raise PolishError(_explain(text_out or str(ev.get("error"))))
                if text_out:
                    # The model is done writing; Claude Code's post-turn work would add seconds.
                    return text_out
            elif kind == "result":
                if ev.get("is_error") or ev.get("subtype") != "success":
                    raise PolishError(_explain(str(ev.get("result") or ev.get("subtype") or "")))
                return str(ev.get("result") or streamed)

    def kill(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            if _IS_WIN and self.argv[0].lower().endswith((".cmd", ".bat")):
                # cmd.exe would leave node running: kill the whole tree.
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    creationflags=_NO_WINDOW,
                    timeout=10,
                )
            else:
                self.proc.kill()
        except (OSError, subprocess.SubprocessError):
            log.exception("killing Claude Code failed")
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass


# --- the polisher --------------------------------------------------------------------------


def _base_flags(model: str, prompt_file: Path) -> list[tuple[str, str | None]]:
    flags: list[tuple[str, str | None]] = [
        ("-p", None),
        ("--input-format", "stream-json"),
        ("--output-format", "stream-json"),
        ("--verbose", None),
        ("--include-partial-messages", None),
        ("--safe-mode", None),
        ("--tools", ""),
        ("--strict-mcp-config", None),
        ("--no-session-persistence", None),
        ("--system-prompt-file", str(prompt_file)),
    ]
    if model:
        flags.append(("--model", model))
    return flags


def _flatten(flags: list[tuple[str, str | None]]) -> list[str]:
    out: list[str] = []
    for k, v in flags:
        out.append(k)
        if v is not None:
            out.append(v)
    return out


class ClaudeCodePolisher:
    """Thread-safe. `start()` returns at once; `polish()` blocks for one answer."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._argv: list[str] | None = None
        self._flags: list[tuple[str, str | None]] = []
        self._warm: _Worker | None = None
        self._warming = False
        self._closed = False
        self.version = ""
        self.status = "尋找 Claude Code…"
        self._prompt_file = paths.data_dir() / "claude-system-prompt.md"
        self._prompt_text = ""

    # setup

    def start(self) -> None:
        threading.Thread(target=self._setup, name="claude-setup", daemon=True).start()

    def _setup(self) -> None:
        argv = find_claude(self.cfg.claude_path)
        with self._lock:
            if argv is None:
                self._argv = None
                self.status = "找不到 Claude Code：請先安裝並登入（終端機執行 claude）"
                log.info("claude not found")
                return
            self._argv = argv
            self._flags = _base_flags(self.cfg.claude_model, self._prompt_file)
        self.version = _version(argv)
        log.info("claude at %s (%s)", argv, self.version or "version unknown")
        with self._lock:
            self.status = f"就緒（{self.cfg.claude_model or '預設模型'}）"
        if self.cfg.claude_prewarm:
            self._prewarm()

    @property
    def available(self) -> bool:
        return self._argv is not None

    def reconfigure(self, cfg: Config) -> None:
        with self._lock:
            self.cfg = cfg
            if self._argv is not None:
                self._flags = _base_flags(cfg.claude_model, self._prompt_file)
            old, self._warm = self._warm, None
        if old is not None:
            old.kill()
        if self._argv is None or cfg.claude_path:
            self._setup_async()
        elif cfg.claude_prewarm:
            self._prewarm_async()

    def _setup_async(self) -> None:
        threading.Thread(target=self._setup, name="claude-setup", daemon=True).start()

    # processes

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if not self.cfg.claude_keep_api_key_env:
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env.pop("CLAUDECODE", None)
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        env["DISABLE_AUTOUPDATER"] = "1"
        return env

    @staticmethod
    def _current_prompt() -> str:
        return prompt.system_prompt(prompt.load_glossary(paths.glossary_file()))

    def _write_prompt(self) -> str:
        text = self._current_prompt()
        if text != self._prompt_text or not self._prompt_file.exists():
            self._prompt_file.write_text(text, encoding="utf-8")
            self._prompt_text = text
        return text

    def _spawn(self) -> _Worker:
        with self._lock:
            if self._argv is None:
                raise PolishError(self.status)
            prompt_text = self._write_prompt()
            argv = self._argv + _flatten(self._flags)
        w = _Worker(argv, paths.claude_workdir(), self._env())
        w.prompt_text = prompt_text
        return w

    def _drop_flag(self, name: str) -> bool:
        with self._lock:
            before = len(self._flags)
            self._flags = [f for f in self._flags if f[0] != name]
            return len(self._flags) != before

    def _prewarm(self, replace: _Worker | None = None) -> None:
        """`replace`: swap that waiting process for a new one once the new one is up."""
        with self._lock:
            if self._warming or self._closed or self._argv is None:
                return
            if replace is not None:
                if self._warm is not replace:
                    return  # already used or replaced
            elif self._warm is not None and self._warm.alive():
                return
            self._warming = True
        try:
            for _ in range(6):
                try:
                    w = self._spawn()
                except (OSError, PolishError) as e:
                    log.warning("prewarm failed: %s", e)
                    with self._lock:
                        self.status = f"Claude Code 無法啟動：{e}"
                    return
                try:
                    w.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    with self._lock:
                        if self._closed:
                            old = w
                        else:
                            old, self._warm = self._warm, w
                            self._schedule_refresh(w)
                    if old is not None:
                        old.kill()
                    return
                # It exited on its own: usually an older CLI refusing a flag. Drop it and retry.
                err = w.stderr_text()
                m = _UNKNOWN_OPTION.search(err)
                if m and m.group(1) not in ("-p", "--input-format", "--output-format") and self._drop_flag(m.group(1)):
                    log.info("claude does not know %s; retrying without it", m.group(1))
                    continue
                log.warning("claude exited during warm-up: %s", err[-500:])
                with self._lock:
                    self.status = _explain(err)
                return
        finally:
            with self._lock:
                self._warming = False

    def _schedule_refresh(self, w: _Worker) -> None:
        t = threading.Timer(REFRESH_S, self._prewarm, kwargs={"replace": w})
        t.daemon = True
        t.start()

    def _prewarm_async(self) -> None:
        if self.cfg.claude_prewarm and not self._closed:
            threading.Thread(target=self._prewarm, name="claude-prewarm", daemon=True).start()

    # use

    def polish(self, transcript: str, on_text: Callable[[str], None] | None = None) -> str:
        with self._lock:
            w, self._warm = self._warm, None
        # A warm process started before the glossary was edited would use the old words, and one
        # left idle for long has been seen to take 20 s over its first answer.
        cold = (
            w is None
            or not w.alive()
            or w.prompt_text != self._current_prompt()
            or time.monotonic() - w.started > WARM_MAX_AGE_S
        )
        if cold:
            if w is not None:
                w.kill()
            w = self._spawn()
        t0 = time.monotonic()
        how = "cold" if cold else "warm"
        try:
            out = w.ask(prompt.user_message(transcript), self.cfg.claude_timeout_s, on_text, FIRST_TOKEN_S)
            log.info("claude answered in %.2fs (%s)", time.monotonic() - t0, how)
            with self._lock:
                self.status = f"就緒（{self.cfg.claude_model or '預設模型'}）"
            return out
        except PolishError as e:
            log.info("claude gave up after %.2fs (%s): %s", time.monotonic() - t0, how, e)
            with self._lock:
                self.status = str(e)
            raise
        finally:
            w.kill()
            self._prewarm_async()

    def complete(self, system_prompt: str, message: str, timeout: float, name: str = "meeting") -> str:
        """One answer with its own instructions (meeting notes, learning words): a fresh,
        isolated process with the same flags as dictation, so the warm one stays ready."""
        with self._lock:
            if self._argv is None:
                raise PolishError(self.status)
            prompt_file = paths.data_dir() / f"claude-{name}-prompt.md"
            prompt_file.write_text(system_prompt, encoding="utf-8")
            flags = [(k, str(prompt_file) if k == "--system-prompt-file" else v) for k, v in self._flags]
            argv = self._argv + _flatten(flags)
        w = _Worker(argv, paths.claude_workdir(), self._env())
        t0 = time.monotonic()
        try:
            return w.ask(message, timeout)
        finally:
            log.info("claude answered %s in %.1fs", name, time.monotonic() - t0)
            w.kill()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            w, self._warm = self._warm, None
        if w is not None:
            w.kill()
