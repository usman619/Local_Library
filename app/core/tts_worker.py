import os
import re
import sys
import json
import time
import shutil
import asyncio
import tempfile
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import QThread, Signal

try:
    import sounddevice as sd
    import soundfile as sf
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False


@dataclass
class TTSUnit:
    page_idx: int
    para_idx: int
    sent_idx: int
    text: str


class KokoroPersistentProcess:
    """Singleton manager that keeps kokoro_daemon.py alive on GPU/CPU across TTS sessions."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.is_ready = False
        self.device_name = "CPU"

    @classmethod
    def instance(cls) -> "KokoroPersistentProcess":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def ensure_started(self, status_callback=None) -> tuple[bool, str | None]:
        if self.proc is not None and self.proc.poll() is None and self.is_ready:
            return True, None

        daemon_script = Path(__file__).parent / "kokoro_daemon.py"

        child_env = os.environ.copy()
        child_env.pop("CUDA_VISIBLE_DEVICES", None)
        child_env["OMP_NUM_THREADS"] = "2"
        child_env["MKL_NUM_THREADS"] = "2"

        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(daemon_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            env=child_env,
        )

        while True:
            line = self.proc.stdout.readline()
            if not line:
                return False, "Kokoro process exited unexpectedly."
            try:
                msg = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            event = msg.get("event")
            if event == "status" and status_callback:
                status_callback(msg.get("message", "Loading Kokoro..."))
            elif event == "ready":
                self.is_ready = True
                self.device_name = msg.get("device", "CPU")
                return True, None
            elif event == "fatal":
                self.is_ready = False
                return False, msg.get("error", "Kokoro initialization failed")

    def synthesize(self, text: str, voice: str, speed: float, status_callback=None) -> tuple[str | None, str | None]:
        with self._lock:
            ok, err = self.ensure_started(status_callback=status_callback)
            if not ok:
                return None, err

            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            req = {"cmd": "synth", "text": text, "voice": voice, "speed": speed, "wav_path": wav_path}
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()

                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        self.is_ready = False
                        return None, "Kokoro daemon closed unexpectedly."
                    resp = json.loads(line.strip())
                    if resp.get("event") == "done":
                        if resp.get("ok") and os.path.exists(wav_path):
                            return wav_path, None
                        if os.path.exists(wav_path):
                            os.remove(wav_path)
                        return None, resp.get("error", "Synthesis failed")
            except Exception as e:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
                self.is_ready = False
                return None, str(e)


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")


def build_page_units(page_idx: int, blocks: list) -> list[TTSUnit]:
    """Converts PyMuPDF page blocks into structured Paragraph -> Sentence TTSUnits."""
    units: list[TTSUnit] = []
    para_counter = 0

    for b in blocks:
        # PyMuPDF block tuple: (x0, y0, x1, y1, "text", block_no, block_type)
        if len(b) >= 7 and b[6] != 0:
            continue  # Skip image blocks
        raw_para = " ".join(str(b[4]).split())
        if not raw_para:
            continue

        parts = _SENT_SPLIT_RE.split(raw_para)
        sentences: list[str] = []
        buf = []
        for part in parts:
            buf.append(part)
            joined = " ".join(buf)
            if len(joined.split()) >= 6:
                sentences.append(joined)
                buf = []
        if buf:
            remainder = " ".join(buf)
            if sentences and len(remainder.split()) < 4:
                sentences[-1] = f"{sentences[-1]} {remainder}"
            else:
                sentences.append(remainder)

        for sent_idx, sent_text in enumerate(sentences):
            units.append(
                TTSUnit(
                    page_idx=page_idx,
                    para_idx=para_counter,
                    sent_idx=sent_idx,
                    text=sent_text,
                )
            )
        para_counter += 1

    return units


class NeuralTTSWorker(QThread):
    """
    Stateful Neural TTS Controller supporting:
    - Real-time sentence highlighting & resume memory
    - Play / Pause / Stop
    - Next / Previous Paragraph jumping
    - Live Speed (0.5x - 2.0x) and Pitch (-6 to +6 semitones) adjustment
    """

    status_changed = Signal(str)
    # Emits (page_idx, para_idx, sent_idx, sentence_text)
    unit_started = Signal(int, int, int, str)
    playback_state_changed = Signal(str)  # "playing", "paused", "stopped"

    def __init__(
        self,
        units: list[TTSUnit],
        start_index: int,
        voice_id: str,
        speed: float = 1.0,
        pitch_semitones: int = 0,
    ):
        super().__init__()
        self.units = units
        self.current_index = max(0, min(start_index, len(units) - 1)) if units else 0
        self.voice_id = voice_id
        self.speed = speed
        self.pitch_semitones = pitch_semitones

        self._lock = threading.Lock()
        self._paused = False
        self._stop_requested = False
        self._seek_target: int | None = None
        self._play_proc: subprocess.Popen | None = None
        self._prefetch_cache: dict[tuple[int, str, float, int], str] = {}

    @property
    def is_paused(self) -> bool:
        return self._paused

    def set_voice(self, voice_id: str):
        with self._lock:
            self.voice_id = voice_id
            self._clear_prefetch_cache()

    def set_speed(self, speed: float):
        with self._lock:
            self.speed = max(0.5, min(2.0, round(speed, 2)))
            self._clear_prefetch_cache()

    def set_pitch(self, semitones: int):
        with self._lock:
            self.pitch_semitones = max(-6, min(6, int(semitones)))
            self._clear_prefetch_cache()

    def pause(self):
        with self._lock:
            if not self._paused:
                self._paused = True
                self._interrupt_active_audio()
                self.playback_state_changed.emit("paused")
                if 0 <= self.current_index < len(self.units):
                    u = self.units[self.current_index]
                    self.status_changed.emit(
                        f"Paused at Page {u.page_idx + 1}, Paragraph {u.para_idx + 1}"
                    )

    def resume(self):
        with self._lock:
            if self._paused:
                self._paused = False
                self.playback_state_changed.emit("playing")

    def stop(self):
        with self._lock:
            self._stop_requested = True
            self._paused = False
            self._interrupt_active_audio()
            self._clear_prefetch_cache()

    def next_paragraph(self):
        with self._lock:
            if not self.units:
                return
            cur = self.units[self.current_index]
            for idx in range(self.current_index + 1, len(self.units)):
                u = self.units[idx]
                if u.page_idx != cur.page_idx or u.para_idx != cur.para_idx:
                    self._seek_target = idx
                    self._interrupt_active_audio()
                    return
            # If already on the very last paragraph, move to its last sentence or stay
            self._seek_target = len(self.units) - 1
            self._interrupt_active_audio()

    def prev_paragraph(self):
        with self._lock:
            if not self.units:
                return
            cur = self.units[self.current_index]
            # If we are past the first sentence of the current paragraph, jump to start of current paragraph
            if cur.sent_idx > 0:
                for idx in range(self.current_index, -1, -1):
                    u = self.units[idx]
                    if u.page_idx == cur.page_idx and u.para_idx == cur.para_idx and u.sent_idx == 0:
                        self._seek_target = idx
                        self._interrupt_active_audio()
                        return

            # Otherwise find the first sentence of the previous paragraph
            prev_page, prev_para = None, None
            for idx in range(self.current_index - 1, -1, -1):
                u = self.units[idx]
                if u.page_idx != cur.page_idx or u.para_idx != cur.para_idx:
                    prev_page, prev_para = u.page_idx, u.para_idx
                    break

            if prev_page is not None:
                for idx in range(self.current_index - 1, -1, -1):
                    u = self.units[idx]
                    if u.page_idx == prev_page and u.para_idx == prev_para and u.sent_idx == 0:
                        self._seek_target = idx
                        self._interrupt_active_audio()
                        return

            self._seek_target = 0
            self._interrupt_active_audio()

    def _interrupt_active_audio(self):
        if self._play_proc and self._play_proc.poll() is None:
            try:
                self._play_proc.terminate()
            except Exception:
                pass
        if HAS_SOUNDDEVICE:
            try:
                sd.stop()
            except Exception:
                pass

    def _clear_prefetch_cache(self):
        for path in list(self._prefetch_cache.values()):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self._prefetch_cache.clear()

    def run(self):
        if not self.units:
            self.status_changed.emit("TTS Warning: No readable text found.")
            self.playback_state_changed.emit("stopped")
            return

        self.playback_state_changed.emit("playing")

        while not self._stop_requested and self.current_index < len(self.units):
            # 1. Handle pause state while still allowing paragraph seeks
            if self._paused:
                with self._lock:
                    if self._seek_target is not None:
                        self.current_index = self._seek_target
                        self._seek_target = None
                        u = self.units[self.current_index]
                        self.unit_started.emit(u.page_idx, u.para_idx, u.sent_idx, u.text)
                        self.status_changed.emit(
                            f"Paused at Page {u.page_idx + 1}, Para {u.para_idx + 1}: {u.text[:55]}..."
                        )
                time.sleep(0.08)
                continue

            # 2. Apply any pending seek
            with self._lock:
                if self._seek_target is not None:
                    self.current_index = self._seek_target
                    self._seek_target = None
                idx = self.current_index
                unit = self.units[idx]
                voice_id = self.voice_id
                speed = self.speed
                pitch = self.pitch_semitones

            # Emit immediately so the reader highlights the active sentence right away
            self.unit_started.emit(unit.page_idx, unit.para_idx, unit.sent_idx, unit.text)

            # 3. Fetch from prefetch cache or synthesize now
            cache_key = (idx, voice_id, speed, pitch)
            audio_path = None
            with self._lock:
                audio_path = self._prefetch_cache.pop(cache_key, None)

            if not audio_path or not os.path.exists(audio_path):
                self.status_changed.emit(
                    f"Synthesizing (Page {unit.page_idx + 1}, Para {unit.para_idx + 1})..."
                )
                audio_path, err = self._synthesize_unit(unit.text, voice_id, speed, pitch)
                if err and not self._stop_requested and not self._paused:
                    self.status_changed.emit(f"TTS Error: {err}")
                    break

            if self._stop_requested:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
                break

            # If user paused or clicked Next/Prev while synthesizing, loop back without advancing
            if self._paused or self._seek_target is not None:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
                continue

            # 4. Pre-fetch the next sentence in a background thread while current sentence plays
            next_idx = idx + 1
            if next_idx < len(self.units):
                threading.Thread(
                    target=self._prefetch_unit,
                    args=(next_idx, voice_id, speed, pitch),
                    daemon=True,
                ).start()

            dev_tag = (
                f"[Kokoro {KokoroPersistentProcess.instance().device_name}]"
                if voice_id.startswith("kokoro:")
                else "[Edge-TTS]"
            )
            pitch_str = f" | Pitch {pitch:+d}st" if pitch != 0 else ""
            self.status_changed.emit(
                f"{dev_tag} ({speed:.1f}x{pitch_str}) P.{unit.page_idx + 1} ¶{unit.para_idx + 1}: {unit.text[:60]}..."
            )

            # 5. Play the audio file
            if audio_path and os.path.exists(audio_path):
                self._play_audio_file_linux(audio_path)
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

            # 6. Only advance to the next sentence if playback wasn't paused or seeked
            with self._lock:
                if self._stop_requested:
                    break
                if self._seek_target is not None:
                    self.current_index = self._seek_target
                    self._seek_target = None
                elif not self._paused:
                    self.current_index += 1

        self._clear_prefetch_cache()
        if not self._stop_requested:
            self.status_changed.emit("TTS Finished")
        else:
            self.status_changed.emit("TTS Stopped")
        self.playback_state_changed.emit("stopped")

    def _prefetch_unit(self, idx: int, voice_id: str, speed: float, pitch: int):
        cache_key = (idx, voice_id, speed, pitch)
        with self._lock:
            if cache_key in self._prefetch_cache or self._stop_requested or self._paused:
                return
        if 0 <= idx < len(self.units):
            path, err = self._synthesize_unit(self.units[idx].text, voice_id, speed, pitch)
            if path and not err:
                with self._lock:
                    if not self._stop_requested:
                        self._prefetch_cache[cache_key] = path
                    elif os.path.exists(path):
                        os.remove(path)

    def _synthesize_unit(
        self, text: str, voice_id: str, speed: float, pitch_semitones: int
    ) -> tuple[str | None, str | None]:
        use_kokoro = voice_id.startswith("kokoro:")
        voice_name = voice_id.split(":", 1)[-1]

        raw_path, err = None, None
        if use_kokoro:
            raw_path, err = KokoroPersistentProcess.instance().synthesize(
                text, voice_name, speed
            )
            if not raw_path and HAS_EDGE_TTS and not self._stop_requested:
                use_kokoro = False
                voice_name = "en-US-AriaNeural"
                raw_path, err = self._synthesize_edge_tts(text, voice_name, speed, pitch_semitones)
                return raw_path, err
        elif HAS_EDGE_TTS:
            raw_path, err = self._synthesize_edge_tts(text, voice_name, speed, pitch_semitones)
            return raw_path, err
        else:
            return None, "Neither 'edge-tts' nor 'kokoro' is installed."

        if not raw_path or err:
            return None, err

        # Apply pitch shift via FFmpeg rubberband/asetrate filter if pitch_semitones != 0
        if pitch_semitones != 0 and shutil.which("ffmpeg"):
            shifted_path = self._apply_pitch_shift_ffmpeg(raw_path, pitch_semitones)
            if shifted_path:
                try:
                    os.remove(raw_path)
                except OSError:
                    pass
                return shifted_path, None

        return raw_path, None

    @staticmethod
    def _apply_pitch_shift_ffmpeg(input_wav: str, semitones: int) -> str | None:
        """Shifts pitch by N semitones while preserving exact speaking tempo."""
        factor = 2.0 ** (semitones / 12.0)
        inv_factor = 1.0 / factor
        fd, out_wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        filter_str = f"asetrate=24000*{factor:.4f},aresample=24000,atempo={inv_factor:.4f}"
        res = subprocess.run(
            ["ffmpeg", "-y", "-i", input_wav, "-af", filter_str, "-loglevel", "quiet", out_wav],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 0:
            return out_wav
        if os.path.exists(out_wav):
            os.remove(out_wav)
        return None

    @staticmethod
    def _synthesize_edge_tts(
        text: str, voice_name: str, speed: float, pitch_semitones: int
    ) -> tuple[str | None, str | None]:
        rate_pct = int((speed - 1.0) * 100)
        rate_str = f"{rate_pct:+d}%"
        pitch_hz = int(pitch_semitones * 4)
        pitch_str = f"{pitch_hz:+d}Hz"

        fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)

        try:
            async def _fetch():
                communicate = edge_tts.Communicate(
                    text, voice_name, rate=rate_str, pitch=pitch_str
                )
                await communicate.save(mp3_path)

            asyncio.run(_fetch())
            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                return mp3_path, None
            return None, "Edge-TTS returned empty audio."
        except Exception as e:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
            return None, f"Edge-TTS failed ({e})"

    def _play_audio_file_linux(self, filepath: str) -> str | None:
        if shutil.which("ffplay"):
            self._play_proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._play_proc.wait()
            return None

        if shutil.which("mpv"):
            self._play_proc = subprocess.Popen(
                ["mpv", "--no-video", "--really-quiet", filepath],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._play_proc.wait()
            return None

        wav_path = filepath
        converted_wav = False
        if not filepath.lower().endswith(".wav") and shutil.which("ffmpeg"):
            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            converted_wav = True
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", filepath,
                    "-ar", "24000", "-ac", "1", "-loglevel", "quiet", wav_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        try:
            if shutil.which("paplay") and os.path.exists(wav_path):
                self._play_proc = subprocess.Popen(
                    ["paplay", wav_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._play_proc.wait()
                return None

            if shutil.which("aplay") and os.path.exists(wav_path):
                self._play_proc = subprocess.Popen(
                    ["aplay", "-q", wav_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._play_proc.wait()
                return None

            if HAS_SOUNDDEVICE and os.path.exists(wav_path):
                data, sr = sf.read(wav_path, dtype="float32")
                sd.play(data, samplerate=sr)
                sd.wait()
                return None

            return "No audio player found. Install ffmpeg (`sudo apt install ffmpeg`)."
        except Exception as e:
            return str(e)
        finally:
            if converted_wav and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass