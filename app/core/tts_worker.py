import os
import re
import ssl
import time
import shutil
import asyncio
import tempfile
import subprocess
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


def build_page_units(page_idx: int, blocks: list) -> list[TTSUnit]:
    """Extracts reading-order sentence units from PyMuPDF page blocks."""
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
        if len(cleaned_block) < 2:
            continue
        if cleaned_block.isdigit() and len(cleaned_block) <= 4:
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
        self._active_proc: subprocess.Popen | None = None
        self._temp_dir = tempfile.mkdtemp(prefix="locallibrary_tts_")
        self._kokoro_pipeline = None
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

    # Backward-compatible aliases in case ReaderView calls set_speed / set_pitch
    def set_speed(self, speed: float):
        self.update_live_settings(speed=speed)

    def set_pitch(self, pitch: int):
        self.update_live_settings(pitch=pitch)

    def pause(self):
        self.is_paused = True
        self._kill_active_audio()
        self.playback_state_changed.emit("paused")
        self.status_changed.emit("⏸ TTS Paused — Click ▶ to resume from this sentence")

    def resume(self):
        if self.is_paused:
            self.is_paused = False
            self.playback_state_changed.emit("playing")

    def stop(self):
        self._stop_requested = True
        self.is_paused = False
        self._kill_active_audio()

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

        # Clean hyphenated line-breaks for speech synthesis only
        spoken_text = re.sub(r"([a-zA-Z])-\s+([a-z])", r"\1\2", text).strip()
        if not spoken_text:
            return None

        engine, voice_id = self._parse_voice_spec(voice_spec)
        if engine == "kokoro":
            wav_file = os.path.join(self._temp_dir, f"unit_{idx}_v{version}.wav")
            if os.path.exists(wav_file) and os.path.getsize(wav_file) > 256:
                return wav_file
            if self._synthesize_kokoro(spoken_text, voice_id, speed, wav_file):
                return wav_file
            voice_id = "en-US-AriaNeural"

        mp3_file = os.path.join(self._temp_dir, f"unit_{idx}_v{version}.mp3")
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
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 128:
                    return out_path
            except Exception as e:
                self._last_error = f"edge-tts CLI error: {e}"

        return None

    def _synthesize_kokoro(
        self, text: str, voice_id: str, speed: float, out_wav: str
    ) -> bool:
        try:
            from kokoro import KPipeline
            import soundfile as sf
            import numpy as np

            if self._kokoro_pipeline is None:
                lang_code = "b" if voice_id.startswith("b") else "a"
                self._kokoro_pipeline = KPipeline(lang_code=lang_code)

            audio_segments = []
            for _gs, _ps, audio in self._kokoro_pipeline(
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

        cmd = None
        if shutil.which("mpv"):
            cmd = ["mpv", "--no-video", "--really-quiet", filepath]
        elif shutil.which("ffplay"):
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath]
        elif shutil.which("gst-play-1.0"):
            cmd = ["gst-play-1.0", "--quiet", "--no-interactive", filepath]
        elif shutil.which("mpg123") and filepath.endswith(".mp3"):
            cmd = ["mpg123", "-q", filepath]
        elif shutil.which("cvlc"):
            cmd = ["cvlc", "--play-and-exit", "--quiet", filepath]
        elif shutil.which("paplay") and filepath.endswith(".wav"):
            cmd = ["paplay", filepath]
        elif shutil.which("ffmpeg") and (shutil.which("paplay") or shutil.which("aplay")):
            # Convert .mp3 to .wav via ffmpeg and play with paplay/aplay
            wav_tmp = filepath + ".wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", filepath, "-loglevel", "quiet", wav_tmp],
                    timeout=8,
                )
                if os.path.exists(wav_tmp):
                    player = "paplay" if shutil.which("paplay") else "aplay"
                    cmd = [player, wav_tmp]
            except Exception:
                pass

        if cmd:
            try:
                self._active_proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
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
        if not self.units:
            self.playback_state_changed.emit("stopped")
            return

        self.playback_state_changed.emit("playing")
        executor = ThreadPoolExecutor(max_workers=2)
        futures: dict[tuple[int, int], Future] = {}
        consecutive_failures = 0

        try:
            idx = self.current_index
            while idx < len(self.units) and not self._stop_requested:
                while self.is_paused and not self._stop_requested:
                    time.sleep(0.04)
                if self._stop_requested:
                    break

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

                if audio_file and os.path.exists(audio_file):
                    consecutive_failures = 0
                    self.current_index = idx
                    # Emit unit_started BEFORE playing so the sky-blue sentence highlight renders immediately!
                    self.unit_started.emit(
                        unit.page_idx, unit.para_idx, unit.sent_idx, unit.text
                    )
                    self.status_changed.emit(
                        f"Speaking [p. {unit.page_idx + 1}]: “{unit.text[:75]}"
                        f"{'...' if len(unit.text) > 75 else ''}”"
                    )
                    self._play_audio_file(audio_file)

                    try:
                        os.remove(audio_file)
                    except OSError:
                        pass

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
            executor.shutdown(wait=False, cancel_futures=True)
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            if not self._stop_requested:
                self.playback_state_changed.emit("stopped")