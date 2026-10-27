import re
import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from PySide6.QtCore import QThread, Signal


class LookupWorker(QThread):
    """
    Non-blocking worker that queries:
    1. Dictionary definitions & phonetics (Free Dictionary API + Wiktionary + Linux 'dict' CLI)
    2. Wikipedia encyclopedic summary (Case-insensitive OpenSearch + Wikimedia REST API)
    3. Multi-language neural translation (Google Translate GTX endpoint)
    """

    dictionary_ready = Signal(str)
    wikipedia_ready = Signal(str)
    translation_ready = Signal(str)

    def __init__(self, text: str, target_lang: str = "ur", mode: str = "all"):
        super().__init__()
        self.text = " ".join(text.strip().split())
        self.target_lang = target_lang
        self.mode = mode  # "all" or "translate_only"

    @staticmethod
    def _clean_term(raw: str) -> str:
        # Strip smart quotes, brackets, and trailing punctuation
        cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", raw, flags=re.UNICODE)
        return cleaned.strip()

    def run(self):
        if not self.text:
            return

        words = [self._clean_term(w) for w in self.text.split() if self._clean_term(w)]
        if not words:
            return

        single_word = words[0]
        phrase = " ".join(words[:4])

        if self.mode == "all":
            self.dictionary_ready.emit(self._fetch_dictionary(single_word))
            self.wikipedia_ready.emit(self._fetch_wikipedia(phrase))

        self.translation_ready.emit(self._fetch_translation(self.text, self.target_lang))

    @staticmethod
    def _http_get_json(url: str, timeout: float = 6.0):
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) LocalLibraryReader/1.0",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.URLError:
            raise

    def _fetch_dictionary(self, word: str) -> str:
        if not word:
            return "<p style='color:#A6ADC8;'>No valid word selected.</p>"

        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word.lower())}"
        last_err = None
        try:
            data = self._http_get_json(url)
            if isinstance(data, list) and data:
                entry = data[0]
                word_title = entry.get("word", word)
                phonetic = entry.get("phonetic") or ""
                if not phonetic:
                    for p in entry.get("phonetics", []):
                        if p.get("text"):
                            phonetic = p["text"]
                            break

                html = [
                    f"<h3 style='color:#89B4FA; margin-bottom:2px;'>{word_title} "
                    f"<span style='color:#A6ADC8; font-weight:normal;'>{phonetic}</span></h3>"
                ]

                for meaning in entry.get("meanings", []):
                    pos = meaning.get("partOfSpeech", "term")
                    html.append(
                        f"<p style='color:#F9E2AF; font-weight:bold; margin:8px 0 2px 0;'>"
                        f"<i>{pos}</i></p><ol style='margin-top:2px;'>"
                    )
                    for d in meaning.get("definitions", [])[:4]:
                        defn = d.get("definition", "")
                        ex = d.get("example", "")
                        ex_html = f"<br><span style='color:#A6ADC8;'><i>“{ex}”</i></span>" if ex else ""
                        html.append(f"<li style='margin-bottom:4px;'>{defn}{ex_html}</li>")
                    html.append("</ol>")
                return "".join(html)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)

        # Offline fallback: Linux 'dict' CLI if installed
        if shutil.which("dict"):
            try:
                res = subprocess.run(["dict", word], capture_output=True, text=True, timeout=3)
                if res.stdout.strip():
                    return f"<pre style='white-space:pre-wrap; color:#CDD6F4;'>{res.stdout.strip()}</pre>"
            except Exception:
                pass

        err_note = f"<br><span style='color:#F38BA8; font-size:11px;'>Network detail: {last_err}</span>" if last_err else ""
        return (
            f"<p style='color:#A6ADC8;'>No dictionary entry found for <b>“{word}”</b>.{err_note}<br><br>"
            "Tip: Check the <b>🌐 Wikipedia</b> or <b>🔤 Translate</b> tab for technical terms and phrases.</p>"
        )

    def _fetch_wikipedia(self, query: str) -> str:
        if not query:
            return "<p style='color:#A6ADC8;'>No query selected.</p>"

        try:
            # Step 1: Resolve exact article title via Wikipedia case-insensitive OpenSearch
            search_url = (
                "https://en.wikipedia.org/w/api.php?action=opensearch"
                f"&search={urllib.parse.quote(query)}&limit=1&namespace=0&format=json"
            )
            search_data = self._http_get_json(search_url)
            resolved_title = query
            if (
                isinstance(search_data, list)
                and len(search_data) >= 2
                and isinstance(search_data[1], list)
                and search_data[1]
            ):
                resolved_title = search_data[1][0]

            # Step 2: Fetch rich summary for resolved title
            slug = urllib.parse.quote(resolved_title.replace(" ", "_"))
            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
            data = self._http_get_json(summary_url)

            title = data.get("title", resolved_title)
            desc = data.get("description", "")
            extract_html = data.get("extract_html") or f"<p>{data.get('extract', '')}</p>"
            page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

            header = f"<h3 style='color:#89B4FA; margin-bottom:2px;'>{title}</h3>"
            sub = f"<p style='color:#F9E2AF; margin-top:0;'><i>{desc}</i></p>" if desc else ""
            link = (
                f"<p><a href='{page_url}' style='color:#89DCEB;'>Open full article on Wikipedia ↗</a></p>"
                if page_url
                else ""
            )
            return f"{header}{sub}{extract_html}{link}"
        except Exception as e:
            return (
                f"<p style='color:#A6ADC8;'>No Wikipedia summary found for <b>“{query}”</b>.<br>"
                f"<span style='color:#6C7086; font-size:11px;'>({e})</span></p>"
            )

    def _fetch_translation(self, text: str, target_lang: str) -> str:
        q = urllib.parse.quote(text[:1200])
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl={target_lang}&dt=t&q={q}"
        )
        try:
            data = self._http_get_json(url)
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
                detected_src = data[2] if len(data) > 2 else "auto"
                return (
                    f"<p style='color:#A6ADC8; font-size:11px;'>Detected source: "
                    f"<b>{str(detected_src).upper()}</b> → <b>{target_lang.upper()}</b></p>"
                    f"<p style='font-size:15px; line-height:1.55; color:#A6E3A1;'>{translated}</p>"
                    f"<hr style='border:1px solid #313244;'>"
                    f"<p style='color:#9399B2; font-size:12px;'><b>Original:</b> {text}</p>"
                )
        except Exception as e:
            return f"<p style='color:#F38BA8;'>Translation error: {e}</p>"
        return "<p style='color:#A6ADC8;'>Could not translate selection.</p>"