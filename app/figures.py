"""Publisher connectors for explicitly captioned models and graphical abstracts.

Only publisher-hosted public HTML/XML and Europe PMC open full text are used. Login walls,
missing figures and rate limits return text-only status, never a stock/guessed image.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

import requests

GRAPHIC = re.compile(r"graphical[\s_-]*abstract|graphical[\s_-]*summary", re.I)
MODEL = re.compile(r"(?:proposed|working|schematic|mechanistic|conceptual|summary) model|model (?:for|of|depicting|illustrating)|模型图", re.I)
NOT_MODEL_DIAGRAM = re.compile(r"confusion matrix|roc curve|accuracy of|performance (?:of|comparison)|survival curve",re.I)


@dataclass
class FigureResult:
    path: str = ""
    source_url: str = ""
    caption: str = ""
    status: str = "未找到可公开获取的图文摘要或模型图，显示文字摘要。"


def safe_url(url: str) -> bool:
    # Reject non-web and local/private links found in external documents.
    import ipaddress
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or host.lower() == "localhost":
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host and not host.endswith((".local", ".internal"))


class FigureHTML(HTMLParser):
    """Keep figure regions only, so cover images and advertising never become abstracts."""
    def __init__(self, base):
        super().__init__()
        self.base, self.frames, self.found = base, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "figure" or (tag in ("div", "section") and
                re.search(r"graphical.?abstract|abstract.?graphical|\bfigure\b|\bfig\b", str(attrs), re.I)):
            self.frames.append(dict(tag=tag, depth=0, text=[str(attrs)], urls=[]))
        for frame in self.frames:
            frame["depth"] += 1
            if tag == "img":
                url = attrs.get("data-src") or attrs.get("data-original") or attrs.get("src") or ""
                frame["urls"].append(urljoin(self.base,url))
                frame["text"].append(attrs.get("alt", ""))
        if tag in ("img", "br", "hr", "meta", "link", "input", "source"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for frame in list(self.frames):
            frame["depth"] -= 1
            if frame["depth"] <= 0:
                caption = " ".join(frame["text"])
                if (MODEL.search(caption) and not NOT_MODEL_DIAGRAM.search(caption)) or GRAPHIC.search(caption):
                    for url in frame["urls"]:
                        if safe_url(url):
                            self.found.append((0 if MODEL.search(caption) else 1, url,caption))
                self.frames.remove(frame)

    def handle_data(self, text):
        for frame in self.frames:
            frame["text"].append(text)


class PublisherConnector:
    name = "Publisher"
    prefixes: tuple[str, ...] = ()

    def matches(self, doi):
        return any(doi.lower().startswith(prefix) for prefix in self.prefixes)

    def public_sources(self, doi):
        return [("html", "https://doi.org/" + quote(doi,safe="/()"))]

    def extract_html(self, content, base):
        parser = FigureHTML(base)
        parser.feed(content)
        return sorted(parser.found, key=lambda row: row[0])

    def extract_xml(self, content, pmcid):
        root = ET.fromstring(content)
        candidates = []
        for element in root.iter():
            # Both normal figures and the JATS graphical abstract element are supported.
            local = element.tag.split("}")[-1]
            if local not in ("fig", "abstract"):
                continue
            caption = " ".join(element.itertext())
            graphic = element.attrib.get("abstract-type", "") == "graphical" or GRAPHIC.search(caption)
            model = MODEL.search(caption) and not NOT_MODEL_DIAGRAM.search(caption)
            if not (graphic or model):
                continue
            for child in element.iter():
                if child.tag.split("}")[-1] != "graphic":
                    continue
                href = child.attrib.get("{http://www.w3.org/1999/xlink}href", "")
                if not href:
                    continue
                if href.startswith("https://"):
                    url = href
                else:
                    # Europe PMC supplementaryFiles is used to resolve relative asset names;
                    # never invent publisher CDN paths from a DOI.
                    url = "asset:" + href
                candidates.append((0 if model else 1, url, caption))
        return sorted(candidates,key=lambda row:row[0])


class ElsevierConnector(PublisherConnector):
    name, prefixes = "Elsevier / Cell Press", ("10.1016/",)

class SpringerNatureConnector(PublisherConnector):
    name, prefixes = "Springer Nature", ("10.1038/", "10.1007/", "10.1186/")

class WileyConnector(PublisherConnector):
    name, prefixes = "Wiley", ("10.1002/", "10.1111/")

    def public_sources(self, doi):
        return [("html","https://onlinelibrary.wiley.com/doi/full/"+quote(doi,safe="/()"))]

class PLOSConnector(PublisherConnector):
    name, prefixes = "PLOS", ("10.1371/",)

    def journal_path(self, doi):
        code=doi.split("journal.")[-1].split(".")[0]
        return {"pone":"plosone","pbio":"plosbiology","pgen":"plosgenetics",
                "ppat":"plospathogens","pcbi":"ploscompbiol","pmed":"plosmedicine",
                "pntd":"plosntds"}.get(code)

    def public_sources(self, doi):
        journal=self.journal_path(doi)
        if journal:
            return [("xml",f"https://journals.plos.org/{journal}/article/file?id={quote(doi,safe='')}&type=manuscript")]
        return super().public_sources(doi)

    def extract_xml(self, content, pmcid):
        candidates=super().extract_xml(content,pmcid)
        resolved=[]
        for priority,url,caption in candidates:
            if url.startswith("asset:info:doi/"):
                identifier=url[len("asset:info:doi/"):]
                journal=self.journal_path(identifier)
                if journal:
                    url=f"https://journals.plos.org/{journal}/article/figure/image?size=large&id={quote(identifier,safe='')}"
            resolved.append((priority,url,caption))
        return resolved

class ELifeConnector(PublisherConnector):
    name, prefixes = "eLife", ("10.7554/",)

    def public_sources(self, doi):
        match=re.search(r"elife\.(\d+)",doi,re.I)
        return [("html",f"https://elifesciences.org/articles/{match[1]}")] if match else super().public_sources(doi)

class FrontiersConnector(PublisherConnector):
    name, prefixes = "Frontiers", ("10.3389/",)

    def public_sources(self, doi):
        return [("html","https://www.frontiersin.org/articles/"+quote(doi,safe="/")+"/full")]

class MDPIConnector(PublisherConnector):
    name, prefixes = "MDPI", ("10.3390/",)

CONNECTORS = [ElsevierConnector(), SpringerNatureConnector(), WileyConnector(), PLOSConnector(),
              ELifeConnector(), FrontiersConnector(), MDPIConnector()]


class FigureService:
    def __init__(self, cache: Path):
        self.cache = cache
        cache.mkdir(parents=True,exist_ok=True)
        self.session = requests.Session()
        self.session.max_redirects = 4
        self.session.headers["User-Agent"] = "BiomedReader/1.7.0"

    def get(self, url):
        if not safe_url(url):
            raise ValueError("图像来源地址不可用")
        response = self.session.get(url,timeout=(8,25),stream=True)
        try:
            response.raise_for_status()
            if int(response.headers.get("Content-Length") or 0) > 20_000_000:
                raise ValueError("图像或文档过大，保留文字摘要")
            chunks,size=[],0
            deadline = time.monotonic()+45
            # Enforce the budget during transfer: Cell supplementary ZIPs may contain large
            # videos. Never download a whole archive merely to discover it exceeds the limit.
            for chunk in (response.iter_content(65536) if response.raw is not None else [response.content]):
                size += len(chunk)
                if size > 20_000_000 or time.monotonic()>deadline:
                    raise ValueError("图像或文档过大，保留文字摘要")
                chunks.append(chunk)
            response._content = b"".join(chunks)
            response._content_consumed = True
        finally:
            response.close()
        return response

    def find(self, article: dict, online=True) -> FigureResult:
        key = hashlib.sha256(str(article["article_key"]).encode()).hexdigest()
        import json
        meta = self.cache / (key + ".json")
        if meta.exists():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if Path(saved.get("path") or "nonexistent").is_file():
                return FigureResult(**saved)
            if not online or time.time() - meta.stat().st_mtime < 3600:
                return FigureResult(status=saved.get("status", "本地没有图像，显示文字摘要。"))
        if not online:
            return FigureResult(status="本地没有缓存图像，显示文字摘要。")
        doi = str(article.get("doi") or "")
        connector = next((c for c in CONNECTORS if c.matches(doi)), PublisherConnector())
        candidates, pmcid, failures = [], str(article.get("pmcid") or ""), []
        archive = None
        if re.fullmatch(r"PMC\d+",pmcid):
            try:
                base = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}"
                xml = self.get(base+"/fullTextXML")
                candidates = connector.extract_xml(xml.content,pmcid)
                if any(url.startswith("asset:") for _,url,_ in candidates):
                    import io,zipfile
                    archive = zipfile.ZipFile(io.BytesIO(self.get(base+"/supplementaryFiles").content))
            except Exception as error:  # An unavailable XML/ZIP source must not hide the abstract.
                failures.append(type(error).__name__)
        # XML can describe a figure whose asset is unavailable in the OA ZIP. The publisher's
        # public page is still worth trying; a failed ZIP must not suppress this independent path.
        if (not candidates or archive is None) and doi:
            for kind,url in connector.public_sources(doi):
                try:
                    response = self.get(url)
                    if kind == "xml":
                        candidates.extend(connector.extract_xml(response.content,pmcid))
                    else:
                        candidates.extend(connector.extract_html(response.text,response.url))
                except (requests.RequestException,ValueError,ET.ParseError) as error:
                    failures.append(type(error).__name__)
        candidates.sort(key=lambda candidate:candidate[0])
        result = FigureResult()
        for _,url,caption in candidates:
            try:
                if url.startswith("asset:"):
                    if archive is None:
                        continue
                    asset = url[6:]
                    matches = [i for i in archive.infolist() if
                               Path(i.filename).stem == Path(asset).stem and
                               Path(i.filename).suffix.lower() in (".jpg",".jpeg",".png",".gif") and
                               i.file_size < 20_000_000]
                    if not matches:
                        continue
                    data = archive.read(matches[0])
                    source = f"https://europepmc.org/articles/{pmcid}"
                else:
                    response = self.get(url)
                    if not response.headers.get("Content-Type", "").startswith("image/"):
                        continue
                    data, source = response.content, url
                # Qt verifies decoding before showing. Invalid images never replace the abstract.
                from PySide6.QtGui import QImage
                if QImage.fromData(data).isNull():
                    continue
                path = self.cache / (key + ".image")
                path.write_bytes(data)
                result = FigureResult(str(path), source,caption[:2000], f"{connector.name} · 已缓存，可离线查看")
                break
            except (requests.RequestException,ValueError,KeyError):
                continue
        if not result.path and failures:
            result.status = "图像接口暂不可用、受限或无公开权限，显示文字摘要。"
        from dataclasses import asdict
        meta.write_text(json.dumps(asdict(result),ensure_ascii=False),encoding="utf-8")
        if archive is not None:
            archive.close()
        return result
