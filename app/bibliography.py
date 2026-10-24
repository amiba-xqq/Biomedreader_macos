"""Retrieve missing citation fields by stable identifier and retain them offline."""
from datetime import datetime, timezone
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

from literature import LiteratureClient, NCBI_EFETCH_URL, NCBI_ESEARCH_URL
from network_transport import refresh_error_message


def enrich_articles(database, articles, client=None, force_missing=False):
    """Reuse complete cached records; explicit copy requests retry incomplete records now."""
    own_client=client is None
    client=client or LiteratureClient('')
    result=[];pending=[];warnings=[]
    for article in articles:
        row=dict(article);stored=database.article_record(row['article_key'])
        for field in ('pmid','doi'):
            if not row.get(field) and stored.get(field):row[field]=stored[field]
        for field in ('volume','issue','pages'):
            if stored.get(field):row[field]=stored[field]
        result.append(row)
        if row.get('volume') and row.get('pages'):continue
        if not force_missing:
            if str(row.get('doi') or '').startswith('10.1101/') or (
                any(s in str(row.get('source_name')) for s in ('bioRxiv','medRxiv')) and not row.get('pmid')):continue
            try:
                checked=datetime.fromisoformat(stored.get('bibliography_checked_at') or '')
                if (datetime.now(timezone.utc)-checked).total_seconds()<86400:continue
            except (ValueError,TypeError):pass
        pending.append(row)
    try:
        for start in range(0,len(pending),100):
            batch=pending[start:start+100];pmids=[str(a['pmid']) for a in batch if a.get('pmid')]
            records={}
            if pmids:
                try:
                    response=client.pubmed_request(NCBI_EFETCH_URL,dict(db='pubmed',id=','.join(dict.fromkeys(pmids)),retmode='xml',tool='BiomedReader'))
                    response.raise_for_status()
                    for node in ET.fromstring(response.content).findall('./PubmedArticle'):
                        record,_=client.normalize_pubmed(node,'')
                        records[record['pmid']]=record
                    time.sleep(0.36)
                except Exception as error:
                    warnings.append(refresh_error_message(error))
            for row in batch:
                metadata=records.get(str(row.get('pmid') or ''))
                if metadata is None and row.get('doi') and not row.get('pmid'):
                    try:
                        doi=str(row['doi']).strip()
                        response=client.pubmed_request(NCBI_ESEARCH_URL,dict(db='pubmed',term='"'+doi+'"[AID]',retmode='json',retmax=2,tool='BiomedReader'))
                        response.raise_for_status();ids=response.json().get('esearchresult',{}).get('idlist',[]);time.sleep(0.36)
                        if ids:
                            fetched=client.pubmed_request(NCBI_EFETCH_URL,dict(db='pubmed',id=','.join(ids),retmode='xml',tool='BiomedReader'))
                            fetched.raise_for_status()
                            for node in ET.fromstring(fetched.content).findall('./PubmedArticle'):
                                candidate,_=client.normalize_pubmed(node,'')
                                if candidate.get('doi','').lower()==doi.lower():metadata=candidate;break
                            time.sleep(0.36)
                        if metadata is None:
                            # DOI metadata also covers old Europe PMC/imported records.
                            crossref=client.session.get('https://api.crossref.org/works/'+quote(doi,safe=''),timeout=(8,15))
                            crossref.raise_for_status();work=crossref.json()['message']
                            metadata={field:str(work.get(key) or '') for field,key in
                                (('volume','volume'),('issue','issue'),('pages','page'))}
                            if not metadata['pages']:metadata['pages']=str(work.get('article-number') or '')
                    except Exception as error:
                        warnings.append(refresh_error_message(error))
                if metadata is not None:
                    if row.get('doi') and not (metadata.get('volume') and metadata.get('pages')):
                        try:
                            crossref=client.session.get('https://api.crossref.org/works/'+quote(str(row['doi']),safe=''),timeout=(5,12))
                            crossref.raise_for_status();work=crossref.json()['message']
                            for field,key in (('volume','volume'),('issue','issue'),('pages','page')):
                                if not metadata.get(field) and work.get(key):metadata[field]=str(work[key])
                            if not metadata.get('pages') and work.get('article-number'):metadata['pages']=str(work['article-number'])
                        except Exception as error:warnings.append(refresh_error_message(error))
                    database.save_bibliography(row['article_key'],metadata)
                    for field in ('volume','issue','pages'):
                        if metadata.get(field):row[field]=metadata[field]
    finally:
        if own_client:client.session.close()
    return result,list(dict.fromkeys(warnings))
