"""Verified HTTPS transport and bounded, human-readable refresh failures."""
from __future__ import annotations

import re
import ssl
import threading
import traceback
from pathlib import Path

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_diagnostic_lock = threading.Lock()


def write_network_diagnostic(error, directory):
    """Keep the full exception locally, without allowing diagnostics to abort a search."""
    try:
        with _diagnostic_lock:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / 'refresh-errors.log'
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(directory / 'refresh-errors.previous.log')
            from datetime import datetime
            with path.open('a', encoding='utf-8') as stream:
                stream.write('\n' + datetime.now().isoformat(timespec='seconds') + '\n')
                stream.write(''.join(traceback.format_exception(error))[-65536:])
    except OSError:
        pass


class VerifiedHTTPSAdapter(HTTPAdapter):
    """Keep HTTPS independent of external TLS debugging log-file paths.

    urllib3 normally reads SSLKEYLOGFILE while opening each connection. If that
    variable points to a deleted directory, even a valid HTTPS request fails with
    FileNotFoundError. This application does not export TLS session secrets.
    Constructing SSLContext directly avoids that unrelated debugging dependency;
    certificate and hostname verification remain mandatory. Windows trust roots
    and the packaged certifi bundle are both loaded for normal and proxy TLS.
    """

    def __init__(self):
        self.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.tls_context.load_default_certs()
        self.tls_context.load_verify_locations(cafile=certifi.where())
        super().__init__(max_retries=Retry(
            total=2, backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            # NCBI ESearch/EFetch POST requests only read metadata and may retry.
            allowed_methods=frozenset(("GET", "POST")),
            respect_retry_after_header=True,
        ))

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs["ssl_context"] = self.tls_context
        return super().init_poolmanager(connections, maxsize, block, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs["ssl_context"] = self.tls_context
        kwargs["proxy_ssl_context"] = self.tls_context
        return super().proxy_manager_for(proxy, **kwargs)


def refresh_error_message(error) -> str:
    """Never put an entire URL, ISSN catalogue or repeated traceback in a dialog."""
    if isinstance(error, requests.exceptions.ProxyError):
        return "代理连接失败，请检查电脑的代理设置或代理程序是否正在运行。"
    if isinstance(error, requests.exceptions.SSLError):
        return "HTTPS 证书验证失败，请检查系统时间、网络代理和证书配置。"
    if isinstance(error, requests.exceptions.Timeout):
        return "连接或读取超时，请稍后重试；已取得的文章会保留在缓存中。"
    if isinstance(error, requests.exceptions.ConnectionError):
        if "FileNotFoundError" in str(error):
            return "HTTPS 连接依赖的本地文件不可用，请检查安装是否完整及代理配置。"
        return "无法连接检索服务器，请检查网络或代理，稍后重试。"
    if isinstance(error, requests.exceptions.HTTPError):
        code = getattr(getattr(error, "response", None), "status_code", None)
        return f"检索服务器返回 HTTP {code or '错误'}，请稍后重试。"
    message = re.sub(r"https?://[^\s]+", "[请求地址已省略]", str(error))
    return message if len(message) <= 500 else message[:497] + "…"


def run_network_self_test(destination: Path) -> int:
    """Exercise the real packaged TLS files, NCBI GET/POST and XML retrieval."""
    import json
    import os
    import time
    import uuid
    from datetime import date, timedelta
    from literature import LiteratureClient, NCBI_ESEARCH_URL, NCBI_EFETCH_URL
    previous = os.environ.get('SSLKEYLOGFILE')
    os.environ['SSLKEYLOGFILE'] = str(destination.parent / uuid.uuid4().hex / 'missing' / 'tls.log')
    result = {'version': '2.1', 'certificate_bundle_exists': Path(certifi.where()).is_file()}
    client = None
    try:
        client = LiteratureClient('')
        context = client.session.get_adapter('https://').tls_context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert context.keylog_filename is None
        result['certificate_verification'] = True
        result['invalid_tls_debug_path_ignored'] = True
        today = date.today()
        params = dict(db='pubmed', term='R-loop[Title/Abstract]', retmode='json', retmax=2,
                      datetype='pdat', mindate=str(today - timedelta(days=365)), maxdate=str(today),
                      tool='BiomedReader', sort='pub_date')
        response = client.pubmed_request(NCBI_ESEARCH_URL, params)
        response.raise_for_status()
        payload = response.json()
        assert 'esearchresult' in payload and 'error' not in payload
        ids = payload['esearchresult'].get('idlist', [])
        result['short_query_get'] = True
        time.sleep(0.4)
        params['term'] += ' AND (' + ' OR '.join('"0092-8674"[Journal]' for _ in range(65)) + ')'
        response = client.pubmed_request(NCBI_ESEARCH_URL, params)
        response.raise_for_status()
        assert 'esearchresult' in response.json() and 'error' not in response.json()
        result['long_query_post_with_daily_bounds'] = True
        if ids:
            time.sleep(0.4)
            response = client.pubmed_request(NCBI_EFETCH_URL, dict(db='pubmed', id=','.join(ids), retmode='xml'))
            response.raise_for_status()
            import xml.etree.ElementTree as ET
            assert ET.fromstring(response.content).find('PubmedArticle') is not None
            result['article_xml_fetch'] = True
        result['passed'] = True
        status = 0
    except Exception as error:
        result.update(passed=False, error=refresh_error_message(error), diagnostic=traceback.format_exc())
        status = 1
    finally:
        if client:
            client.session.close()
        if previous is None:
            os.environ.pop('SSLKEYLOGFILE', None)
        else:
            os.environ['SSLKEYLOGFILE'] = previous
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return status
