"""Веб-поиск и чтение страниц — только стандартная библиотека (без зависимостей).

Используется агентом, когда термин, имя или реалию нужно уточнить перед
переводом. Запросы к локальным и приватным адресам блокируются (защита от
обращений к внутренним сервисам).
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
MAX_FETCH_BYTES = 1_000_000

_DDG_HTML = "https://html.duckduckgo.com/html/"
_DDG_LITE = "https://lite.duckduckgo.com/lite/"

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5",
    "h6", "section", "article", "header", "footer", "pre", "blockquote",
    "ul", "ol", "table", "form", "figure", "aside", "main", "nav",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template"}

_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S
)
_SNIPPET_RE = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(text).strip()


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        return _strip_tags(raw_html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _assert_public(url: str) -> None:
    """Не даём обращаться к localhost и приватным сетям."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("разрешены только http/https-адреса")
    host = parsed.hostname
    if not host:
        raise ValueError("в адресе не указан хост")
    if host.lower() in ("localhost",) or host.lower().endswith(".local"):
        raise ValueError(f"{host} — локальный адрес, запрос заблокирован")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"не удалось разрешить имя {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"{host} ведёт в локальную/приватную сеть, запрос заблокирован")


def fetch_url(url: str, max_chars: int = 12000, timeout: float = 20) -> str:
    """Скачивает страницу и возвращает её текстом."""
    url = (url or "").strip()
    if not url:
        return "Ошибка: пустой URL."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        _assert_public(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_FETCH_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
    except (ValueError, urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError) as exc:
        return f"Ошибка загрузки {url}: {exc}"
    try:
        page = raw.decode(charset, errors="replace")
    except LookupError:
        page = raw.decode("utf-8", errors="replace")
    text = html_to_text(page)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... текст страницы обрезан ...]"
    return text or "Страница пуста."


def _clean_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query:
            return query["uddg"][0]
    return url


def _ddg_request(endpoint: str, query: str, timeout: float) -> str:
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=data, headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_FETCH_BYTES).decode("utf-8", errors="replace")


def _parse_ddg_html(page: str, max_results: int) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    snippets = [_strip_tags(m) for m in _SNIPPET_RE.findall(page)]
    for i, match in enumerate(_RESULT_RE.finditer(page)):
        if len(results) >= max_results:
            break
        url = _clean_url(html.unescape(match.group(1)))
        title = _strip_tags(match.group(2))
        snippet = snippets[i] if i < len(snippets) else ""
        results.append((title, url, snippet))
    return results


def web_search(query: str, max_results: int = 5, timeout: float = 20) -> str:
    """Ищет в интернете и возвращает результат текстом (для модели)."""
    query = (query or "").strip()
    if not query:
        return "Ошибка: пустой поисковый запрос."

    last_error: Exception | None = None
    for endpoint in (_DDG_HTML, _DDG_LITE):
        try:
            page = _ddg_request(endpoint, query, timeout)
            results = _parse_ddg_html(page, max_results)
        except Exception as exc:  # noqa: BLE001 — сеть бывает разной
            last_error = exc
            continue
        if not results:
            continue
        lines = [f"Результаты поиска «{query}»:"]
        for i, (title, url, snippet) in enumerate(results, 1):
            lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
        return "\n".join(lines)

    if last_error is not None:
        return f"Поиск не удался: {last_error}"
    return f"По запросу «{query}» ничего не найдено."
