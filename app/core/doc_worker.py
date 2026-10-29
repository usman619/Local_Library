"""
Background document worker (runs as a separate PROCESS: ``python -m app.core.doc_worker``).

Why a process and not a QThread: PyMuPDF does not support running on multiple threads
("doing so may cause incorrect behaviour or even crash Python itself" - PyMuPDF docs,
Recipes: Multiprocessing) and the GUI thread is rendering pages from its own document at
the same time. A child process gets its own copy of MuPDF, so a slow in-book search or a
multi-second OCR pass can never block, or corrupt, the reader.

Protocol (one JSON object per line):
  stdin  -> {"cmd": "open", "filepath": ..., "fontsize": ...}            (first message)
            {"cmd": "search", "id": N, "query": ..., "cap": M}
            {"cmd": "tts_units", "id": N, "filepath": ..., "start_page": P,
                                 "max_pages": K, "allow_ocr": true}
            {"cmd": "cancel", "id": N}
  stdout <- {"event": "ready", "pages": N} | {"event": "fatal", "error": ...}
            {"event": "search_batch", "id": N, ...} | {"event": "search_done", "id": N, ...}
            {"event": "tts_page_start" | "tts_page" | "tts_done", "id": N, ...}
            {"event": "error", "id": N, "error": ...}

A request is abandoned as soon as it is cancelled OR a newer request arrives, so typing
a new search query supersedes the previous one without any bookkeeping on the GUI side.
When the parent exits, stdin closes and this process exits too (no orphans).
"""
import json
import os
import queue
import sys
import threading
import time


def _emit_factory(out):
    lock = threading.Lock()

    def emit(payload: dict):
        with lock:
            out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            out.flush()

    return emit


def main() -> int:
    # Anything a library prints must not corrupt the JSON protocol on stdout.
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    emit = _emit_factory(protocol_out)

    commands: "queue.Queue[dict | None]" = queue.Queue()
    state = {"latest": 0}
    cancelled: set[int] = set()

    def reader():
        """Reads stdin only (never touches MuPDF), so cancellation is seen mid-job."""
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except ValueError:
                    continue
                if req.get("cmd") == "cancel":
                    cancelled.add(int(req.get("id", 0)))
                    continue
                rid = req.get("id")
                if isinstance(rid, int):
                    state["latest"] = max(state["latest"], rid)
                commands.put(req)
        finally:
            commands.put(None)  # parent closed the pipe / died -> shut down

    threading.Thread(target=reader, daemon=True).start()

    def is_cancelled(rid: int) -> bool:
        return rid in cancelled or rid < state["latest"]

    doc = None
    doc_path = ""

    def open_doc(filepath: str, fontsize: float):
        from app.core.epub_loader import open_clean_document

        return open_clean_document(filepath, fontsize=fontsize)

    # ------------------------------------------------------------------ search
    def do_search(req: dict):
        rid = req["id"]
        query = str(req.get("query", ""))
        cap = int(req.get("cap", 3000))
        total = len(doc)
        q_lower = query.lower()
        matches = 0
        capped = False
        batch: list[dict] = []
        last_emit = time.monotonic()
        pages_done = 0

        for pno in range(total):
            if is_cancelled(rid):
                return
            page = doc.load_page(pno)
            rects = page.search_for(query)
            pages_done = pno + 1
            if rects:
                page_text = " ".join(page.get_text("text").split())
                pt_lower = page_text.lower()
                search_pos = 0
                hits = []
                for r in rects:
                    pos = pt_lower.find(q_lower, search_pos)
                    if pos != -1:
                        start = max(0, pos - 30)
                        end = min(len(page_text), pos + len(query) + 40)
                        snippet = (
                            ("..." if start > 0 else "")
                            + page_text[start:end]
                            + ("..." if end < len(page_text) else "")
                        )
                        search_pos = pos + len(query)
                    else:
                        snippet = query
                    hits.append(
                        {"rect": [float(r.x0), float(r.y0), float(r.x1), float(r.y1)],
                         "snippet": snippet}
                    )
                    matches += 1
                    if matches >= cap:
                        capped = True
                        break
                batch.append({"page": pno, "hits": hits})

            now = time.monotonic()
            if capped or now - last_emit >= 0.08 or pno == total - 1:
                emit({"event": "search_batch", "id": rid, "pages_done": pages_done,
                      "total": total, "batch": batch})
                batch = []
                last_emit = now
            if capped:
                break

        if is_cancelled(rid):
            return
        emit({"event": "search_done", "id": rid, "matches": matches,
              "capped": capped, "total": total})

    # --------------------------------------------------------------- TTS text
    def do_tts_units(req: dict):
        rid = req["id"]
        # Imported lazily: pulls in Qt's core module, which plain search doesn't need.
        from app.core.tts_worker import extract_page_tts_units

        start = int(req.get("start_page", 0))
        end = min(len(doc), start + int(req.get("max_pages", 15)))
        filepath = req.get("filepath", doc_path)
        allow_ocr = bool(req.get("allow_ocr", True))
        skip_headers_footers = bool(req.get("skip_headers_footers", True))
        total_units = 0

        for p_idx in range(start, end):
            if is_cancelled(rid):
                return
            emit({"event": "tts_page_start", "id": rid, "page": p_idx})
            units, used_ocr = extract_page_tts_units(
                doc,
                p_idx,
                filepath=filepath,
                allow_ocr=allow_ocr,
                skip_headers_footers=skip_headers_footers,
            )
            if is_cancelled(rid):
                return
            total_units += len(units)
            emit({
                "event": "tts_page", "id": rid, "page": p_idx, "used_ocr": bool(used_ocr),
                "units": [
                    {"page_idx": u.page_idx, "para_idx": u.para_idx,
                     "sent_idx": u.sent_idx, "text": u.text,
                     "bbox": list(u.bbox) if u.bbox else None}
                    for u in units
                ],
            })
            # Heavy scanned books: only OCR two pages ahead so audio can start quickly.
            if used_ocr and (p_idx - start) >= 1 and total_units >= 5:
                break

        if not is_cancelled(rid):
            emit({"event": "tts_done", "id": rid})

    # -------------------------------------------------------------- main loop
    while True:
        req = commands.get()
        if req is None:
            break
        cmd = req.get("cmd")
        try:
            if cmd == "open":
                doc_path = req["filepath"]
                doc = open_doc(doc_path, float(req.get("fontsize", 12.5)))
                emit({"event": "ready", "pages": len(doc)})
                continue
            if doc is None:
                emit({"event": "fatal", "error": "document is not open"})
                continue
            rid = req.get("id", 0)
            if is_cancelled(rid):
                continue
            if cmd == "search":
                do_search(req)
            elif cmd == "tts_units":
                do_tts_units(req)
        except Exception as e:  # one bad request must not take the worker down
            if cmd == "open":
                emit({"event": "fatal", "error": f"{type(e).__name__}: {e}"})
            else:
                emit({"event": "error", "id": req.get("id", 0),
                      "error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())