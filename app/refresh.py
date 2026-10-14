"""Refresh a saved search using an atomic, source-specific successful-search checkpoint."""
import hashlib
import json
from datetime import date, datetime, timezone
from biorxiv import rolling_start
from source_modes import cache_mode,profile_settings


def refresh_search(database, client, profile, settings, issns, progress, cancelled, force_full=False,website_fetch=None,candidate_callback=None):
    """Keep the baseline candidates, fetch all recent changes, and merge by stable identifiers.

    The checkpoint records completion of the last successful search. Date windows include
    that local day and the local current day. Partial
    failures retain the previous checkpoint; verified batches may already be saved by the callback.
    Changing any retrieval filter invalidates the checkpoint; presentation settings do not.
    """
    settings=profile_settings(profile,settings)
    mode = settings['search_sources']
    storage_mode=cache_mode(settings)
    method=settings.get('biorxiv_search_method','metadata')
    years = max(1, int(settings.get('biorxiv_recent_years', '2')))
    filters = {name: profile.get(name) for name in
               ('query_expression', 'year_from', 'year_to', 'maximum_results', 'pubmed_maximum_results', 'biorxiv_maximum_results', 'article_type', 'minimum_jif')}
    filters.update(issns=sorted(set(issns)), sources=mode, biorxiv_years=years, schema=1)
    if mode!='biorxiv' and settings.get('pubmed_search_scope','default')!='default':
        filters['pubmed_search_scope']=settings['pubmed_search_scope']
    if float(profile.get('minimum_jif') or 0)>0:filters['jif_year']=settings.get('jif_year')
    if method=='website' and mode!='pubmed':
        filters['biorxiv_search_method']='website'
        # Rebuild older unverified website baselines once instead of merging
        # potentially unrelated records into all future incremental results.
        filters['website_query_revision']=3
        scope=settings.get('biorxiv_website_scope','fulltext')
        filters['biorxiv_website_scope']='fulltext' if scope=='default' else scope
        filters['preprint_servers']=settings.get('preprint_servers','biorxiv')
        filters['website_order']='relevance' if profile.get('sort_mode')=='最相关' else 'newest'
    signature = hashlib.sha256(json.dumps(filters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    started_at = datetime.now(timezone.utc).isoformat()
    previous = database.refresh_checkpoint(profile['id'], storage_mode)
    since = None
    if not force_full and previous and previous['signature'] == signature:
        try:
            checkpoint_date = datetime.fromisoformat(previous.get('completed_at') or previous['started_at']).astimezone().date()
            if checkpoint_date <= date.today():
                since = checkpoint_date
        except (ValueError, TypeError):
            pass
    old = database.cached_articles(profile['id'], storage_mode) if since else []
    old_website={a['doi']:a for a in database.cached_articles(profile['id'],storage_mode) if a.get('doi')} if method=='website' and mode!='pubmed' else {}
    if since:
        progress(f'增量检索：复用 {len(old)} 篇缓存，补查 {since} 至今的新收录和更新记录（按发表日期精确到日，包含两个边界）。')
    else:
        progress('完整检索：首次检索、条件已变化或已选择完整检索。')
    report = client.search(profile['query_expression'], issns,
        0 if since else int(profile.get('pubmed_maximum_results') if profile.get('pubmed_maximum_results') is not None else profile['maximum_results']), progress,
        profile.get('year_from'), profile.get('year_to'), cancelled,
        sources={'both': ('bioRxiv', 'PubMed'), 'biorxiv': ('bioRxiv',), 'pubmed': ('PubMed',)}[mode],
        article_type=profile.get('article_type', 'all'), biorxiv_years=years, updated_since=since,
        biorxiv_search_method=method,website_fetch=website_fetch,
        biorxiv_website_scope=settings.get('biorxiv_website_scope','title_abstract'),
        preprint_servers=settings.get('preprint_servers','biorxiv'),
        website_order='relevance' if profile.get('sort_mode')=='最相关' else 'newest',
        pubmed_maximum_results=0 if since else int(profile.get('pubmed_maximum_results') if profile.get('pubmed_maximum_results') is not None else profile['maximum_results']),
        biorxiv_maximum_results=0 if since else int(profile.get('biorxiv_maximum_results') if profile.get('biorxiv_maximum_results') is not None else profile['maximum_results']),
        pubmed_search_scope=settings.get('pubmed_search_scope','default'),
        **({'candidate_callback':candidate_callback} if candidate_callback else {}))
    if cancelled():
        raise InterruptedError('检索已取消，保留上次缓存。')
    if report.source_errors and not report.candidates:
        raise ValueError('；'.join(report.source_errors))
    if method=='website' and mode!='pubmed' and any(error.startswith(('bioRxiv','medRxiv')) for error in report.source_errors):
        raise ValueError('；'.join(report.source_errors))
    for article,_ in report.candidates:
        cached=old_website.get(article.get('doi'))
        if cached and article.get('biorxiv_search_method')=='website' and cached.get('preprint_version')==article.get('preprint_version'):
            for key in ('abstract','authors','publication_date','publication_year'):
                if not article.get(key) and cached.get(key):article[key]=cached[key]
    lower = max(rolling_start(years), date(profile.get('year_from') or 2013, 1, 1))
    upper = min(date.today(), date(profile.get('year_to') or date.today().year, 12, 31))
    def within_window(article):
        if not any(source in str(article.get('source_name') or '') for source in ('bioRxiv','medRxiv')):
            return True
        if article.get('website_date_filtered') and not article.get('publication_date'):
            return True
        try:
            return lower <= date.fromisoformat(str(article.get('publication_date') or '')[:10]) <= upper
        except ValueError:
            return False
    merged, identifiers = {}, {}
    def identity(article):
        return [('key', article['article_key'])] + [
            (field, str(article[field]).strip().casefold()) for field in ('doi', 'pmid') if article.get(field)]
    for article in old:
        if not within_window(article):
            continue
        # Do not persist transient UI/favorite state in the metadata snapshot.
        article = {key: value for key, value in article.items() if key not in
                   ('is_favorite', 'favorite_saved_at', 'tags', 'personal_note', 'doi_visited', 'fetched_at')}
        merged[article['article_key']] = (article, float(article.get('relevance_score') or 0))
        for token in identity(article):
            identifiers[token] = article['article_key']
    reused = len(merged)
    new_count = 0
    for article, relevance in report.candidates:
        if not within_window(article):
            continue
        existing_key = next((identifiers[token] for token in identity(article) if token in identifiers), None)
        if existing_key:
            original, _ = merged.pop(existing_key)
            article = {**original, **{key: value for key, value in article.items() if value not in (None, '', [])}}
            for token in identity(original):
                identifiers[token] = article['article_key']
        else:
            new_count += 1
        merged[article['article_key']] = (article, relevance)
        for token in identity(article):
            identifiers[token] = article['article_key']
    report.candidates = list(merged.values())
    report.incremental = since is not None
    report.new_count, report.reused_count = new_count, reused
    checkpoint = previous if since and report.source_errors else None
    if not report.source_errors:
        checkpoint = dict(signature=signature, started_at=started_at, completed_at=datetime.now(timezone.utc).isoformat())
    return report, checkpoint
