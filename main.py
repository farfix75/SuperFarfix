import platform as _platform

# Windows: no console windows, and console-tool output decoded with the right
# code page (see core/win_subprocess.py for why a UnicodeDecodeError appeared).
import core.win_subprocess  # noqa: F401  (patches subprocess on import)


# ── Console must survive non-UTF-8 code pages ────────────────────────────────
# Every status line in this file carries an emoji, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# because most of these prints sit inside the receive loop it takes the session
# down on startup. Reconfiguring to UTF-8 with a replacement fallback costs
# nothing and makes the app launch the same way in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
import threading
import collections
import time
import json
import sys
import traceback
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
import google.genai.live as _genai_live

# The SDK opens the Live socket with websockets' defaults: a ping every 20 s and
# 20 s to answer it. On a network that drops silently (Wi-Fi roaming, a router
# hiccup, the PC resuming from sleep) that is up to 40 s of an assistant that
# looks alive and hears nothing. Tighter keepalive detects it in seconds and
# the run loop resumes the conversation on a fresh socket.
_orig_ws_connect = _genai_live.ws_connect


def _ws_connect(*a, **kw):
    kw.setdefault("ping_interval", 10)
    kw.setdefault("ping_timeout", 15)
    kw.setdefault("open_timeout", 15)
    kw.setdefault("close_timeout", 3)
    return _orig_ws_connect(*a, **kw)


_genai_live.ws_connect = _ws_connect
from ui import FarfixUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    search_memory, set_trim_notifier,
)

# The file-backed tools (open_app, web_search, browser_control, …) are no longer
# imported or declared here — they self-describe via a TOOL dict in their own
# actions/*.py file and are auto-discovered by core.action_loader at startup.
# Only tools that are tied to live-session state stay inline in this file
# (screen_process, close_camera, save_memory, manage_monitor, shutdown_farfix,
# system_status).
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import (
    get_brief_enabled, get_media_resolution, get_proactive_audio_enabled,
    get_push_to_talk_enabled, get_thinking_enabled, get_turn_tuning, get_voice,
    get_wake_word_enabled, save_wake_word_enabled,    get_input_device, get_output_device,
    get_voice_barge_in_enabled, get_interaction_log_enabled,
)
from core.plugin_loader        import discover_plugins
from core                      import undo as undo_stack
from core                      import confirm as confirm_gate
from core                      import audio_devices
from core.action_loader        import discover_actions
from core.echo                 import EchoGuard
from core.interaction_log      import InteractionLog
from core.viseme               import VisemeStream
from core.wake_word            import (
    WakeWordDetector, is_ready as wake_is_ready, install_and_download as wake_install,
    WAKE_PHRASE,
)
from core                      import local_llm as _local_llm

# How long the assistant stays awake with no user speech before it auto-sleeps
# again (wake-word mode only).
WAKE_SLEEP_TIMEOUT = 120.0   # seconds (2 minutes)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "gemini-3.8-live"   # override with "live_model" in config/api_keys.json
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000 
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
MIC_MIME            = f"audio/pcm;rate={SEND_SAMPLE_RATE}"

# ── Reliability knobs ─────────────────────────────────────────────────────────
# Each of these closes one way the assistant used to go deaf after a while.
TOOL_TIMEOUT      = 90.0   # s — a hung tool gets an error reply instead of freezing the turn
MIC_STALL_S       = 2.5    # s without a mic callback → the input stream is dead, reopen it
SPK_STALL_S       = 3.0    # s without a speaker callback → reopen the output stream
SPEAK_IDLE_S      = 1.2    # s of empty playback with no new audio → not speaking any more
INTERRUPT_MAX_S   = 8.0    # s — the "discard the cancelled answer" flag can never outlive this
OUT_QUEUE_MAX     = 64     # mic blocks (~4 s) buffered towards the server; oldest dropped first
PLAYBACK_AHEAD_S  = 0.18   # s of audio kept queued in front of the speaker
OUT_LATENCY_S     = 0.10   # requested output latency: short enough to stop instantly
CONNECT_TIMEOUT_S = 20.0   # s — a connect whose setup never completes is abandoned
SUMMARY_EVERY_S   = 1800   # s — session summary at most this often on routine reconnects


def _live_model() -> str:
    """Live model id, overridable from config without touching code."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            m = str(json.load(f).get("live_model") or "").strip()
        return m or LIVE_MODEL
    except Exception:
        return LIVE_MODEL


class _PcmRing:
    """Thread-safe byte FIFO between the asyncio loop and the speaker callback.

    The old playback path pushed every 100 ms batch through
    `asyncio.to_thread(stream.write, …)`. That borrowed a worker from the SAME
    default thread pool the tools run in, so a few slow or hung tools were
    enough to leave the speaker waiting for a free thread: playback stalled,
    the "speaking" flag never dropped, and the microphone — which is gated on
    that flag — stopped being streamed. The assistant went deaf.

    Here PortAudio pulls audio from its own callback thread instead. Nothing
    on the playback path can wait for a thread pool any more, and an interrupt
    is a `clear()`: silence within one device buffer rather than after half a
    second of already-written audio.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.last_pull = time.monotonic()

    def push(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)

    def seconds(self, sr: int = RECEIVE_SAMPLE_RATE) -> float:
        return len(self._buf) / (2.0 * sr)

    def pull_into(self, outdata) -> None:
        need = len(outdata)
        with self._lock:
            n = min(need, len(self._buf))
            if n:
                outdata[:n] = bytes(self._buf[:n])
                del self._buf[:n]
        if n < need:
            outdata[n:] = b"\x00" * (need - n)
        self.last_pull = time.monotonic()

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


# ── Viseme extraction ─────────────────────────────────────────────────────────
# The avatar's mouth used to be driven by one RMS value per ~200 ms write batch,
# which is five updates a second averaged over a fifth of a second — it could
# only ever flap. These read the *shape* of each 20 ms slice straight from the
# spectrum of the audio being played, so no transcript, no forced alignment and
# no language assumption: it works the same for Turkish and English.
#
# Two numbers come out. Openness tracks the first formant — F1 climbs as the jaw
# drops, so /a/ reads open and /i/ or /u/ read closed. Width tracks the second —
# F2 is high for spread vowels (/i/, /e/) and low for rounded ones (/u/, /o/).
# Extra time beyond the device's reported output latency before the microphone
# is trusted again: covers room decay and the speaker's own settling.
_TAIL_MARGIN = 0.25

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second

# Delay from handing the first bytes of a reply to an already-running output
# stream to hearing them: one callback period, plus whatever the DAC adds.
_FIRST_SOUND = CHUNK_SIZE / RECEIVE_SAMPLE_RATE      # ~43 ms
# How far past the device's own buffer the mouth's timeline may drift before it
# is re-anchored. The buffer is the hard limit on how much audio can be queued
# ahead, so anything beyond it plus a margin for clock error is impossible.
_CURSOR_SLACK = 0.15

# Erring early is the safe direction. A viewer tolerates a mouth that moves
# slightly before the sound far better than one that moves after it — the
# broadcast limits are about 45 ms of lag against 125 ms of lead — so where
# this is uncertain it is biased to lead.


def _pcm_visemes(samples, sr: int = 24000):
    """Slice a PCM block into (level, openness, width) frames, one per 20 ms.

    Returns [] on anything unexpected — the mouth falls back to loudness-only
    articulation rather than the caller having to handle an error.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a full
        # window fits stopped 1024 - 480 samples short of the end, so a 200 ms
        # batch yielded 160 ms of schedule: the mouth ran out of frames before
        # the audio ran out of sound, and each batch no longer lined up with the
        # end of the one before it. Losing 20 % of every batch is most of why
        # the mouth did not track the words.
        out = []
        for start in range(0, x.size, _VIS_HOP):
            # The level gates closures, so it is measured over exactly this
            # 20 ms and never looks ahead. The spectrum needs a longer window
            # to resolve formants and may be short-filled at the very end.
            level = _pcm_level(x[start:start + _VIS_HOP])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def _describe_tools(declarations) -> str:
    """One line per capability, straight from the live tool declarations.

    Derived rather than written down: the action and plugin registries are
    discovered at startup, so whatever the user has installed is what the model
    is told it can do. Adding a plugin extends this by itself, and removing one
    stops the model from claiming an ability it no longer has.
    """
    lines = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            desc = (d.get("description") if isinstance(d, dict)
                    else getattr(d, "description", "")) or ""
        except Exception:
            continue
        if not name:
            continue
        desc = " ".join(str(desc).split())
        lines.append(f"- {name}: {desc[:150]}" if desc else f"- {name}")
    return "\n".join(lines)


def _describe_limits(has_vision: bool, has_mic: bool) -> str:
    """The other half of self-knowledge: what is out of reach, and why.

    Derived from how the program is actually built, not from a list of refusals.
    A model that knows its boundaries stops improvising around them, and stating
    them as architecture rather than as rules keeps the answer honest in any
    language.
    """
    out = [
        "- Anything not listed above is outside your reach. Say so in one clause "
        "and offer the nearest thing you can actually do — never mime an action "
        "you cannot take, and never report a result you did not get.",
        "- You act on this machine only. You cannot reach the user's other "
        "devices, accounts or hardware except through the tools listed above.",
        "- You remember what is in the memory block and what has been said this "
        "session. Anything else you were told before is gone unless it was saved.",
    ]
    if has_vision:
        out.append(
            "- Your sight is not continuous. You see nothing until you call a "
            "vision tool, and then only that single frame at that moment — you "
            "cannot watch, monitor or notice something changing on screen.")
    else:
        out.append("- You have no sight at all in this build.")
    if has_mic:
        out.append(
            "- You hear nothing while the microphone is muted, and you cannot "
            "unmute it yourself.")
    return "\n".join(out)


def _render_prompt(template: str, values: dict) -> str:
    """Fill {tokens} in the prompt template.

    A plain replace rather than str.format: the file is meant to be edited by
    hand, and a stray brace in someone's own wording must never take the app
    down at startup.
    """
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return str(json.load(f).get("gemini_api_key") or "").strip()
    except Exception:
        return ""


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are FARFIX, a personal AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# Transcript chunks shorter than this may legitimately repeat ("evet, evet"),
# so only longer ones are treated as duplicates.
_REPEAT_MIN = 12


def _is_repeat_chunk(txt: str, buf: list) -> bool:
    """True if this transcript chunk has already been seen this turn.

    Guards against the API re-sending the tail of a response across the several
    turn_completes a tool-using turn produces.
    """
    if len(txt) < _REPEAT_MIN:
        return bool(buf) and txt == buf[-1]
    joined = " ".join(buf)
    return txt in joined

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    # ── Inline tools ─────────────────────────────────────────────────────────
    # These stay here (rather than in an actions/*.py TOOL dict) because their
    # handling is woven into live-session state — vision capture/injection,
    # camera stream, memory writes, the monitor engine, and shutdown. All other
    # tools live in their own action file and are auto-discovered by
    # core.action_loader (see FarfixLive.__init__).
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when the user says (in ANY language): close camera, stop camera, "
            "turn off camera, that's creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "FARFIX checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_farfix",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Farfix. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
]

class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


class FarfixLive:
    def __init__(self, ui: FarfixUI):
        self.ui             = ui
        self._asst_name     = "FARFIX"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        # Transcript-driven mouth shapes for the avatar. Fed from the receive
        # loop as words arrive, drained by the playback loop against the audio.
        self._visemes              = VisemeStream()
        self._last_out_logged      = ""      # de-dupes a re-sent transcript tail
        # Push-to-talk
        self._ptt_enabled          = False
        self._ptt_held             = False
        self._ptt                  = None    # core.hotkey.PushToTalk
        self._out_level            = 0.0     # level of the audio being played right now
        self._echo                 = EchoGuard()
        # Voice barge-in (see _listen_audio): how many consecutive mic blocks
        # the guard has called "not our echo" while FARFIX talks, and the
        # last ~0.8 s of mic audio, so the words you started interrupting
        # with are sent to the model instead of being lost.
        self._barge_run            = 0
        self._barge_buf            = collections.deque(maxlen=12)
        self._barge_in_enabled     = get_voice_barge_in_enabled()
        # `stream.write()` returns when the buffer accepts the audio, not when the
        # speaker has finished with it, so sound is still in the room after the
        # speaking flag drops. Streaming the microphone during that gap is how an
        # assistant ends up answering itself. Measured from the device rather than
        # guessed; see _play_audio.
        self._out_latency          = 0.20    # seconds, replaced with the real value
        self._tail_until           = 0.0     # monotonic time the echo tail expires
        # Wall-clock time at which the audio written next will begin to sound.
        # The mouth is scheduled against this, never against "now": batches are
        # handed to the device far faster than they play, so "now" ran the lips
        # ahead of the words and cut every schedule short. 0 = nothing playing.
        self._play_cursor          = 0.0
        # ── Liveness state (see the constants at the top of the file) ────────
        self._ring                 = _PcmRing()   # audio waiting for the speaker
        self._gen_active           = False   # server is still generating the current answer
        self._interrupted_at       = 0.0     # when _interrupted was raised (safety expiry)
        self._last_audio_rx        = 0.0     # monotonic time the last audio chunk arrived
        self._mic_last_cb          = 0.0     # monotonic time of the last mic callback
        self._tool_tasks: set      = set()   # tool calls running beside the receive loop
        self._cancelled_calls: set = set()   # ids the server withdrew (tool_call_cancellation)
        self._goaway_deadline      = None    # monotonic deadline announced by GoAway
        self._quiet_reconnect      = False   # routine reconnect: no log spam, no UI flash
        self._first_connect        = True
        self._model_name           = LIVE_MODEL
        # Tools get their own pool. They used to share the default executor
        # with everything else, so a handful of slow ones could starve the rest.
        self._tool_pool            = ThreadPoolExecutor(max_workers=16,
                                                        thread_name_prefix="tool")
        # Opening/closing audio devices gets its own threads too: it must work
        # even when every other pool is busy, or a reconnect cannot hear.
        self._audio_pool           = ThreadPoolExecutor(max_workers=2,
                                                        thread_name_prefix="audio")
        self._last_summary         = time.monotonic()
        self.ui.on_push_to_talk   = self.set_push_to_talk
        self.ui.ptt_hold          = self._on_ptt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._conn_backoff  = 0
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary
        # Registro strutturato su disco: a differenza di _session_log qui
        # restano anche i tempi di risposta, le azioni eseguite e gli esiti,
        # cioè le colonne che rendono i dati utilizzabili in seguito.
        self._ilog = InteractionLog(enabled=get_interaction_log_enabled())

        self._enhanced_live = True  # proactive audio; auto-disabled if the server rejects it
        self._tuned_live    = True  # turn-taking / media / thinking knobs; same fallback

        _base_dir = Path(__file__).resolve().parent
        _inline_names = {t["name"] for t in TOOL_DECLARATIONS}

        # File-backed tools: every actions/*.py with a TOOL dict, discovered the
        # same way plugins are. Reserved names = the inline tools above, so an
        # action can never shadow one.
        self._action_registry = discover_actions(
            actions_dir=_base_dir / "actions",
            reserved_names=_inline_names,
            logger=lambda msg: print(f"[Actions] {msg}"),
        )

        # Plugins must not collide with either an inline tool or a discovered action.
        _core_names = _inline_names | self._action_registry.names()
        self._plugin_registry = discover_plugins(
            plugins_dir=_base_dir / "plugins",
            core_tool_names=_core_names,
            # Console gets the full boot transcript; the activity log gets only
            # what the user has to know about. Every plugin loading correctly is
            # the expected case and does not belong in their conversation.
            logger=lambda msg: print(f"[Plugins] {msg}"),
            notify=lambda msg: self.ui.write_log(f"SYS: {msg}"),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.get_plugin_settings = self._plugin_registry.settings_schemas  # ⚙ settings tab
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── Wake word ────────────────────────────────────────────────────────
        # _awake gates the mic (see _listen_audio) and the background speakers.
        # It is True whenever wake word is OFF, so default behaviour is unchanged.
        self._wake_enabled     = get_wake_word_enabled()
        self._awake            = not self._wake_enabled
        self._wake_detector: WakeWordDetector | None = None
        self._wake_sleep_timeout = WAKE_SLEEP_TIMEOUT

        # Restore the saved push-to-talk preference. Doing it here rather than
        # in __init__ means the hotkey thread only exists once there is a
        # session to talk to.
        if get_push_to_talk_enabled():
            try:
                self.set_push_to_talk(True)
            except Exception as e:
                print(f"[FARFIX] ⚠ Push-to-talk unavailable: {e}")
        # UI control surface for the Wake Word settings section.
        self.ui.wake_is_ready    = wake_is_ready          # () -> bool
        self.ui.wake_get_state   = self._wake_state       # () -> dict
        self.ui.on_wake_toggle   = self._ui_wake_toggle   # (enable: bool) -> str
        self.ui.on_wake_manual   = self._ui_wake_manual   # () -> toggle awake/asleep
        self.ui.on_wake_install  = self._ui_wake_install  # () -> (ok, msg)

    # ── Wake word: state machine ─────────────────────────────────────────────

    def _wake_state(self) -> dict:
        # A loaded, running detector is definitively ready; otherwise fall back
        # to the cheap on-disk model-file check (no Model construction).
        ready = bool(self._wake_detector and self._wake_detector.ready) or wake_is_ready()
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def _ensure_wake_detector(self) -> bool:
        """Load the detector once (model loads on first start). Idempotent."""
        if self._wake_detector is None:
            self._wake_detector = WakeWordDetector(
                on_detect=self._on_wake_detected,
                logger=lambda m: print(f"[Wake] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"),
            )
        if not self._wake_detector.ready:
            return self._wake_detector.start()
        return True

    def _on_wake_detected(self) -> None:
        """Called from the detector thread when the wake phrase is heard."""
        self.wake(reason="wake word")

    def wake(self, reason: str = "wake word") -> None:
        if self._awake:
            return
        self._awake = True
        self._last_user_speech = time.monotonic()   # start the auto-sleep clock now
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Awake — {reason}.")

    def sleep(self, reason: str = "timeout") -> None:
        if not self._awake:
            return
        self._awake = False
        self.set_speaking(False)
        self.ui.set_state("SLEEPING")
        self.ui.write_log(f"SYS: Sleeping — {reason}. Say '{WAKE_PHRASE}' to wake me.")

    async def _run_sleep_watch(self) -> None:
        """Auto-sleep after the configured silence window (wake-word mode only)."""
        while True:
            await asyncio.sleep(5)
            if not self._wake_enabled or not self._awake:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            if (time.monotonic() - self._last_user_speech) > self._wake_sleep_timeout:
                self.sleep(reason="no speech for 2 minutes")

    # ── Wake word: UI callbacks (called from the Qt thread) ──────────────────

    def _ui_wake_toggle(self, enable: bool) -> str:
        """Enable/disable wake word from the settings UI. Returns a status token:
        'enabled' | 'disabled' | 'need_download'."""
        if enable:
            if not wake_is_ready():
                return "need_download"
            self._wake_enabled = True
            save_wake_word_enabled(True)
            self._ensure_wake_detector()
            self.sleep(reason="wake word enabled")
            return "enabled"
        else:
            self._wake_enabled = False
            save_wake_word_enabled(False)
            self.wake(reason="wake word disabled")
            return "disabled"

    def _ui_wake_manual(self) -> None:
        """Manual sleep/wake button in the UI."""
        if not self._wake_enabled:
            return
        if self._awake:
            self.sleep(reason="you tapped sleep")
        else:
            self.wake(reason="you tapped wake")

    def _ui_wake_install(self) -> tuple[bool, str]:
        """Download openwakeword + the model (runs in a UI worker thread)."""
        # Triggered by the user pressing the button, so its progress is exactly
        # what they are waiting to see.
        return wake_install(logger=lambda m: print(f"[Wake] {m}"),
                            notify=lambda m: self.ui.write_log(f"SYS: {m}"))

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask FARFIX to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self._send_text(instruction)
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = "",
                          quiet: bool = False):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        self._quiet_reconnect  = quiet
        if loop and ev is not None:
            loop.call_soon_threadsafe(ev.set)

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        self.request_reconnect(keep_context=True, reason="audio device")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        if self._quiet_reconnect:
            print(f"[FARFIX] 🔄 Routine reconnect ({reason})")
        else:
            self.ui.write_log(
                f"SYS: Applying {reason} — reconnecting"
                + ("..." if keep else " (starting a fresh conversation)...")
            )
        raise _ReconnectSignal(keep_context=keep)

    async def _watch_goaway(self):
        """Session-scoped task: the server announced (GoAway) that it will
        drop this connection — it does so roughly every 10 minutes.

        Before, nothing listened for that. The socket simply died, often in the
        middle of an answer, the run loop treated it as an error, flashed
        SLEEPING and waited three seconds. Now the switch to a fresh connection
        (same conversation, via the resumption handle) is made at the first
        quiet moment, or just before the deadline if there is none."""
        while True:
            await asyncio.sleep(0.2)
            dl = self._goaway_deadline
            if dl is None:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            idle = (not speaking and not self._gen_active
                    and not self._tool_tasks and len(self._ring) == 0)
            if idle or time.monotonic() >= dl:
                self._goaway_deadline = None
                self._reconnect_keep   = True
                self._reconnect_reason = "server connection refresh"
                self._quiet_reconnect  = True
                print("[FARFIX] 🔄 GoAway — switching to a fresh connection")
                raise _ReconnectSignal(keep_context=True)

    def _bcast(self, msg: dict) -> None:
        """Fire-and-forget dashboard update with a deadline.

        It used to be awaited inline — including right after connecting, before
        the audio tasks were started. A phone that had gone to sleep with the
        dashboard open leaves a half-dead websocket, a send to it can wait for
        a long time, and the whole assistant waited with it: connected, but
        never listening. Now it can never hold anything up."""
        if not self._dashboard:
            return

        async def _go():
            try:
                await asyncio.wait_for(self._dashboard.broadcast(msg), timeout=3.0)
            except Exception:
                pass
        try:
            asyncio.get_running_loop().create_task(_go())
        except RuntimeError:
            pass

    def _supports_client_content(self) -> bool:
        """gemini-3.1-flash-live only accepts send_client_content to seed the
        initial history; any later call closes the socket with 1007. 3.8 (and
        2.5) accept it for the whole session."""
        return "3.1-flash-live" not in self._model_name

    async def _send_text(self, text: str, session=None) -> None:
        """One place that sends a text turn, in the form this model accepts."""
        s = session or self.session
        if s is None:
            return
        if self._supports_client_content():
            await s.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True,
            )
        else:
            await s.send_realtime_input(text=text)

    def _enqueue_out(self, item: dict) -> None:
        """Queue a mic block for the server; when the link is slow, drop the
        OLDEST block rather than raising QueueFull inside a loop callback on
        every block (which flooded the console and dropped the newest speech)."""
        q = self.out_queue
        if q is None:
            return
        while True:
            try:
                q.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    return

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        # Respect wake-word sleep: a typed command must not be answered while
        # asleep either (the sleep gate is not just for the mic). Wake first with
        # the wake phrase or the WAKE NOW button.
        if self._wake_enabled and not self._awake:
            self.ui.write_log(f"SYS: I'm asleep — say '{WAKE_PHRASE}' or tap WAKE NOW first.")
            return

        # ── Local LLM intent hint (non-blocking, best-effort) ───────────────────────
        # Classify the intent locally (<400 ms) while the command is sent to
        # Gemini Live. Used for: logging, future pre-routing, and offline fallback.
        def _intent_async(cmd=text):
            try:
                intent = _local_llm.quick_intent(cmd)
                if intent["action"] != "unknown":
                    print(f"[LocalLLM] intent: {intent}")
            except Exception:
                pass
        threading.Thread(target=_intent_async, daemon=True, name="local-llm-intent").start()

        if not self._loop or not self.session:
            # ── Gemini offline fallback ────────────────────────────────────────
            # When the Live session is being rebuilt (reconnect, GoAway,
            # first connect still pending), we answer the user locally
            # instead of silently dropping their message.
            if _local_llm.is_available():
                def _local_answer(cmd=text):
                    try:
                        reply = _local_llm.quick_text(cmd, max_tokens=200, timeout=20)
                        self.ui.write_log(f"[Local] {reply}")
                        self.ui.write_log("SYS: (risposta locale — Gemini si sta riconnettendo)")
                    except Exception as e:
                        self.ui.write_log(f"SYS: Gemini non disponibile al momento ({e}).")
                threading.Thread(target=_local_answer, daemon=True, name="local-llm-fallback").start()
            else:
                self.ui.write_log("SYS: Gemini non disponibile. Riconnessione in corso...")
            return

        asyncio.run_coroutine_threadsafe(
            self._send_text(text),
            self._loop
        )

    def _tail_active(self) -> bool:
        """True while the speakers may still be finishing our last sentence."""
        return time.monotonic() < self._tail_until

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            changed = self._is_speaking != value
            self._is_speaking = value
        if not changed:
            # Called for every 100 ms batch while talking — re-emitting the Qt
            # state signal each time was pure overhead on the UI thread.
            return
        if value:
            self._tail_until = 0.0
        else:
            # Hold the guard open across the device's own output latency plus a
            # margin for the room. The microphone is NOT muted during it — the
            # guard still lets a genuine reply through, so answering instantly
            # still works. Only our own echo is dropped.
            self._tail_until = time.monotonic() + self._out_latency + _TAIL_MARGIN
        if not value:
            # The echo history is deliberately NOT cleared here: the tail above
            # still needs it to recognise our own voice. It is dropped when the
            # tail expires. What the guard learned about the room always stays.
            self._out_level = 0.0
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def set_push_to_talk(self, enabled: bool) -> str:
        """Turn hold-to-talk on or off. Returns the scope actually achieved."""
        from core.hotkey import PushToTalk

        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"

        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        # A window-scoped chord is a real limitation, not a detail — say it once
        # in the log so nobody wonders why it does nothing while another app is
        # focused. Reporting it must never be able to undo the thing it reports.
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global"
                   else " (works while this window is focused)."))
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        """Chord pressed or released — may arrive on the hotkey thread."""
        self._ptt_held = held
        if held:
            # Holding the key is also a way to wake it, so push-to-talk works
            # without having to say the wake word first.
            if self._wake_enabled and not self._awake:
                self._awake = True
                self._last_user_speech = time.monotonic()
        try:
            self.ui.set_state("LISTENING" if held else "SLEEPING")
        except Exception:
            pass

    def interrupt(self, reason: str = "manual") -> None:
        """Stop FARFIX mid-speech: drain queued audio and open mic immediately."""
        # Audio arrives much faster than it plays, so by the time you talk over
        # an answer the server has usually finished generating it already.
        # Raising the "discard until turn_complete" flag in that case made the
        # app throw away the reply to what you just said, because the only
        # turn_complete left to come was that reply's. Only discard when there
        # is genuinely more of the cancelled answer still on its way.
        self._interrupted = bool(self._gen_active)
        self._interrupted_at = time.monotonic()
        self._barge_run = 0
        self._ilog.interrupted(reason)
        self._ring.clear()          # silence now, not after the device buffer
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[FARFIX] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        # The words we were about to mouth are never going to be spoken now.
        self._visemes.reset()
        self._play_cursor = 0.0     # next batch starts a fresh timeline
        if self._turn_done_event:
            self._turn_done_event.clear()
        if reason == "voice":
            self.ui.write_log("SYS: Interrotto dalla tua voce — ti ascolto...")
        else:
            self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_text(text),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "FARFIX").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "FARFIX"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # Address form is a property of the language being spoken, so it is
        # stated as a principle rather than a two-language lookup — the model
        # already knows the respectful register of whatever language it is in.
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else 'ADDRESS: Address the user with the ordinary respectful form '
                      'for a superior in the language you are currently speaking — '
                      '"sir" in English, its everyday equivalent in any other '
                      'language. Never an archaic or aristocratic form, and never '
                      'the form from a different language than the one you are '
                      'speaking in this sentence.')
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Everything the model is told about *itself* is derived here, not
        # written into prompt.txt: the name comes from config, the platform from
        # the host, the capability list from the registries that were just
        # discovered. Rename the assistant, add a plugin or move to another OS
        # and this follows without anyone editing a prompt.
        _all_decls = (TOOL_DECLARATIONS
                      + self._action_registry.get_tool_declarations()
                      + self._plugin_registry.get_tool_declarations())
        _names = {(d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
                  for d in _all_decls}
        sys_prompt = _render_prompt(sys_prompt, {
            "assistant_name": self._asst_name,
            "platform": f"{_platform.system()} {_platform.release()}".strip(),
            "capabilities": _describe_tools(_all_decls),
            "limits": _describe_limits(
                has_vision="screen_process" in _names,
                has_mic=True,
            ),
        })

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _all_decls}],
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — FARFIX can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Proactive audio: FARFIX stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            # (Affective dialog was dropped: gemini-3.1-flash-live does not
            #  support it, and it never reliably detected tone in practice.
            #  To restore it on a 2.5 native-audio model, add back:
            #  cfg["enable_affective_dialog"] = True )
            if get_proactive_audio_enabled():
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)

        if self._tuned_live:
            cfg.update(self._tuning_config())

        return types.LiveConnectConfig(**cfg)

    def _tuning_config(self) -> dict:
        """The optional knobs, kept apart so one bad field can be dropped wholesale.

        Every one of these is a preview-API field. If a future model release
        stops accepting any of them the connection fails at setup, so the run
        loop turns `_tuned_live` off and reconnects on the plain config rather
        than leaving the user with an assistant that will not start.
        """
        out: dict = {}

        # How long the server waits through a pause before deciding your turn is
        # over. This — not the size of the prompt — is what most of the delay
        # before a reply actually is, and the default has to suit everybody, so
        # it is necessarily cautious.
        turn = get_turn_tuning()
        if turn.get("enabled", True):
            detect = types.AutomaticActivityDetection(
                silence_duration_ms=turn["silence_ms"],
                prefix_padding_ms=turn["prefix_ms"],
            )
            if turn["end_sensitivity"] == "high":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_HIGH
            elif turn["end_sensitivity"] == "low":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_LOW
            if turn["start_sensitivity"] == "high":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_HIGH
            elif turn["start_sensitivity"] == "low":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_LOW
            out["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=detect)

        # Screenshots and camera frames are tokenised at this resolution and then
        # stay in the session's context. 'medium' keeps on-screen text legible
        # for a fraction of a full-resolution frame.
        res = get_media_resolution()
        if res != "default":
            out["media_resolution"] = {
                "low":    types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high":   types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[res]

        # Thinking is left at the server default deliberately. Forcing the budget
        # to zero was measured on gemini-3.1-flash-live over interleaved trials
        # and did not make the first word arrive sooner — this model does not
        # appear to deliberate on the Live path, so pinning the field only adds a
        # way for a future release to behave differently. Set "thinking_enabled"
        # in config/api_keys.json to true to let it reason instead.
        # gemini-3.8-live rejects any thinking_config at setup; the extended-
        # thinking variant and 3.1 take a thinking_level, never a budget (the
        # old thinking_budget=-1 was a 2.5-era field).
        if get_thinking_enabled() and "3.8-live" in self._model_name \
                and "extended" not in self._model_name:
            pass
        elif get_thinking_enabled():
            out["thinking_config"] = types.ThinkingConfig(thinking_level="low")

        return out

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        _tool_t0 = time.monotonic()

        print(f"[FARFIX] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")


        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_running_loop()
        result = "Done."

        async def _blocking(fn, *a):
            # Own pool, bounded wait: a tool that hangs costs one worker thread,
            # never the conversation.
            return await asyncio.wait_for(
                loop.run_in_executor(self._tool_pool, fn, *a), TOOL_TIMEOUT)

        try:
            if name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await _blocking(undo_stack.undo_last)

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await _blocking(_capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await _blocking(_capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    # The image is attached to this same exchange, so there is
                    # nothing to stall for and nothing to announce. Asking for an
                    # acknowledgement here is what produced two spoken answers —
                    # the model filled that turn by answering the question from
                    # imagination, then answered it again once it could see.
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured and attached to this "
                        f"same exchange. Do not acknowledge and do not answer yet — the image "
                        f"is arriving with this result. Reply once, from what you actually see "
                        f"in it."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "system_status":
                r = await _blocking(get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_farfix":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self._send_text("Say a brief natural goodbye to the user.")
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif self._action_registry.has(name):
                # file_processor: fall back to the currently-uploaded file when none is given
                if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                _ctx = {"player": self.ui, "speak": self.speak,
                        "response": None, "session_memory": None}
                r = await _blocking(lambda: self._action_registry.run(name, args, _ctx))
                result = r or "Done."
                # web_search: mirror results to the on-screen content panel
                if (name == "web_search" and r
                        and not r.startswith("No results")
                        and not r.startswith("Search failed")):
                    _mode  = args.get("mode", "search")
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)

            else:
                if self._plugin_registry.has(name):
                    r = await _blocking(
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except asyncio.TimeoutError:
            result = (f"Tool '{name}' did not finish within {int(TOOL_TIMEOUT)} s and "
                      "was abandoned. Tell the user briefly; do not retry automatically.")
            self.ui.write_log(f"ERR: {name} — timed out after {int(TOOL_TIMEOUT)} s")
            if name == "screen_process":
                self._vision_busy = False
        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            # The failure already goes back in the tool response, and the model
            # phrases it. Also injecting "Sir, X encountered an error" as a new
            # user turn made it talk about the same failure twice.
            self.ui.write_log(f"ERR: {name} — {str(e)[:120]}")
            if name == "screen_process":
                self._vision_busy = False

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[FARFIX] 📤 {name} → {str(result)[:80]}")
        # Quali azioni falliscono, e quanto costano, è la statistica che dice
        # dove intervenire: senza misura si ottimizza a sensazione.
        _txt = str(result)
        self._ilog.tool_used(
            name,
            ok=not _txt.startswith((f"Tool '{name}' failed",
                                    f"Tool '{name}' did not finish",
                                    "Unknown tool:")),
            ms=(time.monotonic() - _tool_t0) * 1000.0,
        )

        # A tool that declared itself NON_BLOCKING also says when its answer may
        # re-enter the conversation. Without this the model finishes whatever it
        # was saying and then reads the result out on top of it — which, for
        # something like a phone call already ringing, is exactly the noise the
        # non-blocking call was meant to avoid. Tools that declared nothing get
        # the API default and behave as they always have.
        _sched = (self._action_registry.scheduling(name)
                  or self._plugin_registry.scheduling(name))
        _extra = {"scheduling": _sched} if _sched else {}
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result},
            **_extra
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            # Gemini 3.x Live rejects the old realtime_input.media_chunks field
            # (what `media=...` maps to) and closes the socket with a 1007. Send
            # mic / phone PCM through the new `audio` field instead. Queue items
            # are {"data": <bytes>, "mime_type": <str>} from _listen_audio and
            # the phone relay.
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=msg["data"],
                    mime_type=msg.get("mime_type") or MIC_MIME,
                )
            )

    async def _listen_audio(self):
        print("[FARFIX] 🎤 Mic started")
        loop = asyncio.get_running_loop()

        def callback(indata, frames, time_info, status):
            # Heartbeat for the stall watchdog below — first line, so it ticks
            # in every state (asleep, speaking, muted) the stream is alive in.
            self._mic_last_cb = time.monotonic()
            # ── Wake-word gate ───────────────────────────────────────────────
            # While asleep, the mic audio NEVER goes to Gemini (nothing is
            # streamed, so FARFIX can't respond to speech not addressed to it and
            # nothing leaves the machine). Frames are instead handed to the local
            # detector, which runs its model in ITS OWN thread — the cost here is
            # only a queue push, so the audio path is never slowed. When wake word
            # is off (default) or we're awake, this is a single boolean check.
            if self._wake_enabled and not self._awake:
                det = self._wake_detector
                if det is not None:
                    det.feed(indata)
                return
            with self._speaking_lock:
                farfix_speaking = self._is_speaking

            # ── Barge-in ─────────────────────────────────────────────────────
            # While FARFIX talks the mic is not streamed, but it is still worth
            # listening to locally: if the user starts speaking, cut the answer
            # short the way a person would stop when interrupted.
            #
            # The whole difficulty is echo — on speakers the mic hears FARFIX.
            # So the test is not "is the mic loud" but "is the mic louder than
            # the echo of what we are playing right now", sustained long enough
            # that a cough or a keystroke cannot trigger it.
            if farfix_speaking:
                # Nothing is streamed to the model while FARFIX talks, but each
                # block is classified locally. When `required_blocks` in a row
                # are a voice that is not our own echo, FARFIX stops at once
                # and the buffered start of your sentence is sent, so the
                # model hears everything you said and answers that.
                if (not self._barge_in_enabled or self.ui.muted
                        or self._phone_active
                        or (self._ptt_enabled and not self._ptt_held)):
                    return
                try:
                    data = indata.tobytes()
                    self._barge_buf.append(data)
                    if self._echo.is_user_speech(indata, SEND_SAMPLE_RATE,
                                                 _pcm_level(indata)):
                        self._barge_run += 1
                    else:
                        self._barge_run = 0
                    if self._barge_run >= self._echo.required_blocks:
                        self._barge_run = 0
                        pending = list(self._barge_buf)
                        self._barge_buf.clear()

                        def _do_barge(chunks=pending):
                            self.interrupt("voice")
                            for c in chunks:
                                self._enqueue_out({"data": c, "mime_type": MIC_MIME})
                        loop.call_soon_threadsafe(_do_barge)
                except Exception:
                    self._barge_run = 0
                return

            if self._barge_buf:
                self._barge_buf.clear()
                self._barge_run = 0

            # ── Echo tail ────────────────────────────────────────────────────
            # The speaking flag has dropped but the speakers have not finished.
            # Sending this to the model is how an assistant hears itself, decides
            # it was addressed, and answers its own last sentence. The microphone
            # stays OPEN — the guard only drops blocks that are our own voice, so
            # replying the instant it stops still works.
            if self._tail_active():
                try:
                    if not self._echo.is_user_speech(
                            indata, SEND_SAMPLE_RATE, _pcm_level(indata)):
                        return
                    self._tail_until = 0.0      # a real voice ends the tail early
                except Exception:
                    return
            elif self._echo._hist:
                self._echo.reset()

            # ── Push-to-talk ─────────────────────────────────────────────────
            # When it is on the microphone is closed by default and the chord
            # opens it, which is the whole point: nothing leaves the machine
            # unless you are holding the key.
            if self._ptt_enabled and not self._ptt_held:
                return

            if not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self._enqueue_out, {"data": data, "mime_type": MIC_MIME}
                )
                # Feed the live mic level to the HUD so the waveform reacts to
                # the user's actual voice while listening. Purely cosmetic — any
                # failure here must never disturb the mic.
                try:
                    self.ui.set_audio_level(_pcm_level(indata))
                except Exception:
                    pass

        try:
            def _open_mic(dev):
                return sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[FARFIX] 🎤 Input device: {_mic_name}")

            def _open_best():
                try:
                    st = _open_mic(_mic_dev)
                except Exception as _e:
                    # A device the picker listed but the driver will not open
                    # right now — exclusive mode, a webcam already in use, a
                    # virtual mic whose source went away. Chosen hardware
                    # failing must never mean the assistant cannot hear at all.
                    if _mic_dev is None:
                        raise
                    print(f"[FARFIX] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                    self.ui.write_log(
                        f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                    )
                    st = _open_mic(None)
                st.start()
                return st

            # ── Stall watchdog ─────────────────────────────────────────────
            # A PortAudio input stream can die without raising anything: a USB
            # headset that power-saves, the Windows audio service restarting, a
            # Bluetooth profile switch, another app grabbing the device in
            # exclusive mode. The callback simply stops being called. The old
            # loop slept forever inside `with stream:` and never noticed — the
            # assistant just stopped hearing, minutes into a session, with no
            # error anywhere. Now a silent stream is closed and reopened.
            stream = await asyncio.get_running_loop().run_in_executor(self._audio_pool, _open_best)
            self._mic_last_cb = time.monotonic()
            print("[FARFIX] 🎤 Mic stream open")
            fails = 0
            try:
                while True:
                    await asyncio.sleep(0.25)
                    stale = time.monotonic() - self._mic_last_cb
                    if stream.active and stale < MIC_STALL_S:
                        fails = 0
                        continue
                    print(f"[FARFIX] ⚠️  Mic silent for {stale:.1f}s — reopening")
                    try:
                        stream.abort(ignore_errors=True)
                        stream.close(ignore_errors=True)
                    except Exception:
                        pass
                    try:
                        stream = await asyncio.get_running_loop().run_in_executor(self._audio_pool, _open_best)
                        self._mic_last_cb = time.monotonic()
                        if fails:
                            self.ui.write_log("SYS: Microphone recovered.")
                        fails = 0
                    except Exception as _e:
                        fails += 1
                        if fails == 1:
                            self.ui.write_log(f"SYS: Microphone lost — retrying ({_e}).")
                        await asyncio.sleep(min(5.0, 0.5 * fails))
            finally:
                try:
                    stream.abort(ignore_errors=True)
                    stream.close(ignore_errors=True)
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[FARFIX] ❌ Mic: {e}")
            raise

    async def _flush_pending_vision(self) -> bool:
        """Send a captured frame immediately after its tool response.

        The frame is already in hand by the time `screen_process` returns — the
        capture happened inside the tool call. The old flow still made the model
        speak a turn first and only injected the image on that turn's
        turn_complete, which cost a whole extra round trip AND produced two
        spoken answers: one improvised without the picture, then the real one.
        Sending it here means the model has the tool result and the image before
        it generates anything, so the user gets one answer, sooner.
        """
        if not (self._pending_vision and self.session):
            return False

        import base64 as _b64
        img_b, mime_t, question, angle = self._pending_vision
        self._pending_vision = None
        b64 = _b64.b64encode(img_b).decode("ascii")
        print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")

        # Label the source. Without it the image arrives carrying nothing but
        # the user's own sentence, and a screenshot of this app — which has a
        # face in the middle of it — got read as a photo of the user. What the
        # label *means* is explained once, in the generated [SELF] block.
        src = ("[IMAGE SOURCE: WEBCAM]" if angle == "camera"
               else "[IMAGE SOURCE: SCREEN CAPTURE]")
        if self._supports_client_content():
            await self.session.send_client_content(
                turns={"role": "user", "parts": [
                    {"inline_data": {"mime_type": mime_t, "data": b64}},
                    {"text": f"{src}\n\n{question}"},
                ]},
                turn_complete=True,
            )
        else:
            await self.session.send_realtime_input(
                video=types.Blob(data=img_b, mime_type=mime_t))
            await self.session.send_realtime_input(text=f"{src}\n\n{question}")

        if self._vision_cam_active:
            # Camera: stay busy until FARFIX has finished speaking the answer,
            # then close the preview.
            self._vision_cam_active    = False
            self._vision_close_pending = True
        else:
            self._vision_busy = False
        return True

    async def _receive_audio(self):
        print("[FARFIX] 👂 Recv started")
        out_buf, in_buf = [], []
        session = self.session

        try:
            while True:
                async for response in session.receive():

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically. `resumable` goes false
                    # while a turn is mid-flight — replaying a handle from that
                    # moment is what the flag exists to prevent — so only
                    # resumable handles are kept.
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            if self._resume_handle is None:
                                print("[FARFIX] 🔗 Session resumption armed")
                            self._resume_handle = _sru.new_handle

                    # ── GoAway: this connection is about to be closed ────────
                    _ga = getattr(response, "go_away", None)
                    if _ga is not None and self._goaway_deadline is None:
                        left = 10.0
                        try:
                            tl = getattr(_ga, "time_left", None)
                            if tl is not None:
                                left = (tl.total_seconds() if hasattr(tl, "total_seconds")
                                        else float(str(tl).rstrip("s")))
                        except Exception:
                            pass
                        # Leave a margin: the handover itself takes a moment.
                        self._goaway_deadline = time.monotonic() + max(0.5, left - 3.0)
                        print(f"[FARFIX] ⏳ GoAway — {left:.0f}s left on this connection")

                    # ── Server withdrew tool calls (user moved on) ───────────
                    _tcc = getattr(response, "tool_call_cancellation", None)
                    if _tcc is not None and getattr(_tcc, "ids", None):
                        for _id in _tcc.ids:
                            self._cancelled_calls.add(_id)
                        print(f"[FARFIX] 🚫 Tool call(s) cancelled: {list(_tcc.ids)}")

                    if response.data:
                        self._gen_active = True
                        self._last_audio_rx = time.monotonic()
                        if self._interrupted and (
                                time.monotonic() - self._interrupted_at) > INTERRUPT_MAX_S:
                            # Safety valve: whatever we were waiting for is not
                            # coming. Never let this flag mute the assistant.
                            self._interrupted = False
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            self._ilog.first_audio_received()
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        # The server heard you and cancelled the answer it
                        # was giving. Stop playback here too, and clear the
                        # local "interrupted" flag: there is no turn_complete
                        # coming for the cancelled answer, so waiting for one
                        # would silently discard the reply to what you said.
                        if getattr(sc, "interrupted", False):
                            self._gen_active = False
                            if not self._interrupted:
                                self.interrupt("voice")
                            self._interrupted = False
                            out_buf = []
                            self._visemes.reset()

                        if getattr(sc, "generation_complete", False):
                            self._gen_active = False

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            # A turn that involves a tool call passes through
                            # several turn_completes, and the API re-sends the
                            # tail of the transcript across them.
                            if txt and not _is_repeat_chunk(txt, out_buf):
                                out_buf.append(txt)
                                if not self._interrupted:
                                    self._visemes.feed_text(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                self._ilog.user_stopped_speaking()

                        if sc.turn_complete:
                            self._gen_active = False
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response,
                            # clear the flag. The user's words are still logged —
                            # only the cancelled answer is dropped.
                            if self._interrupted:
                                self._interrupted = False
                                out_buf = []
                                self._visemes.reset()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_out_logged = ""   # new exchange
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    self._bcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    })
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            # Never log the same answer (or a tail of it) twice in a row.
                            if full_out and len(full_out) >= _REPEAT_MIN and self._last_out_logged:
                                if full_out in self._last_out_logged:
                                    full_out = ""
                            if full_out:
                                self._last_out_logged = full_out
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    self._bcast({
                                        "type": "log", "speaker": "farfix",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    })
                            out_buf = []

                            # Lo scambio è concluso: finisce nel registro
                            # strutturato con i tempi e le azioni del turno.
                            self._ilog.exchange(full_in, full_out,
                                                model=self._model_name)

                            if self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        # Run the calls BESIDE this loop, never inside it. While
                        # a tool ran here nothing read the socket: audio (3.8
                        # keeps talking during non-blocking tools), resumption
                        # handles and GoAway piled up, websockets stopped
                        # answering keepalive pings, and after ~20 s the server
                        # closed the connection — mid-tool, with the answer lost.
                        calls = list(response.tool_call.function_calls or [])
                        if calls:
                            t = asyncio.create_task(self._run_tool_calls(session, calls))
                            self._tool_tasks.add(t)
                            t.add_done_callback(self._tool_tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[FARFIX] ❌ Recv: {e}")
            raise

    async def _run_tool_calls(self, session, calls) -> None:
        """Execute one tool_call message and answer it on the session it came from."""
        try:
            for fc in calls:
                print(f"[FARFIX] 📞 {fc.name}")
            # Independent calls in one message run in parallel.
            results = await asyncio.gather(
                *(self._execute_tool(fc) for fc in calls), return_exceptions=True)
            fn_responses = []
            for fc, fr in zip(calls, results):
                if getattr(fc, "id", None) in self._cancelled_calls:
                    # Answering a withdrawn call can make the server close the
                    # socket, and the user has moved on anyway.
                    self._cancelled_calls.discard(fc.id)
                    continue
                if isinstance(fr, BaseException):
                    fr = types.FunctionResponse(
                        id=fc.id, name=fc.name,
                        response={"result": f"Tool '{fc.name}' failed: {fr}"})
                fn_responses.append(fr)
            if not fn_responses or session is not self.session:
                return    # nothing left to answer, or the session was replaced
            await session.send_tool_response(function_responses=fn_responses)
            await self._flush_pending_vision()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A failed send means the socket is gone; the run loop notices and
            # reconnects. This task must never take the session down itself.
            print(f"[FARFIX] ⚠️ Tool response not delivered: {e}")
        finally:
            if not self.ui.muted and not self._is_speaking and not self._tool_tasks - {asyncio.current_task()}:
                self.ui.set_state("LISTENING")

    async def _play_audio(self):
        print("[FARFIX] 🔊 Play started")

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[FARFIX] 🔊 Output device: {_spk_name}")

        ring = self._ring
        ring.clear()

        def _cb(outdata, frames, time_info, status):
            ring.pull_into(outdata)

        def _open_spk(dev, latency):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=0,          # let the host pick its natural period
                latency=latency,
                device=dev,
                callback=_cb,
            )
            st.start()
            return st

        def _open_best():
            last = None
            for dev in ((_spk_dev, None) if _spk_dev is not None else (None,)):
                for lat in (OUT_LATENCY_S, "high"):
                    try:
                        return _open_spk(dev, lat)
                    except Exception as _e:
                        last = _e
                if dev is not None:
                    # A chosen output that the host API refuses to open must
                    # not cost the user their voice. Fall back and say so.
                    print(f"[FARFIX] ⚠️  Output device '{_spk_name}' failed: {last} — using default")
                    self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            raise last

        stream = await asyncio.get_running_loop().run_in_executor(self._audio_pool, _open_best)
        ring.last_pull = time.monotonic()

        # With a callback stream the reported latency is the real distance to
        # the speaker. It sizes the echo tail and anchors the mouth.
        try:
            lat = float(getattr(stream, "latency", 0.0) or 0.0)
            if 0.0 < lat < 1.0:
                self._out_latency = lat
            print(f"[FARFIX] 🔊 Output latency {self._out_latency*1000:.0f} ms "
                  f"→ echo tail {(self._out_latency + _TAIL_MARGIN)*1000:.0f} ms")
        except Exception:
            pass

        last_nonempty = time.monotonic()
        try:
            while True:
                # ── Speaker watchdog ─────────────────────────────────────────
                if (not stream.active
                        or time.monotonic() - ring.last_pull > SPK_STALL_S):
                    print("[FARFIX] ⚠️  Speaker stream stalled — reopening")
                    try:
                        stream.abort(ignore_errors=True)
                        stream.close(ignore_errors=True)
                    except Exception:
                        pass
                    try:
                        stream = await asyncio.get_running_loop().run_in_executor(self._audio_pool, _open_best)
                        ring.last_pull = time.monotonic()
                    except Exception as _e:
                        print(f"[FARFIX] ❌ Speaker reopen failed: {_e}")
                        ring.clear()
                        await asyncio.sleep(1.0)
                        continue

                # Keep only a little audio in front of the speaker: enough to
                # ride out scheduling jitter, little enough that an interrupt
                # (which clears it) is heard as instant silence.
                if ring.seconds() > PLAYBACK_AHEAD_S:
                    last_nonempty = time.monotonic()
                    await asyncio.sleep(0.02)
                    continue

                try:
                    chunk = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    if len(ring):
                        last_nonempty = time.monotonic()
                        continue
                    if not self._is_speaking:
                        continue
                    idle = time.monotonic() - last_nonempty
                    turn_done = bool(self._turn_done_event and self._turn_done_event.is_set())
                    # Normal end: the turn is complete and everything is played.
                    # Safety end: nothing to play for SPEAK_IDLE_S even without a
                    # turn_complete (a lost message, a tool pause, a reconnect).
                    # Without it a missing turn_complete kept "speaking" true
                    # forever, and the mic — gated on it — never opened again.
                    if (turn_done and idle > 0.05) or idle > SPEAK_IDLE_S:
                        self.set_speaking(False)
                        if self._turn_done_event:
                            self._turn_done_event.clear()
                    continue

                if self._interrupted:
                    continue          # stale audio of a cancelled answer

                self.set_speaking(True)

                batch = bytearray(chunk)
                while len(batch) < 4800:   # ≈ 100 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform and the avatar's mouth from FARFIX's
                # own voice: a schedule of 20 ms viseme frames anchored at the
                # moment this batch will actually be heard — behind whatever is
                # already queued in the ring, plus the device latency.
                try:
                    pcm = np.frombuffer(bytes(batch), dtype=np.int16)
                    hop = _VIS_HOP / RECEIVE_SAMPLE_RATE
                    frames = _pcm_visemes(pcm, sr=RECEIVE_SAMPLE_RATE)
                    at = time.time() + ring.seconds() + min(self._out_latency, 0.25)
                    self._play_cursor = at + pcm.size / RECEIVE_SAMPLE_RATE
                    if frames:
                        frames = self._visemes.frames(frames, hop)
                        self.ui.push_visemes(frames, hop, at)
                        # Barge-in needs to know what we are playing, not just
                        # how loud: the guard subtracts this from the microphone.
                        self._out_level = max(f[0] for f in frames)
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE,
                                               self._out_level)
                    else:
                        lvl = _pcm_level(pcm)
                        self.ui.set_audio_level(lvl)
                        self._out_level = lvl
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE, lvl)
                except Exception:
                    pass

                ring.push(bytes(batch))
                last_nonempty = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[FARFIX] ❌ Play: {e}")
            raise
        finally:
            ring.clear()
            self.set_speaking(False)
            try:
                stream.abort(ignore_errors=True)
                stream.close(ignore_errors=True)
            except Exception:
                pass

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_running_loop()  # get_event_loop() is deprecated in 3.10+ inside coroutines
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        # The briefing fires before the user has said anything, so the
        # remembered language is the only signal there is. It is a starting
        # point, not a setting: the moment they reply, their language wins.
        lang_clause = (f" Speak this greeting in {lang}, then follow the "
                       f"user's own language from their first reply onward."
                       if lang else "")
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self._send_text(p1)
        print("[FARFIX] Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = (f" Speak in {lang} unless the user has since "
                            f"spoken another language, in which case use theirs."
                            if lang else "")

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self._send_text(p2)
                print("[FARFIX] Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                print(f"[FARFIX] Briefing phase 2 failed: {e}")
                self.ui.write_log("SYS: Could not fetch the news for the briefing.")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import gemini
            summary = await asyncio.to_thread(
                gemini.text, prompt, gemini.SMART, None, 30_000,
            )
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session or not self._awake:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self._send_text(alert)
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session and self._awake:
                # Don't interrupt if user spoke recently or FARFIX is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self._send_text(msg)
                            print("[FARFIX] Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session or not self._awake:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self._send_text(prompt)
                print("[FARFIX] Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                self._enqueue_out(chunk)

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    # A remote command is deliberate control and the phone user
                    # has no desktop WAKE button — so it wakes FARFIX if asleep.
                    if self._wake_enabled and not self._awake:
                        self.wake(reason="remote command")
                    await self._send_text(text)
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_running_loop()
        # Reconnecting resolves the server name through the loop's DEFAULT
        # executor. That pool is also where background checks and the morning
        # news run; with only cpu+4 threads, a few hung ones were enough to make
        # every reconnect wait forever for DNS. Give it room.
        self._loop.set_default_executor(
            ThreadPoolExecutor(max_workers=32, thread_name_prefix="bg"))
        self._reconnect_event = asyncio.Event()

        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
        )
        set_trim_notifier(self.ui.write_log)

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.
        audio_devices.prefetch()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            # Import + construction off the loop: the import may fetch a file
            # and the constructor resolves the machine's own name, and either
            # can stall on a bad network before the conversation even starts.
            def _make_dashboard():
                from dashboard.server import DashboardServer
                return DashboardServer()
            self._dashboard = await asyncio.wait_for(
                asyncio.to_thread(_make_dashboard), timeout=15)
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            connected_at = None
            try:
                _resumed_with = self._resume_handle is not None
                seamless = _resumed_with and not self._first_connect and self._quiet_reconnect
                if not seamless:
                    print("[FARFIX] Connecting...")
                    self.ui.set_state("THINKING")
                self._model_name = _live_model()
                config = self._build_config()

                gemini_key = _get_api_key()
                if not gemini_key:
                    print("[FARFIX] Gemini Live key assente: voce Live disattivata. "
                          "Grok/OpenAI restano disponibili per i task testuali.")
                    self.ui.set_state("LISTENING")
                    await asyncio.sleep(30)
                    continue
                # Fresh client on every reconnect — avoids stale HTTP session state.
                # v1alpha carries proactive audio; if it gets rejected we fall
                # back to v1beta.
                client = genai.Client(
                    api_key=gemini_key,
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                # A setup that never completes (half-open network) used to hang
                # here forever; nothing retried because nothing failed.
                _cm = client.aio.live.connect(model=self._model_name, config=config)
                _session = await asyncio.wait_for(_cm.__aenter__(), CONNECT_TIMEOUT_S)
                async with (
                    contextlib.AsyncExitStack() as _stack,
                    asyncio.TaskGroup() as tg,
                ):
                    _stack.push_async_exit(_cm.__aexit__)
                    session = _session
                    connected_at = time.monotonic()
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=OUT_QUEUE_MAX)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a
                    # previous connection. `_is_speaking` in particular: if a
                    # reconnect happened mid-answer it stayed True, and since
                    # the mic is only streamed while it is False, the new
                    # session could never hear anything again.
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False
                    self._gen_active           = False
                    self._goaway_deadline      = None
                    self._cancelled_calls.clear()
                    self._barge_run = 0
                    self._barge_buf.clear()
                    self._ring.clear()
                    self._visemes.reset()
                    self.set_speaking(False)
                    self._tail_until = 0.0

                    print(f"[FARFIX] Connected ({self._model_name}).")
                    self._ilog.start_session(self._model_name)
                    if self._ilog.enabled:
                        # Detto apertamente: l'utente deve sapere che gli
                        # scambi finiscono su disco, e dove.
                        print("[Log] Scambi registrati in memory/interactions/ "
                              "— statistiche: python -m core.interaction_log stats")
                    if _resumed_with and not seamless:
                        self.ui.write_log("SYS: Reconnected — conversation restored.")

                    # Wake word: come up ASLEEP only on a genuinely new start.
                    # It used to go back to sleep on every reconnect — and the
                    # server recycles the connection about every 10 minutes, so
                    # it fell asleep in the middle of conversations.
                    if self._wake_enabled:
                        self._ensure_wake_detector()
                        if not _resumed_with or self._first_connect:
                            self._awake = False
                            self.ui.set_state("SLEEPING")
                            self.ui.write_log(f"SYS: {self._asst_name} online — sleeping. Say '{WAKE_PHRASE}' to wake me.")
                        else:
                            self.ui.set_state("LISTENING" if self._awake else "SLEEPING")
                    else:
                        self._awake = True
                        if not self.ui.muted:
                            self.ui.set_state("LISTENING")
                        if not seamless:
                            self.ui.write_log("SYS: FARFIX online.")
                    self._first_connect   = False
                    self._quiet_reconnect = False

                    self._bcast({"type": "status", "state": "active"})

                    self._reconnect_event.clear()  # ignore requests from before this session
                    tg.create_task(self._watch_reconnect())
                    tg.create_task(self._watch_goaway())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._run_sleep_watch())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled).
                    # Skipped in wake-word mode: it comes up asleep, and a briefing
                    # would mean talking while "asleep".
                    if not self._briefing_sent and get_brief_enabled() and self._awake:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (TaskGroup wraps
                # child failures in a group, which `except Exception` misses).
                if _is_reconnect_signal(e):
                    # Voluntary reconnect (settings, GoAway) — not an error.
                    # Rebuild immediately, no backoff, no scary logs.
                    if not _keep_context_of(e):
                        self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                err_str = str(e)
                err_low = err_str.lower()
                lived = (time.monotonic() - connected_at) if connected_at else 0.0

                # A resumption handle the server will not accept — expired, or
                # belonging to a session it has since dropped. Drop it once and
                # start clean, or the same dead handle is replayed forever.
                if _resumed_with and lived < 3.0 and (
                    "resum" in err_low or "handle" in err_low
                    or "invalid_argument" in err_low or "invalid argument" in err_low
                    or "not_found" in err_low
                ):
                    print("[FARFIX] 🔗 Resumption handle rejected — starting a fresh session")
                    self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                    self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                print(f"[FARFIX] Error ({type(e).__name__}): {e}")
                if connected_at is None:
                    traceback.print_exc()

                # A connection that worked for a while and then dropped is not a
                # configuration problem — it is the network or the server
                # recycling the socket. Resume straight away.
                if isinstance(e, asyncio.TimeoutError) or (
                        isinstance(e, BaseExceptionGroup)
                        and any(isinstance(x, asyncio.TimeoutError) for x in e.exceptions)):
                    lived = 0.0 if connected_at is None else lived
                if lived > 5.0:
                    self._conn_backoff = 0.3 if self._resume_handle else 1.0
                    self._quiet_reconnect = True
                    print("[FARFIX] Connection dropped — resuming")
                else:
                    setup_rejected = connected_at is None or lived < 3.0
                    bad_arg = ("invalid_argument" in err_low or "invalid argument" in err_low
                               or "unknown name" in err_low or "unexpected keyword" in err_low
                               or "1007" in err_str)

                    # Preview fields rejected at setup (API drift) — drop the
                    # newest, cheapest ones first, then proactive audio.
                    if setup_rejected and bad_arg and self._tuned_live:
                        self._tuned_live = False
                        print("[FARFIX] Live tuning rejected — reconnecting without it.")
                        self._conn_backoff = 0
                        continue
                    if setup_rejected and bad_arg and self._enhanced_live:
                        self._enhanced_live = False
                        self.ui.write_log(
                            "SYS: Proactive audio unavailable — reconnecting without it.")
                        self._conn_backoff = 0
                        continue

                    # Only a real authentication error asks for a new key. A bare
                    # 1007 means "invalid argument", and treating it as a bad key
                    # left the app parked on the setup screen, deaf, for good.
                    if ("api key not valid" in err_low or "api_key_invalid" in err_low
                            or "permission_denied" in err_low or "unauthenticated" in err_low):
                        self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                        self.ui.set_state("SLEEPING")
                        self.ui.prompt_reconfig()
                        while not self.ui._win._ready:
                            await asyncio.sleep(1)
                        print("[FARFIX] New API key saved — reconnecting...")
                        self._conn_backoff = 0
                        continue

                    prev = getattr(self, "_conn_backoff", 0) or 1.0
                    quota = ("resource_exhausted" in err_low or "quota" in err_low
                             or "429" in err_str or "too many" in err_low)
                    self._conn_backoff = 60 if quota else min(max(prev, 1.0) * 2, 30)
                    reason = ("limite di utilizzo / sessioni Gemini raggiunto"
                              if quota else ((err_str.strip().splitlines() or [type(e).__name__])[0][:90]))
                    self.ui.write_log(
                        f"NET: Connection failed ({reason}) — retrying in "
                        f"{self._conn_backoff:.0f}s."
                    )
            finally:
                self.session = None
                # Stop tool calls that belonged to the dead connection.
                for t in list(self._tool_tasks):
                    t.cancel()
                self._tool_tasks.clear()
                # Only save if there was a real conversation (≥3 turns). Runs
                # in the background — it never delays the reconnect.
                # A routine reconnect continues the same conversation, and each
                # summary opens a side session on the same key — right when the
                # main one is reconnecting. So: when the context is really lost,
                # or at most every SUMMARY_EVERY_S.
                if len(self._session_log) >= 3 and (
                        self._resume_handle is None
                        or time.monotonic() - self._last_summary > SUMMARY_EVERY_S):
                    self._last_summary = time.monotonic()
                    asyncio.create_task(self._save_session_summary())

            self.set_speaking(False)
            delay = getattr(self, "_conn_backoff", 1.0)
            if delay >= 1.0:
                self.ui.set_state("SLEEPING")
                self._bcast({"type": "status", "state": "sleeping"})
            print(f"[FARFIX] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    # Carry an existing install over from the old name (auto-start entry,
    # certificates, browser profiles, saved assistant name). No-op otherwise.
    from core.legacy_names import migrate
    migrate()
    ui = FarfixUI("face.png")

    def runner():
        ui.wait_for_api_key()
        farfix = FarfixLive(ui)
        # If anything ever escaped run(), this thread used to end quietly: the
        # window stayed open and the assistant never answered again. Now the
        # loop is restarted, and the failure is shown instead of swallowed.
        while True:
            try:
                asyncio.run(farfix.run())
            except KeyboardInterrupt:
                print("\n🔴 Shutting down...")
                return
            except BaseException as e:           # noqa: BLE001
                traceback.print_exc()
                try:
                    ui.write_log(f"ERR: voice engine restarted after an error: {str(e)[:100]}")
                except Exception:
                    pass
                farfix.session = None
                time.sleep(2)

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()