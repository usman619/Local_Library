import os
import sys
import gc
import json
import time

# 1. Remove the parent Qt GUI process's CUDA block so this isolated worker can see the GPU
if os.environ.get("CUDA_VISIBLE_DEVICES") == "-1":
    del os.environ["CUDA_VISIBLE_DEVICES"]

# 2. Cap OpenMP / MKL threads BEFORE importing torch/numpy so CPU usage never spikes to 100%
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"

import numpy as np
import soundfile as sf


def _select_device(torch_module) -> str:
    """Safely selects 'cuda' if the NVIDIA driver is healthy, otherwise falls back to 'cpu'."""
    try:
        if torch_module.cuda.is_available():
            # Perform a tiny allocation test to verify nvidia_uvm isn't stuck
            _ = torch_module.zeros(1, device="cuda")
            return "cuda"
    except Exception as e:
        sys.stderr.write(f"[Kokoro] CUDA unavailable ({e}), falling back to throttled CPU.\n")
    return "cpu"


def run_warmup():
    print("1/3 Importing PyTorch and Kokoro...")
    import torch
    from kokoro import KPipeline

    device = _select_device(torch)
    torch.set_num_threads(2 if device == "cuda" else min(4, max(1, (os.cpu_count() or 4) // 2)))
    print(f"2/3 Initializing KPipeline on device='{device}'...")

    t0 = time.time()
    try:
        pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=device)
    except TypeError:
        pipe = KPipeline(lang_code="a")
    print(f"    Pipeline loaded on {device.upper()} in {time.time() - t0:.2f}s")

    print("3/3 Running test synthesis with voice 'af_heart'...")
    t1 = time.time()
    with torch.inference_mode():
        chunks = [
            audio for _, _, audio in pipe("Kokoro neural text to speech is now running efficiently.", voice="af_heart", speed=1.0)
        ]
    print(f"    Synthesized {len(chunks)} chunk(s) in {time.time() - t1:.2f}s on {device.upper()}!")


def run_daemon():
    """Persistent JSON-line worker over stdin/stdout with GPU acceleration & low RAM."""
    protocol_out = sys.stdout
    sys.stdout = sys.stderr

    def send(payload: dict):
        protocol_out.write(json.dumps(payload) + "\n")
        protocol_out.flush()

    try:
        import torch
        from kokoro import KPipeline

        device = _select_device(torch)
        # Keep CPU threads low: 2 threads when feeding GPU, max 4 threads on CPU-only
        cpu_threads = 2 if device == "cuda" else min(4, max(1, (os.cpu_count() or 4) // 2))
        torch.set_num_threads(cpu_threads)

        send({"event": "status", "message": f"Kokoro: Loading model on {device.upper()}..."})

        pipelines = {}
        shared_model = None

        def get_pipeline(lang_code: str):
            nonlocal shared_model
            if lang_code not in pipelines:
                try:
                    if shared_model is None:
                        p = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M", device=device)
                        shared_model = getattr(p, "model", None)
                    else:
                        p = KPipeline(
                            lang_code=lang_code,
                            repo_id="hexgrad/Kokoro-82M",
                            model=shared_model,
                            device=device,
                        )
                except TypeError:
                    p = KPipeline(lang_code=lang_code)
                pipelines[lang_code] = p
            return pipelines[lang_code]

        get_pipeline("a")
        send({"event": "ready", "device": device.upper(), "message": f"Kokoro Ready ({device.upper()})"})

        for raw_line in sys.stdin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            req = json.loads(raw_line)
            if req.get("cmd") == "quit":
                break

            text = req["text"]
            voice = req["voice"]
            speed = float(req.get("speed", 1.0))
            wav_path = req["wav_path"]
            lang_code = voice[0] if voice else "a"

            try:
                pipe = get_pipeline(lang_code)
                chunks = []
                # torch.inference_mode() disables autograd graph tracking, saving huge RAM & CPU
                with torch.inference_mode():
                    for _, _, audio in pipe(text, voice=voice, speed=speed):
                        if audio is not None:
                            arr = (
                                audio.detach().cpu().numpy()
                                if hasattr(audio, "detach")
                                else np.asarray(audio, dtype=np.float32)
                            )
                            chunks.append(arr)

                if chunks:
                    full_audio = np.concatenate(chunks)
                    sf.write(wav_path, full_audio, 24000)
                    del full_audio
                    chunks.clear()
                    send({"event": "done", "ok": True, "wav_path": wav_path, "device": device.upper()})
                else:
                    send({"event": "done", "ok": False, "error": "Empty audio output"})

                # Prevent PyTorch / Python memory fragmentation over long chapters
                gc.collect()
                if device == "cuda":
                    torch.cuda.empty_cache()

            except Exception as e:
                send({"event": "done", "ok": False, "error": str(e)})

    except Exception as e:
        send({"event": "fatal", "error": str(e)})


if __name__ == "__main__":
    if "--warmup" in sys.argv:
        run_warmup()
    else:
        run_daemon()