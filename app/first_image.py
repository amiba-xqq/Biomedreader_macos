"""Read the first article image in a DOI landing page, preserving its document order.

There is no model/graphical-abstract ranking and no full-text API fallback. A page that requires
login or JavaScript may not expose its figures in the returned HTML; in that case show no image.
"""
from __future__ import annotations

from version import APP_VERSION

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests


@dataclass
class ImageResult:
    path: str = ""
    source_url: str = ""
    caption: str = ""
    status: str = ""


def public_url(url: str) -> bool:
    import ipaddress
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or host == "localhost" or host.endswith((".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


class FirstImageHTML(HTMLParser):
    """Collect figures in DOM order, excluding site logos, navigation, ads and tracking pixels.

    Explicit figure/graphical abstract containers identify article images reliably. If a publisher
    uses plain images instead, accept images inside its article/main body. Neither captions nor
    figure numbers are used to reorder images: an ordinary Figure 1 is a valid first image.
    """
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    FIGURE = re.compile(r"(?:^|[\s_\-])(?:fig(?:ure)?\d*|graphical[\s_\-]?abstract)(?:$|[\s_\-])", re.I)
    BODY = re.compile(r"article[\s_\-]?(?:body|content|text)|full[\s_\-]?text|main[\s_\-]?content", re.I)
    DECORATIVE = re.compile(r"logo|favicon|avatar|tracking|spacer|advert|journal[\s_\-]?cover|social[\s_\-]|share[\s_\-]|(?:^|[/_\-])icon(?:[/_.\-]|$)", re.I)

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.figures: list[tuple[str, str]] = []
        self.body_images: list[tuple[str, str]] = []
        self.images: list[tuple[str, str]] = []
        self.picture_source = ""

    @staticmethod
    def image_source(attrs):
        for name in ("data-src", "data-original", "data-lazy-src", "data-image-src", "data-srcset", "srcset", "src"):
            value = attrs.get(name, "").strip()
            if not value or value.startswith(("data:", "javascript:", "#")):
                continue
            return value.split(",")[0].strip().split()[0] if name.endswith("srcset") else value
        return ""

    def handle_starttag(self, tag, attributes):
        attrs = {key: value or "" for key, value in attributes}
        if tag == "base" and attrs.get("href"):
            proposed = urljoin(self.base, attrs["href"])
            if public_url(proposed):
                self.base = proposed
        if tag == "picture":
            self.picture_source = ""
        if tag == "source" and any(t == "picture" for t, _ in self.stack):
            self.picture_source = self.picture_source or self.image_source(attrs)
        if tag == "img":
            context = self.stack + [(tag, attrs)]
            markers = " ".join(" ".join(a.get(k, "") for k in ("class", "id", "role")) for _, a in context)
            excluded = any(t in ("header", "footer", "nav", "aside") or a.get("aria-hidden") == "true"
                           or "hidden" in a or "display:none" in a.get("style", "").replace(" ", "")
                           for t, a in context)
            url = urljoin(self.base, self.image_source(attrs) or self.picture_source)
            decorative = self.DECORATIVE.search(" ".join((url, attrs.get("alt", ""), markers)))
            tiny = any(attrs.get(k, "").isdigit() and int(attrs[k]) <= 2 for k in ("width", "height"))
            if not excluded and not decorative and not tiny and public_url(url):
                candidate = (url, attrs.get("alt", "").strip() or "第一张文章图片")
                if any(t == "figure" for t, _ in context) or self.FIGURE.search(markers):
                    self.figures.append(candidate)
                    self.images.append(candidate)
                elif any(t in ("article", "main") for t, _ in context) or self.BODY.search(markers):
                    self.body_images.append(candidate)
                    self.images.append(candidate)
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag == "picture":
            self.picture_source = ""

    def first(self) -> tuple[str, str] | None:
        return self.images[0] if self.images else None


class FirstArticleImageService:
    def __init__(self, cache: Path):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"BiomedReader/{APP_VERSION}"

    def get(self, url: str):
        """Bound transfers and validate each redirect before fetching publisher content."""
        for _ in range(6):
            if not public_url(url):
                raise ValueError("图片或页面地址不可用")
            response = self.session.get(url, timeout=(8, 20), stream=True, allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get("Location", ""))
                response.close()
                if target == url:
                    raise ValueError("页面重定向无效")
                url = target
                continue
            try:
                response.raise_for_status()
                if int(response.headers.get("Content-Length") or 0) > 20_000_000:
                    raise ValueError("图片或页面过大")
                chunks, size = [], 0
                deadline = time.monotonic() + 40
                for chunk in (response.iter_content(65536) if response.raw is not None else [response.content]):
                    size += len(chunk)
                    if size > 20_000_000 or time.monotonic() > deadline:
                        raise ValueError("图片或页面传输超出限制")
                    chunks.append(chunk)
                response._content = b"".join(chunks)
                response._content_consumed = True
                return response
            finally:
                response.close()
        raise ValueError("页面重定向过多")

    def find(self, article: dict, online: bool = True) -> ImageResult:
        # New cache namespace intentionally excludes previously cached model-ranking results.
        key = hashlib.sha256(str(article["article_key"]).encode()).hexdigest()
        meta = self.cache / (key + ".json")
        try:
            saved = json.loads(meta.read_text(encoding="utf-8"))
            image_path = Path(saved.get("path") or "")
            if image_path.is_file() and image_path.resolve().parent == self.cache.resolve():
                from PySide6.QtGui import QImage
                if not QImage(str(image_path)).isNull():
                    return ImageResult(**saved)
        except (OSError, ValueError, TypeError):
            pass
        if not online:
            return ImageResult()
        doi = str(article.get("doi") or "").strip()
        if not doi:
            return ImageResult()
        try:
            page = self.get("https://doi.org/" + quote(doi, safe="/()"))
            if "html" not in page.headers.get("Content-Type", "text/html").lower():
                return ImageResult()
            # Requests otherwise treats many UTF-8 publisher pages without a charset as Latin-1.
            page.encoding = (requests.utils.get_encoding_from_headers(page.headers)
                             if "charset=" in page.headers.get("Content-Type", "").lower() else "utf-8")
            parser = FirstImageHTML(page.url)
            parser.feed(page.text)
            candidate = parser.first()
            if candidate is None:
                return ImageResult()
            url, caption = candidate
            response = self.get(url)
            if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                return ImageResult()
            from PySide6.QtGui import QImage
            image = QImage.fromData(response.content)
            if image.isNull():
                return ImageResult()
            path = self.cache / (key + ".image")
            path.write_bytes(response.content)
            result = ImageResult(str(path), response.url, caption[:2000], "DOI 页面第一张文章图片 · 已缓存，可离线查看")
            meta.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
            return result
        except (requests.RequestException, OSError, ValueError):
            # Empty image area is the requested fallback for absent or inaccessible article images.
            return ImageResult()
