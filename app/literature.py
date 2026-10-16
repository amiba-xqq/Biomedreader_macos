from __future__ import annotations

import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from article_types import type_clause
from biorxiv import plain_text
from biorxiv_website import search_website
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date


NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


@dataclass
class SearchReport:
    candidates: list[tuple[dict[str, Any], float]] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=lambda: {"bioRxiv": 0, "PubMed": 0})
    request_counts: dict[str, int] = field(default_factory=lambda: {"bioRxiv": 0, "PubMed": 0})
    source_errors: list[str] = field(default_factory=list)
    selected_sources: tuple[str, ...] = ("bioRxiv", "PubMed")
    incremental: bool = False
    new_count: int = 0
    reused_count: int = 0


class LiteratureClient:
    """Retrieve PubMed journal articles and bioRxiv preprint metadata.

    Journal ISSNs constrain PubMed only. bioRxiv uses posted dates and local title/abstract matching. The merged records are rescored locally so cached results retain a stable
    order even if an upstream search engine changes its ranking algorithm.
    """

    def __init__(self, contact_email: str, ncbi_api_key: str = "", biorxiv_cache_path=None):
        self.biorxiv_cache_path = Path(biorxiv_cache_path) if biorxiv_cache_path else None
        self.completed_website_jobs = []
        self.contact_email = contact_email.strip() or "anonymous@example.com"
        self.ncbi_api_key = ncbi_api_key.strip()
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(total=2,backoff_factor=0.5,
            status_forcelist=(429,500,502,503,504),allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True)))
        self.session.headers.update(
            {"User-Agent": f"BiomedReader/1.9.13 ({self.contact_email})"}
        )

    @staticmethod
    def local_relevance(title: str, abstract: str, query_expression: str) -> float:
        """Score term and phrase matches with higher weight for titles than abstracts."""
        quoted_phrases = [phrase.lower().strip() for phrase in re.findall(r'"([^"]+)"', query_expression)]
        plain_tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+\-]{1,}", query_expression)
            if token.upper() not in {
                "AND", "OR", "NOT", "TITLE", "ABSTRACT", "JOURNAL", "ISSN",
            }
        ]
        title_lower = title.lower()
        abstract_lower = abstract.lower()
        score = 0.0
        for token in set(plain_tokens):
            score += 4.0 * title_lower.count(token)
            score += 1.0 * min(abstract_lower.count(token), 5)
        for phrase in set(quoted_phrases):
            score += 8.0 if phrase in title_lower else 0.0
            score += 3.0 if phrase in abstract_lower else 0.0
        return score

    def normalize_biorxiv(self, raw, query_expression):
        """A preprint DOI remains separate from its later published journal DOI."""
        doi = str(raw.get("doi") or "").strip().lower()
        title, abstract = plain_text(raw.get("title")) or "无标题", plain_text(raw.get("abstract"))
        posted = str(raw.get("date") or "")
        article = dict(article_key="doi:" + doi, doi=doi, pmid="", pmcid="",
                       title=title, abstract=abstract, authors=str(raw.get("authors") or ""),
                       journal_title="bioRxiv · 未经同行评审的预印本", issn="",
                       publication_date=posted, publication_year=int(posted[:4]) if posted[:4].isdigit() else None,
                       cited_by_count=0, is_open_access=1, landing_page_url="https://doi.org/" + doi,
                       graphical_abstract_url="", source_name="bioRxiv", publication_types=["Preprint"],
                       preprint_version=str(raw.get("version") or "1"), category=str(raw.get("category") or ""))
        article['preprint_server']=raw.get('preprint_server','biorxiv')
        if article['preprint_server']=='medrxiv':article.update(source_name='medRxiv',journal_title='medRxiv · 未经同行评审的预印本')
        return article, self.local_relevance(title, abstract, query_expression)

    def normalize_pubmed(self, element: ET.Element, query_expression: str) -> tuple[dict[str, Any], float]:
        article_element = element.find("./MedlineCitation/Article")
        if article_element is None:
            article_element = ET.Element("Article")
        title_element = article_element.find("ArticleTitle")
        title = "".join(title_element.itertext()).strip() if title_element is not None else "无标题"

        abstract_parts = []
        for abstract_element in article_element.findall("./Abstract/AbstractText"):
            paragraph = "".join(abstract_element.itertext()).strip()
            label = str(abstract_element.attrib.get("Label") or "").strip()
            if paragraph:
                abstract_parts.append(f"{label}: {paragraph}" if label else paragraph)
        abstract = "\n\n".join(abstract_parts)

        authors = []
        for author in article_element.findall("./AuthorList/Author"):
            collective = str(author.findtext("CollectiveName") or "").strip()
            if collective:
                authors.append(collective)
                continue
            last_name = str(author.findtext("LastName") or "").strip()
            fore_name = str(author.findtext("ForeName") or author.findtext("Initials") or "").strip()
            full_name = " ".join(part for part in (fore_name, last_name) if part)
            if full_name:
                authors.append(full_name)

        identifiers = {
            str(item.attrib.get("IdType") or "").lower(): str(item.text or "").strip()
            for item in element.findall("./PubmedData/ArticleIdList/ArticleId")
            if item.attrib.get("IdType") and item.text
        }
        pmid = str(element.findtext("./MedlineCitation/PMID") or "").strip()
        doi = identifiers.get("doi", "").lower()
        pmcid = identifiers.get("pmc", "")
        journal = str(article_element.findtext("./Journal/Title") or "").strip()
        issn = str(article_element.findtext("./Journal/ISSN") or "").strip().upper()

        date_element = article_element.find("ArticleDate")
        if date_element is None:
            date_element = article_element.find("./Journal/JournalIssue/PubDate")
        year = month = day = medline_date = ""
        if date_element is not None:
            year = str(date_element.findtext("Year") or "").strip()
            month = str(date_element.findtext("Month") or "").strip()
            day = str(date_element.findtext("Day") or "").strip()
            medline_date = str(date_element.findtext("MedlineDate") or "").strip()
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        if year.isdigit():
            month_number = max(1, min(int(month), 12)) if month.isdigit() else month_map.get(month[:3].lower(), 1)
            day_number = max(1, min(int(day), 31)) if day.isdigit() else 1
            publication_year = int(year)
            publication_date = f"{publication_year:04d}-{month_number:02d}-{day_number:02d}"
        else:
            match = re.search(r"(?:19|20)\d{2}", medline_date)
            publication_year = int(match.group()) if match else None
            publication_date = f"{publication_year:04d}-01-01" if publication_year else ""

        fallback = "|".join((title, journal, publication_date))
        article_key = (
            "doi:" + doi if doi else
            "pmid:" + pmid if pmid else
            "fallback:" + hashlib.sha1(fallback.encode("utf-8")).hexdigest()
        )
        article = {
            "article_key": article_key,
            "doi": doi,
            "pmid": pmid,
            "pmcid": pmcid,
            "title": title,
            "abstract": abstract,
            "authors": ", ".join(authors),
            "journal_title": journal,
            "issn": issn,
            "publication_date": publication_date,
            "publication_year": publication_year,
            "cited_by_count": 0,
            "is_open_access": 0,
            "landing_page_url": (
                "https://doi.org/" + quote(doi, safe="/()") if doi
                else f"https://pubmed.ncbi.nlm.nih.gov/{quote(pmid)}/" if pmid else ""
            ),
            "graphical_abstract_url": "",
            "source_name": "PubMed",
            "publication_types": [str(item.text or "") for item in article_element.findall("./PublicationTypeList/PublicationType")],
        }
        return article, self.local_relevance(title, abstract, query_expression)

    def search(
        self,
        query_expression: str,
        journal_issns: list[str],
        maximum_results: int,
        progress: Callable[[str], None] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        cancelled: Callable[[], bool] | None = None,
        sources: tuple[str, ...] | None = None,
        article_type: str = "all",
        biorxiv_years: int = 2,
        updated_since: date | None = None,
        biorxiv_search_method: str = 'website',
        website_fetch=None,
        pubmed_maximum_results: int | None = None,
        biorxiv_maximum_results: int | None = None,
        biorxiv_website_scope: str = 'fulltext',
        preprint_servers: str = 'biorxiv',
        website_order: str = 'newest',
        pubmed_search_scope: str = 'default',
        candidate_callback=None,
    ) -> SearchReport:
        """Query only selected sources, merging candidates without touching SQLite or images."""
        report = SearchReport()
        selected_sources = set(sources if sources is not None else ("bioRxiv", "PubMed"))
        if not selected_sources or not selected_sources.issubset({"bioRxiv", "PubMed"}):
            raise ValueError("请选择 bioRxiv、PubMed 或同时使用两者。")
        report.selected_sources = tuple(s for s in ("bioRxiv", "PubMed") if s in selected_sources)
        collected: dict[str, tuple[dict[str, Any], float]] = {}
        doi_index: dict[str, str] = {}
        pmid_index: dict[str, str] = {}
        batches = [journal_issns[index:index + 80] for index in range(0, len(journal_issns), 80)]
        if not batches and selected_sources == {"PubMed"}:
            raise ValueError("当前检索方案没有选择期刊。")
        legacy_budget=(maximum_results+1)//2 if maximum_results and len(selected_sources)==2 else maximum_results
        budgets={'PubMed':legacy_budget if pubmed_maximum_results is None else int(pubmed_maximum_results),
                 'bioRxiv':legacy_budget if biorxiv_maximum_results is None else int(biorxiv_maximum_results)}

        def notify(message: str) -> None:
            if cancelled and cancelled():
                raise InterruptedError("检索已取消，保留上次完整缓存。")
            if progress:
                progress(message)

        def merge_candidate(article: dict[str, Any], relevance: float) -> None:
            existing_key = ""
            if article.get("doi"):
                existing_key = doi_index.get(article["doi"], "")
            if not existing_key and article.get("pmid"):
                existing_key = pmid_index.get(article["pmid"], "")
            if not existing_key:
                existing_key = article["article_key"] if article["article_key"] in collected else ""

            if not existing_key:
                collected[article["article_key"]] = (article, relevance)
                if article.get("doi"):
                    doi_index[article["doi"]] = article["article_key"]
                if article.get("pmid"):
                    pmid_index[article["pmid"]] = article["article_key"]
                return

            existing, old_relevance = collected[existing_key]
            preferred_key = "doi:" + article["doi"] if article.get("doi") else existing_key
            if len(article.get("abstract") or "") > len(existing.get("abstract") or ""):
                existing["abstract"] = article["abstract"]
            for field_name in (
                "doi", "pmid", "pmcid", "authors", "journal_title", "issn",
                "publication_date", "publication_year", "landing_page_url", "graphical_abstract_url",
            ):
                if not existing.get(field_name) and article.get(field_name):
                    existing[field_name] = article[field_name]
            existing["cited_by_count"] = max(
                int(existing.get("cited_by_count") or 0), int(article.get("cited_by_count") or 0)
            )
            existing["is_open_access"] = max(
                int(existing.get("is_open_access") or 0), int(article.get("is_open_access") or 0)
            )
            sources = {
                part.strip()
                for value in (existing.get("source_name"), article.get("source_name"))
                for part in str(value or "").split("+") if part.strip()
            }
            existing["source_name"] = " + ".join(sorted(sources))
            existing["publication_types"] = sorted(set(existing.get("publication_types") or []) | set(article.get("publication_types") or []))
            existing["article_key"] = preferred_key
            if preferred_key != existing_key:
                del collected[existing_key]
            collected[preferred_key] = (existing, max(old_relevance, relevance))
            if existing.get("doi"):
                doi_index[existing["doi"]] = preferred_key
            if existing.get("pmid"):
                pmid_index[existing["pmid"]] = preferred_key

        if len(selected_sources) == 2:
            # Independent sessions and source-local reports make both endpoints progress
            # concurrently. SQLite cache writes belong only to the bioRxiv worker.
            bio_client = LiteratureClient(self.contact_email, self.ncbi_api_key, self.biorxiv_cache_path)
            bio_client.completed_website_jobs = self.completed_website_jobs
            try:
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="literature") as executor:
                    jobs = {executor.submit(client.search, query_expression, journal_issns, budgets[source],
                                progress, year_from, year_to, cancelled, (source,), article_type,
                                biorxiv_years, updated_since, biorxiv_search_method, website_fetch,
                                biorxiv_website_scope=biorxiv_website_scope,
                                preprint_servers=preprint_servers,website_order=website_order,
                                pubmed_search_scope=pubmed_search_scope,candidate_callback=candidate_callback): source
                            for source, client in (("PubMed", self), ("bioRxiv", bio_client))}
                    for job in as_completed(jobs):
                        source = jobs[job]
                        notify(f"{source} 检索已返回，正在合并……")
                        try:
                            partial = job.result()
                            report.source_counts[source] = partial.source_counts[source]
                            report.request_counts[source] = partial.request_counts[source]
                            report.source_errors.extend(partial.source_errors)
                            for article, relevance in partial.candidates:
                                merge_candidate(article, relevance)
                        except (requests.RequestException, ValueError) as error:
                            report.source_errors.append(f"{source}：{error}")
            finally:
                bio_client.session.close()
            report.candidates = list(collected.values())
            notify(f"已合并为 {len(report.candidates)} 篇文章。")
            return report

        maximum_results=budgets[next(iter(selected_sources))]
        stream_count=max(1,2*len(batches))
        per_stream_limit=max(1,(maximum_results+stream_count-1)//stream_count) if maximum_results else None

        if "bioRxiv" in selected_sources:
            try:
                limit = (maximum_results + len(selected_sources) - 1) // len(selected_sources) if maximum_results else None
                def requested():
                    report.request_counts["bioRxiv"] += 1
                if biorxiv_search_method=='website':
                    def publish_rows(rows):
                        if not candidate_callback:return
                        batch=[]
                        for raw in rows:
                            article,relevance=self.normalize_biorxiv(raw,query_expression)
                            server=raw.get('preprint_server','biorxiv')
                            article.update(biorxiv_search_method='website',biorxiv_website_scope=biorxiv_website_scope,
                                website_date_filtered=True,preprint_version=raw.get('version') or '',
                                preprint_server=server,source_name='medRxiv' if server=='medrxiv' else 'bioRxiv',
                                journal_title='medRxiv' if server=='medrxiv' else 'bioRxiv',
                                website_rank=raw.get('website_rank',0),landing_page_url='https://doi.org/'+article['doi'])
                            if website_order=='relevance':relevance=10/(1+raw.get('website_rank',0))
                            batch.append((article,relevance))
                        candidate_callback(batch)
                    records=search_website(self.session,query_expression,year_from,year_to,biorxiv_years,
                                           notify,requested=requested,fetch=website_fetch,
                                           start_date=updated_since,limit=maximum_results,scope=biorxiv_website_scope,
                                           servers=preprint_servers,ordering=website_order,
                                           cache_path=Path(self.biorxiv_cache_path).with_name('website_progress.sqlite3') if self.biorxiv_cache_path else None,
                                           cancelled=cancelled,progress_jobs=self.completed_website_jobs,
                                           **({'rows_callback':publish_rows} if candidate_callback else {}))
                else:
                    raise ValueError('预印本仅支持官网关键词搜索。')
                for raw in records:
                    article, relevance = self.normalize_biorxiv(raw, query_expression)
                    if biorxiv_search_method=='website':
                        article.update(biorxiv_search_method='website',biorxiv_website_scope=biorxiv_website_scope,website_date_filtered=True,
                                       preprint_version=raw.get('version') or '',landing_page_url='https://doi.org/'+article['doi'])
                        server=raw.get('preprint_server','biorxiv')
                        article.update(preprint_server=server,source_name='medRxiv' if server=='medrxiv' else 'bioRxiv',
                                       journal_title='medRxiv' if server=='medrxiv' else 'bioRxiv',website_rank=raw.get('website_rank',0))
                        if website_order=='relevance':relevance=10/(1+raw.get('website_rank',0))
                    merge_candidate(article, relevance)
                report.source_counts["bioRxiv"] = len(records)
            except (requests.RequestException, ValueError) as error:
                label={'biorxiv':'bioRxiv','medrxiv':'medRxiv','both':'bioRxiv / medRxiv'}.get(preprint_servers,'预印本')
                report.source_errors.append(f"{label}：{error}")

        if "PubMed" in selected_sources:
            notify("正在查询 PubMed……")
            if not batches:
                report.source_errors.append("PubMed：所选期刊目录或最低 JIF 条件下没有可检索期刊；bioRxiv 不受此限制。")
        ncbi_delay = 0.12 if self.ncbi_api_key else 0.36
        fetched_pmids = set()
        for journal_batch in (batches if "PubMed" in selected_sources else []):
            journal_clause = " OR ".join(f'"{issn}"[Journal]' for issn in journal_batch)
            if pubmed_search_scope not in ('default','title_abstract'):
                raise ValueError('PubMed 仅支持默认或标题与摘要搜索范围。')
            scoped_query=query_expression
            if pubmed_search_scope=='title_abstract':
                if '[' in query_expression or ']' in query_expression:
                    raise ValueError('标题与摘要范围下请移除关键词中的 PubMed 字段标签，或改用默认范围。')
                tokens=re.findall(r'"[^"]*"|[()]|[^\s()]+',query_expression)
                scoped_query=' '.join(token.upper() if token.upper() in ('AND','OR','NOT') else
                    token if token in ('(',')') else token+'[Title/Abstract]' for token in tokens)
            pubmed_query = f"({scoped_query}) AND ({journal_clause})"
            if type_clause(article_type, "PubMed"):
                pubmed_query += " AND (" + type_clause(article_type, "PubMed") + ")"
            if year_from or year_to:
                pubmed_query += f' AND ("{year_from or 1000}/01/01"[Date - Publication] : "{year_to or 3000}/12/31"[Date - Publication])'
            if updated_since:
                pubmed_query += f' AND ("{updated_since:%Y/%m/%d}"[Date - Publication] : "{date.today():%Y/%m/%d}"[Date - Publication])'
                notify(f'PubMed 增量检索：发表日期 {updated_since} 至 {date.today()}，合并已有缓存。')
            for ordering in (("relevance", "pub_date") if maximum_results else ("pub_date",)):
                try:
                    pmids = self.pubmed_ids(pubmed_query, ordering, per_stream_limit,
                                            report, notify, ncbi_delay,
                                            **({'date_from':updated_since,'date_to':date.today()} if updated_since else {}))
                    pmids = [pmid for pmid in dict.fromkeys(pmids) if pmid not in fetched_pmids]

                    for start in range(0, len(pmids), 100):
                        notify(f"PubMed：已取得 {report.source_counts['PubMed']} 条记录……")
                        fetch_params = {
                            "db": "pubmed", "id": ",".join(pmids[start:start + 100]),
                            "retmode": "xml", "tool": "BiomedReader",
                            "email": self.contact_email,
                        }
                        if self.ncbi_api_key:
                            fetch_params["api_key"] = self.ncbi_api_key
                        response = self.session.get(NCBI_EFETCH_URL, params=fetch_params, timeout=(10, 60))
                        report.request_counts["PubMed"] += 1
                        response.raise_for_status()
                        root = ET.fromstring(response.content)
                        received=[]
                        for element in root.findall("./PubmedArticle"):
                            article, relevance = self.normalize_pubmed(element, query_expression)
                            merge_candidate(article, relevance)
                            received.append((article,relevance))
                            report.source_counts["PubMed"] += 1
                        fetched_pmids.update(pmids[start:start + 100])
                        if candidate_callback and received:candidate_callback(received)
                        time.sleep(ncbi_delay)
                except (requests.RequestException, ValueError, json.JSONDecodeError, ET.ParseError) as error:
                    report.source_errors.append(f"PubMed（{ordering}）：{error}")

        report.candidates = list(collected.values())
        # Resolve exact DOI metadata only for genuinely missing journal names.
        for article, _ in report.candidates:
            if not article.get("journal_title") and article.get("doi"):
                notify("正在补全缺失的期刊名称……")
                try:
                    response = self.session.get("https://api.crossref.org/works/"+
                        quote(article["doi"],safe=""),timeout=(8,20))
                    response.raise_for_status()
                    titles = response.json().get("message",{}).get("container-title",[])
                    if titles:
                        article["journal_title"] = str(titles[0])
                    time.sleep(0.1)
                except (requests.RequestException,ValueError):
                    pass
        notify(f"已合并为 {len(report.candidates)} 篇文章。")
        return report

    def pubmed_ids(self, query, ordering, limit, report, notify, delay,date_from=None,date_to=None):
        """Split dense result sets by publication date to respect PubMed's 9,999 ID boundary.

        ESearch cannot return arbitrary numbers of PubMed IDs with retstart alone. Date intervals
        are bisected until each fits. A single day over the server boundary is reported explicitly.
        """
        from datetime import date, timedelta
        def retrieve(term, count):
            params = dict(db="pubmed",term=term,retmode="json",retmax=count,sort=ordering,
                          tool="BiomedReader", email=self.contact_email)
            if date_from is not None:
                params.update(datetype='pdat',mindate=date_from.strftime('%Y/%m/%d'),
                              maxdate=(date_to or date.today()).strftime('%Y/%m/%d'))
            if self.ncbi_api_key:
                params["api_key"] = self.ncbi_api_key
            response = self.session.get(NCBI_ESEARCH_URL, params=params, timeout=(10,60))
            response.raise_for_status()
            report.request_counts["PubMed"] += 1
            time.sleep(delay)
            payload = response.json()
            if payload.get("error"):
                raise ValueError(str(payload["error"]))
            return payload.get("esearchresult", {})
        first = retrieve(query, min(limit, 9999) if limit else 0)
        if int(first.get("count",0)) <= 9999 or (limit and limit <= 9999):
            if not limit and int(first.get("count",0)):
                return retrieve(query,int(first["count"])).get("idlist",[])
            return first.get("idlist",[])
        pending = [(date_from or date(1000,1,1), date_to or date.today().replace(year=date.today().year+2))]
        identifiers = set()
        while pending:
            start, end = pending.pop()
            notify(f"PubMed 大结果集分页：{start}—{end}，已取得 {len(identifiers)} 个 ID")
            term = f'({query}) AND ("{start:%Y/%m/%d}"[dp]:"{end:%Y/%m/%d}"[dp])'
            payload = retrieve(term,9999)
            if int(payload.get("count",0)) > 9999:
                if start == end:
                    raise ValueError(f"PubMed 在 {start} 单日结果超过 9999，需进一步细化关键词；未静默截断。")
                mid = start + (end-start)//2
                pending.extend([(start,mid),(mid+timedelta(days=1),end)])
            else:
                identifiers.update(payload.get("idlist",[]))
        return sorted(identifiers)

