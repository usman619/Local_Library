import os
import re
import sys
import ssl
import time
import shutil
import asyncio
import tempfile
import threading
import subprocess
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, Future
from PySide6.QtCore import QThread, Signal

# Ensure Conda on Linux doesn't fail HTTPS certificate checks when contacting Edge-TTS
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    if os.path.exists("/etc/ssl/certs/ca-certificates.crt"):
        os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")


def _ensure_conda_bin_in_path():
    """
    When launched from a Linux desktop icon (.desktop), the shell's Conda PATH is not loaded.
    Prepend the active Python interpreter's bin/ directory and ~/.local/bin to PATH
    so shutil.which() finds edge-tts, ffmpeg, ffplay, and mpv.
    """
    py_bin = str(Path(sys.executable).resolve().parent)
    local_bin = str(Path.home() / ".local" / "bin")
    cur_path = os.environ.get("PATH", "")
    parts = cur_path.split(os.pathsep) if cur_path else []
    changed = False
    for d in (py_bin, local_bin):
        if d and os.path.isdir(d) and d not in parts:
            parts.insert(0, d)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


_ensure_conda_bin_in_path()


def _clean_system_env() -> dict:
    """
    Returns a safe copy of os.environ for CLI subprocesses (like edge-tts) that:
    - Marks the process as a worker so main.py's bootstrapper never spawns a second GUI window
    - Preserves the active Python/Conda bin directory in PATH
    """
    _ensure_conda_bin_in_path()
    env = os.environ.copy()
    env["_LOCAL_LIBRARY_BOOTSTRAPPED"] = "1"
    env["_LOCAL_LIBRARY_WORKER"] = "1"
    return env


def _get_audio_player_env(player_executable: str) -> dict:
    """
    Returns a clean environment for external audio players (mpv, ffplay, gst-play-1.0, cvlc, paplay).
    If the player is a system binary in /usr/bin or /bin, strips Conda's LD_PRELOAD and
    LD_LIBRARY_PATH so system audio binaries don't fail with C++ runtime symbol conflicts.
    """
    env = _clean_system_env()
    resolved_bin = shutil.which(player_executable) or player_executable
    conda_prefix = os.environ.get("CONDA_PREFIX") or str(
        Path(sys.executable).resolve().parent.parent
    )
    # If the audio player is a system binary outside the Conda environment, strip Conda's preload
    if resolved_bin and not str(resolved_bin).startswith(str(conda_prefix)):
        env.pop("LD_PRELOAD", None)
        env.pop("LD_LIBRARY_PATH", None)
    return env


# _GLOBAL_QWEN_MODEL = None
_GLOBAL_KOKORO_PIPELINE = None
_GLOBAL_MODEL_LOCK = threading.Lock()


# def _get_qwen_model(status_callback=None):
#     """
#     Thread-safe singleton loader for Qwen3-TTS 0.6B CustomVoice configured for GPU ONLY (cuda:0).
#     Directly targets GPU compute as requested.
#     """
#     global _GLOBAL_QWEN_MODEL
#     with _GLOBAL_MODEL_LOCK:
#         if _GLOBAL_QWEN_MODEL is not None:
#             return _GLOBAL_QWEN_MODEL
#
#         import torch
#         from qwen_tts import Qwen3TTSModel
#
#         if not torch.cuda.is_available():
#             raise RuntimeError("CUDA is not available on this system. Qwen3-TTS is configured for GPU ONLY.")
#
#         os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
#
#         # Determine precision: bfloat16 for Ampere+ (SM 80+), float16 for Pascal/Turing (SM 60-75)
#         cap = torch.cuda.get_device_capability(0)
#         dtype = torch.bfloat16 if cap[0] >= 8 else torch.float16
#
#         if status_callback:
#             gpu_name = torch.cuda.get_device_name(0)
#             status_callback(f"⏳ Initializing Qwen3-TTS on GPU ({gpu_name})...")
#
#         _GLOBAL_QWEN_MODEL = Qwen3TTSModel.from_pretrained(
#             "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
#             device_map="cuda:0",
#             dtype=dtype,
#             attn_implementation="sdpa",
#         )
#         return _GLOBAL_QWEN_MODEL


def _get_kokoro_pipeline(lang_code: str):
    """Thread-safe singleton loader for Kokoro TTS pipeline (runs on GPU with ~318 MB VRAM)."""
    global _GLOBAL_KOKORO_PIPELINE
    with _GLOBAL_MODEL_LOCK:
        if _GLOBAL_KOKORO_PIPELINE is None or getattr(_GLOBAL_KOKORO_PIPELINE, "lang_code", "") != lang_code:
            import torch
            from kokoro import KPipeline
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            _GLOBAL_KOKORO_PIPELINE = KPipeline(lang_code=lang_code, device=device)
        return _GLOBAL_KOKORO_PIPELINE


@dataclass
class TTSUnit:
    page_idx: int
    para_idx: int
    sent_idx: int
    text: str
    bbox: tuple[float, float, float, float] | None = None


_ABBREV_PROTECT_RE = re.compile(
    r"\b(Fig|Figs|Tab|Eq|Dr|Mr|Mrs|Ms|Prof|St|No|Vol|Ch|Sec|Art|Ref|vs|etc|al|Jr|Sr|Inc|Ltd|Co|p|pp)\.",
    re.IGNORECASE,
)
_EG_IE_PROTECT_RE = re.compile(r"\b(e\.g|i\.e|c\.f|ph\.d)\.", re.IGNORECASE)
_DECIMAL_PROTECT_RE = re.compile(r"(\d)\.(\d)")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'(\[])")
_DOT_TOKEN = "\x00DOT\x00"


def _split_into_natural_sentences(paragraph: str) -> list[str]:
    """
    Splits a paragraph into natural sentences WITHOUT altering characters
    so PyMuPDF page.search_for() can always locate and highlight the exact text!
    """
    text = " ".join(paragraph.split()).strip()
    if not text:
        return []

    protected = _ABBREV_PROTECT_RE.sub(lambda m: m.group(1) + _DOT_TOKEN, text)
    protected = _EG_IE_PROTECT_RE.sub(
        lambda m: m.group(0).replace(".", _DOT_TOKEN), protected
    )
    protected = _DECIMAL_PROTECT_RE.sub(rf"\1{_DOT_TOKEN}\2", protected)

    raw_parts = _SENT_SPLIT_RE.split(protected)
    sentences: list[str] = []

    for part in raw_parts:
        restored = part.replace(_DOT_TOKEN, ".").strip()
        if restored:
            sentences.append(restored)

    return sentences if sentences else [text]


# Matches all PDF/EPUB line-wrap hyphen characters:
# ASCII (-), Soft Hyphen (\u00ad), Unicode Hyphen (\u2010), Non-Breaking Hyphen (\u2011),
# Figure Dash (\u2012), Small/Fullwidth Hyphens (\ufe63, \uff0d)
_HYPHEN_CHARS = r"\-\u00ad\u2010\u2011\u2012\ufe63\uff0d"
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")

# 1. Line-break hyphenation (while \n is still present): "summa-\nrizes" -> "summarizes", "Janu-\nary" -> "January"
_LINEBREAK_HYPHEN_RE = re.compile(
    rf"([A-Za-z])[ \t]*[{_HYPHEN_CHARS}][ \t]*\r?\n[ \t]*([a-z])"
)

# 2. Flattened space hyphenation: "summa- rizes" or "Janu\u2010 ary" -> "summarizes", "January"
_SPACE_HYPHEN_RE = re.compile(
    rf"([A-Za-z])[ \t]*[{_HYPHEN_CHARS}]\s+([a-z])"
)


def _dehyphenate_raw_block(raw_text: str) -> str:
    """
    Rejoins words split across PDF/EPUB line breaks (e.g. 'summa-\\nrizes' -> 'summarizes',
    'Janu-\\nary' -> 'January') BEFORE newlines are flattened into spaces, while preserving
    true mid-line compound words like 'follow-up'.
    """
    text = _ZERO_WIDTH_RE.sub("", raw_text)
    # Rejoin hyphen + newline + lowercase continuation
    text = _LINEBREAK_HYPHEN_RE.sub(r"\1\2", text)
    # Strip any remaining invisible soft hyphens (\u00ad) inside words
    text = text.replace("\u00ad", "")
    # Catch cases where a newline was already turned into a space after a hyphen
    text = _SPACE_HYPHEN_RE.sub(r"\1\2", text)
    return text

def build_page_units(
    page_idx: int,
    blocks: list,
    skip_headers_footers: bool = True,
) -> list[TTSUnit]:
    """Extracts reading-order sentence units from PyMuPDF page blocks."""
    from app.core.ocr_engine import HybridOCREngine

    units: list[TTSUnit] = []
    para_counter = 0

    for b in blocks:
        if len(b) < 5:
            continue
        if len(b) >= 7 and b[6] != 0:
            continue

        x0, y0, x1, y1, raw_text = b[0], b[1], b[2], b[3], b[4]
        if not isinstance(raw_text, str):
            continue

        cleaned_block = " ".join(raw_text.split()).strip()

        if not cleaned_block:
            continue
        if len(cleaned_block) < 2 and not cleaned_block.isalnum():
            continue

        if skip_headers_footers:
            if cleaned_block.isdigit() and len(cleaned_block) <= 5:
                continue
            if HybridOCREngine.is_page_number_text(cleaned_block):
                continue
            if HybridOCREngine.is_chapter_or_header_text(cleaned_block):
                continue

        sentences = _split_into_natural_sentences(cleaned_block)
        if not sentences:
            continue

        bbox = (float(x0), float(y0), float(x1), float(y1))
        for s_idx, sent in enumerate(sentences):
            units.append(
                TTSUnit(
                    page_idx=page_idx,
                    para_idx=para_counter,
                    sent_idx=s_idx,
                    text=sent,
                    bbox=bbox,
                )
            )
        para_counter += 1

    return units


def extract_page_tts_units(
    doc,
    page_idx: int,
    filepath: str = "",
    allow_ocr: bool = True,
    skip_headers_footers: bool = True,
) -> tuple[list[TTSUnit], bool]:
    """
    Extracts TTSUnits for page_idx using HybridOCREngine:
    - Strips PDF headers/footers, chapter titles, & standalone page numbers when skip_headers_footers=True
    - Applies C-level de-hyphenation & multi-column reading order sort
    - Automatically falls back to disk-cached Tesseract OCR on scanned pages
    Returns: (units, used_ocr)
    """
    from app.core.ocr_engine import HybridOCREngine

    page = doc.load_page(page_idx)
    blocks, used_ocr = HybridOCREngine.extract_clean_blocks(
        page,
        page_idx,
        filepath=filepath,
        allow_ocr=allow_ocr,
        skip_headers_footers=skip_headers_footers,
    )
    units = build_page_units(
        page_idx, blocks, skip_headers_footers=skip_headers_footers
    )
    return units, used_ocr


class NeuralTTSWorker(QThread):
    """
    Neural TTS Worker with look-ahead prefetching, responsive Play/Pause/Stop controls,
    and multi-backend Linux audio playback.
    """

    status_changed = Signal(str)
    unit_started = Signal(int, int, int, str)  # (page_idx, para_idx, sent_idx, text)
    playback_state_changed = Signal(str)       # "playing", "paused", "stopped"

    PREFETCH_AHEAD = 2

    def __init__(
        self,
        units: list[TTSUnit],
        start_index: int = 0,
        voice_spec: str = "edge:en-US-AriaNeural (US — Aria Crisp Female)",
        speed: float = 1.0,
        pitch: int = 0,
        *args,
        **kwargs,
    ):
        super().__init__()
        self.units = units
        self.current_index = max(0, start_index)
        self.voice_spec = voice_spec
        self.speed = float(speed)
        self.pitch = int(pitch)

        self.is_paused = False
        self._stop_requested = False
        self._param_version = 0
        self._seek_target: int | None = None
        self._active_proc: subprocess.Popen | None = None
        self._temp_dir = tempfile.mkdtemp(prefix="locallibrary_tts_")
        self._last_error: str = ""

    @staticmethod
    def _parse_voice_spec(spec: str) -> tuple[str, str]:
        raw = spec.split(" (")[0].strip()
        if ":" in raw:
            engine, v_id = raw.split(":", 1)
            return engine.lower(), v_id.strip()
        return "edge", raw

    def update_live_settings(
        self,
        voice_spec: str | None = None,
        speed: float | None = None,
        pitch: int | None = None,
    ):
        changed = False
        if voice_spec is not None and voice_spec != self.voice_spec:
            self.voice_spec = voice_spec
            changed = True
        if speed is not None and abs(float(speed) - self.speed) > 1e-3:
            self.speed = float(speed)
            changed = True
        if pitch is not None and int(pitch) != self.pitch:
            self.pitch = int(pitch)
            changed = True
        if changed:
            self._param_version += 1
            # Kill current playback immediately so the new voice takes effect on this sentence!
            self._kill_active_audio()

    # Backward-compatible aliases in case ReaderView calls set_speed / set_pitch
    def set_speed(self, speed: float):
        self.update_live_settings(speed=speed)

    def set_pitch(self, pitch: int):
        self.update_live_settings(pitch=pitch)

    def pause(self):
        self.is_paused = True
        self._kill_active_audio()
        self.playback_state_changed.emit("paused")
        self.status_changed.emit("TTS Paused — Click Play to resume from this sentence")

    def resume(self):
        if self.is_paused:
            self.is_paused = False
            self.playback_state_changed.emit("playing")

    def stop(self):
        self._stop_requested = True
        self.is_paused = False
        self._kill_active_audio()
    
    def seek(self, target_index: int):
        """Immediately interrupts current audio and jumps to target_index."""
        if not self.units:
            return
        clamped = max(0, min(len(self.units) - 1, int(target_index)))
        self._seek_target = clamped
        self.current_index = clamped
        self._param_version += 1
        if self.is_paused:
            self.is_paused = False
            self.playback_state_changed.emit("playing")
        self._kill_active_audio()

    def next_sentence(self):
        """Skips immediately to the next sentence."""
        self.seek(self.current_index + 1)

    def prev_sentence(self):
        """Skips immediately to the previous sentence."""
        self.seek(self.current_index - 1)

    def _kill_active_audio(self):
        proc = self._active_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._active_proc = None
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def _synthesize_unit_to_file(
        self, idx: int, text: str, voice_spec: str, speed: float, pitch: int, version: int
    ) -> str | None:
        if self._stop_requested or version != self._param_version:
            return None
        
        # Only modify spoken_text for TTS audio; `unit.text` stays intact for PDF highlighting!
        spoken_text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
        spoken_text = re.sub(
            r"([A-Za-z])[ \t]*[\-\u00ad\u2010\u2011\u2012\u2013\ufe63\uff0d]\s+([a-z])",
            r"\1\2",
            spoken_text,
        )
        spoken_text = spoken_text.replace("\u00ad", "")
        spoken_text = re.sub(r"\s*—\s*", " — ", spoken_text)
        spoken_text = " ".join(spoken_text.split()).strip()
        if not spoken_text:
            return None

        engine, voice_id = self._parse_voice_spec(voice_spec)
        if engine == "qwen":
            wav_file = os.path.join(self._temp_dir, f"unit_{idx}_v{version}_{voice_id}.wav")
            if os.path.exists(wav_file) and os.path.getsize(wav_file) > 256:
                return wav_file
            if self._synthesize_qwen(spoken_text, voice_id, speed, wav_file):
                return wav_file
            return None

        if engine == "kokoro":
            wav_file = os.path.join(self._temp_dir, f"unit_{idx}_v{version}_{voice_id}.wav")
            if os.path.exists(wav_file) and os.path.getsize(wav_file) > 256:
                return wav_file
            if self._synthesize_kokoro(spoken_text, voice_id, speed, wav_file):
                return wav_file
            return None

        mp3_file = os.path.join(self._temp_dir, f"unit_{idx}_v{version}_{voice_id}.mp3")
        if os.path.exists(mp3_file) and os.path.getsize(mp3_file) > 256:
            return mp3_file

        res = self._synthesize_edge(spoken_text, voice_id, speed, pitch, mp3_file)
        if res:
            return res

        # Automatic fallback to standard AriaNeural if a Multilingual voice fails
        if voice_id != "en-US-AriaNeural":
            return self._synthesize_edge(
                spoken_text, "en-US-AriaNeural", speed, pitch, mp3_file
            )
        return None

    def _synthesize_edge(
        self, text: str, voice_id: str, speed: float, pitch: int, out_path: str
    ) -> str | None:
        try:
            import edge_tts

            rate_pct = int(round((speed - 1.0) * 100))
            rate_str = f"{rate_pct:+d}%"
            pitch_hz = int(round(pitch * 2.5))
            pitch_str = f"{pitch_hz:+d}Hz"

            async def _run():
                comm = edge_tts.Communicate(
                    text, voice=voice_id, rate=rate_str, pitch=pitch_str
                )
                await comm.save(out_path)

            asyncio.run(_run())

            if os.path.exists(out_path) and os.path.getsize(out_path) > 128:
                return out_path
        except Exception as e:
            self._last_error = f"Edge-TTS error: {e}"

        # CLI fallback if edge-tts Python async loop had an SSL/aiohttp issue
        _ensure_conda_bin_in_path()
        edge_cli = shutil.which("edge-tts")
        if edge_cli:
            try:
                rate_pct = int(round((speed - 1.0) * 100))
                pitch_hz = int(round(pitch * 2.5))
                cmd = [
                    edge_cli,
                    "--voice", voice_id,
                    f"--rate={rate_pct:+d}%",
                    f"--pitch={pitch_hz:+d}Hz",
                    "--text", text,
                    "--write-media", out_path,
                ]
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=12,
                    env=_clean_system_env(),  # <-- Safe worker environment
                )
                if os.path.exists(out_path) and os.path.getsize(out_path) > 128:
                    return out_path
            except Exception as e:
                self._last_error = f"edge-tts CLI error: {e}"

        return None

    def _synthesize_kokoro(
        self, text: str, voice_id: str, speed: float, out_wav: str
    ) -> bool:
        try:
            import soundfile as sf
            import numpy as np

            lang_code = "b" if voice_id.startswith("b") else "a"
            pipeline = _get_kokoro_pipeline(lang_code)

            audio_segments = []
            with _GLOBAL_MODEL_LOCK:
                for _gs, _ps, audio in pipeline(
                    text, voice=voice_id, speed=speed
                ):
                    if audio is not None:
                        audio_segments.append(audio)
            if audio_segments:
                full_audio = np.concatenate(audio_segments)
                sf.write(out_wav, full_audio, 24000)
                return True
        except Exception as e:
            self._last_error = f"Kokoro error: {e}"
        return False

    def _play_audio_file(self, filepath: str):
        """
        Plays MP3/WAV audio using the best available Linux audio tool:
        1. mpv / ffplay / gst-play-1.0 / mpg123 / cvlc
        2. ffmpeg -> paplay / aplay pipe
        3. Python soundfile + sounddevice
        """
        if not filepath or not os.path.exists(filepath) or self._stop_requested:
            return

        _ensure_conda_bin_in_path()

        cmd = None
        if shutil.which("mpv"):
            cmd = [shutil.which("mpv"), "--no-video", "--really-quiet", filepath]
        elif shutil.which("ffplay"):
            cmd = [shutil.which("ffplay"), "-nodisp", "-autoexit", "-loglevel", "quiet", filepath]
        elif shutil.which("gst-play-1.0"):
            cmd = [shutil.which("gst-play-1.0"), "--quiet", "--no-interactive", filepath]
        elif shutil.which("mpg123") and filepath.endswith(".mp3"):
            cmd = [shutil.which("mpg123"), "-q", filepath]
        elif shutil.which("cvlc"):
            cmd = [shutil.which("cvlc"), "--play-and-exit", "--quiet", filepath]
        elif shutil.which("paplay") and filepath.endswith(".wav"):
            cmd = [shutil.which("paplay"), filepath]
        elif shutil.which("ffmpeg") and (shutil.which("paplay") or shutil.which("aplay")):
            # Convert .mp3 to .wav via ffmpeg and play with paplay/aplay
            ffmpeg_bin = shutil.which("ffmpeg")
            wav_tmp = filepath + ".wav"
            try:
                subprocess.run(
                    [ffmpeg_bin, "-y", "-i", filepath, "-loglevel", "quiet", wav_tmp],
                    timeout=8,
                    env=_get_audio_player_env(ffmpeg_bin),  # <-- Clean audio env
                )
                if os.path.exists(wav_tmp):
                    player = shutil.which("paplay") or shutil.which("aplay")
                    cmd = [player, wav_tmp]
            except Exception:
                pass

        if cmd:
            try:
                self._active_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=_get_audio_player_env(cmd[0]),  # <-- Clean audio env
                )
                while self._active_proc and self._active_proc.poll() is None:
                    if self._stop_requested or self.is_paused:
                        self._kill_active_audio()
                        break
                    time.sleep(0.02)
            except Exception as e:
                self._last_error = f"Audio player error: {e}"
            finally:
                self._active_proc = None
                if os.path.exists(filepath + ".wav"):
                    try:
                        os.remove(filepath + ".wav")
                    except OSError:
                        pass
            return

        # Fallback: Python soundfile + sounddevice
        try:
            import soundfile as sf
            import sounddevice as sd

            data, fs = sf.read(filepath, dtype="float32")
            sd.play(data, fs)
            duration = len(data) / float(fs)
            start_t = time.time()
            while (time.time() - start_t) < duration:
                if self._stop_requested or self.is_paused:
                    sd.stop()
                    break
                time.sleep(0.02)
            sd.wait()
        except Exception as e:
            self._last_error = (
                f"No audio player found ({e}). Install 'ffmpeg' or 'mpv' (sudo apt install mpv ffmpeg)."
            )

    def run(self):
        if not self.units and not getattr(self, "expect_more_units", False):
            self.playback_state_changed.emit("stopped")
            return

        self.playback_state_changed.emit("playing")
        executor = ThreadPoolExecutor(max_workers=2)
        futures: dict[tuple[int, int], Future] = {}
        consecutive_failures = 0

        try:
            idx = self.current_index
            while not self._stop_requested:
                # 1. Check if user clicked Next / Prev sentence button
                if self._seek_target is not None:
                    idx = self._seek_target
                    self._seek_target = None
                    self.current_index = idx

                # Wait for units to become available when the background daemon
                # is still extracting text from upcoming pages.
                if idx >= len(self.units):
                    if not getattr(self, "expect_more_units", False):
                        break
                    while idx >= len(self.units) and not self._stop_requested:
                        if self._seek_target is not None:
                            break
                        if not getattr(self, "expect_more_units", False):
                            break
                        time.sleep(0.05)
                    if self._seek_target is not None:
                        continue
                    if idx >= len(self.units):
                        break

                while self.is_paused and not self._stop_requested:
                    if self._seek_target is not None:
                        break
                    time.sleep(0.04)
                if self._stop_requested:
                    break
                if self._seek_target is not None:
                    continue

                ver = self._param_version

                # Look-ahead prefetch
                for look_idx in range(
                    idx, min(len(self.units), idx + 1 + self.PREFETCH_AHEAD)
                ):
                    key = (look_idx, ver)
                    if key not in futures:
                        u_look = self.units[look_idx]
                        futures[key] = executor.submit(
                            self._synthesize_unit_to_file,
                            look_idx,
                            u_look.text,
                            self.voice_spec,
                            self.speed,
                            self.pitch,
                            ver,
                        )

                unit = self.units[idx]
                cur_key = (idx, ver)
                fut = futures.get(cur_key)

                if fut and not fut.done():
                    self.status_changed.emit(
                        f"⏳ Synthesizing [p. {unit.page_idx + 1}]: “{unit.text[:48]}...”"
                    )

                audio_file = fut.result() if fut else None

                if ver != self._param_version and not self._stop_requested:
                    continue
                if self._stop_requested or self.is_paused:
                    continue
                if self._seek_target is not None:
                    continue

                if audio_file and os.path.exists(audio_file):
                    consecutive_failures = 0
                    self.current_index = idx
                    # Emit unit_started BEFORE playing so the sky-blue sentence highlight renders immediately!
                    self.unit_started.emit(
                        unit.page_idx, unit.para_idx, unit.sent_idx, unit.text
                    )
                    _eng, v_name = self._parse_voice_spec(self.voice_spec)
                    self.status_changed.emit(
                        f"Speaking [{v_name}] [p. {unit.page_idx + 1}]: “{unit.text[:70]}"
                        f"{'...' if len(unit.text) > 70 else ''}”"
                    )
                    self._play_audio_file(audio_file)

                    # Only clean up the audio file if the sentence finished
                    # or was stopped — NOT when paused, so it replays on resume.
                    if not self.is_paused and self._seek_target is None:
                        try:
                            os.remove(audio_file)
                        except OSError:
                            pass

                    # If user clicked Next/Prev during playback, jump immediately to _seek_target!
                    if self._seek_target is not None:
                        idx = self._seek_target
                        self._seek_target = None
                        self.current_index = idx
                        continue

                    # If settings/voice changed during playback, don't advance idx:
                    # immediately replay this sentence with the new voice!
                    if ver != self._param_version:
                        continue

                    if not self.is_paused and not self._stop_requested:
                        idx += 1
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        err_msg = self._last_error or "Check internet connection or edge-tts installation."
                        self.status_changed.emit(f"⚠ TTS Error: {err_msg}")
                        break
                    idx += 1

        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            if not self._stop_requested:
                self.playback_state_changed.emit("stopped")