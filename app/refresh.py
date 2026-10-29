"""Refresh a saved search using an atomic, source-specific successful-search checkpoint."""
import hashlib
import json
from datetime import date, datetime, timezone
from biorxiv import rolling_start
from source_modes import cache_mode,profile_settings
from refresh_baseline import compatible_baseline,baseline_articles,filter_signature
from search_queries import profile_queries


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
    queries=profile_queries(profile);filters['query_expression']=queries['bioRxiv'] if mode=='biorxiv' else queries['PubMed']
    if mode=='both' and queries['bioRxiv']!=queries['PubMed']:filters['preprint_query_expression']=queries['bioRxiv']
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
    legacy_signature = hashlib.sha256(json.dumps(filters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if mode=='pubmed':
        # Preprint preferences do not alter a PubMed-only retrieval baseline.
        filters.pop('biorxiv_years',None)
        filters.pop('biorxiv_maximum_results',None)
    signature = filter_signature(filters)
    started_at = datetime.now(timezone.utc).isoformat()
    previous,source_states,baseline_mode,adopted=compatible_baseline(database,profile['id'],settings,filters,signature,legacy_signature) if not force_full else (
        database.refresh_checkpoint(profile['id'],storage_mode),database.source_refresh_checkpoints(profile['id'],storage_mode),storage_mode,False)
    since = None
    if not force_full and previous and previous['signature'] in (signature,legacy_signature):
        try:
            checkpoint_date = datetime.fromisoformat(previous.get('completed_at') or previous['started_at']).astimezone().date()
            if checkpoint_date <= date.today():
                since = checkpoint_date
        except (ValueError, TypeError):
            pass
    selected_sources={'both':('bioRxiv','PubMed'),'biorxiv':('bioRxiv',),'pubmed':('PubMed',)}[mode]
    source_dates={source:since for source in selected_sources}
    pending_sources=set()
    for source in selected_sources:
        state=source_states.get(source)
        if force_full or not state or state['signature'] not in (signature,legacy_signature):continue
        if state.get('completed_at'):
            try:
                completed_date=datetime.fromisoformat(state['completed_at']).astimezone().date()
                if completed_date<=date.today():source_dates[source]=completed_date
            except (ValueError,TypeError):pass
        else:pending_sources.add(source)
    dated=[value for value in source_dates.values() if value is not None]
    since=min(dated) if dated else None
    # Load a large cache once; the old code read the same 24,000+ rows twice.
    cached=baseline_articles(database,profile['id'],baseline_mode,settings,adopted) if since or pending_sources or (not previous and not force_full) or (method=='website' and mode!='pubmed') else []
    if adopted:
        progress('复用已验证的较大来源范围基线：当前来源是原范围的子集，其它检索条件一致，沿用各来源的成功日期。')
        if candidate_callback:
            # Seed the active snapshot before streamed updates, so cancellation
            # keeps both the old verified cache and already received new rows.
            database.adopt_refresh_baseline(profile['id'],storage_mode,cached,source_states,
                database.source_cache_refreshed_at(profile['id'],baseline_mode))
    old=cached if since or pending_sources else []
    if cached and not previous and not source_states and not force_full and 'PubMed' in selected_sources:
        # Old versions could save a partial cache without any completion marker.
        # Recheck the current matching PMID set; reuse its cached metadata rather
        # than downloading 24,000 already stored abstracts again. This is NOT a
        # fabricated successful checkpoint or a date cut-off based on cache size.
        pending_sources.add('PubMed')
    old_website={a['doi']:a for a in cached if a.get('doi')} if method=='website' and mode!='pubmed' else {}
    cached_pubmed={str(a['pmid']):a for a in cached if a.get('pmid') and 'PubMed' in str(a.get('source_name',''))} if 'PubMed' in pending_sources and not force_full else {}
    if since:
        progress(f'增量检索：复用 {len(old)} 篇缓存，补查 {since} 至今的新收录和更新记录（按发表日期精确到日，包含两个边界）。')
    else:
        reason='已勾选完整检索' if force_full else '检索条件已变化' if previous else '尚无完整成功检查点（可能为首次检索或旧版存在未完成请求）'
        progress('完整检索：'+reason+'。')
    for source in selected_sources:
        progress(f'{source} 增量范围：{source_dates[source]} 至 {date.today()}。' if source_dates[source] else
                 f'{source}：续查未完成的首次检索，复用已缓存记录。' if source in pending_sources else f'{source}：完整检索。')
    # Reset previous NEW badges once the refresh mode is known, including full searches.
    database.clear_new_articles(profile['id'])
    known=set()
    for record in cached:
        known.add(('key',record['article_key']))
        known.update((field,str(record[field]).strip().casefold()) for field in ('doi','pmid') if record.get(field))
    delivered=[False]
    incremental_new_keys=set()
    def save_new_batch(candidates):
        if candidate_callback:candidate_callback(candidates)
        keys=[]
        for record,_ in candidates:
            tokens={('key',record['article_key'])}|{(field,str(record[field]).strip().casefold()) for field in ('doi','pmid') if record.get(field)}
            source='PubMed' if 'PubMed' in str(record.get('source_name','')) else 'bioRxiv'
            if source_dates.get(source) and not (tokens & known):keys.append(record['article_key'])
            known.update(tokens)
        if keys:
            incremental_new_keys.update(keys)
            database.mark_new_articles(profile['id'],storage_mode,keys)
        if candidates:delivered[0]=True
    report = client.search(filters['query_expression'], issns,
        0 if since else int(profile.get('pubmed_maximum_results') if profile.get('pubmed_maximum_results') is not None else profile['maximum_results']), progress,
        profile.get('year_from'), profile.get('year_to'), cancelled,
        sources=selected_sources,
        article_type=profile.get('article_type', 'all'), biorxiv_years=years, updated_since=since,
        biorxiv_search_method=method,website_fetch=website_fetch,
        biorxiv_website_scope=settings.get('biorxiv_website_scope','title_abstract'),
        preprint_servers=settings.get('preprint_servers','biorxiv'),
        website_order='relevance' if profile.get('sort_mode')=='最相关' else 'newest',
        pubmed_maximum_results=0 if source_dates.get('PubMed') else int(profile.get('pubmed_maximum_results') if profile.get('pubmed_maximum_results') is not None else profile['maximum_results']),
        biorxiv_maximum_results=0 if source_dates.get('bioRxiv') else int(profile.get('biorxiv_maximum_results') if profile.get('biorxiv_maximum_results') is not None else profile['maximum_results']),
        pubmed_search_scope=settings.get('pubmed_search_scope','default'),
        candidate_callback=save_new_batch,
        **({'source_queries':queries} if mode=='both' and queries['bioRxiv']!=queries['PubMed'] else {}),
        **({'source_updated_since':source_dates} if any(value!=since for value in source_dates.values()) else {}),
        **({'cached_pubmed':cached_pubmed} if cached_pubmed else {}))
    if not delivered[0] and report.candidates:save_new_batch(report.candidates)
    if cancelled():
        raise InterruptedError('检索已取消，保留上次缓存。')
    # Commit each completed endpoint with its returned records atomically. A
    # failure on the other endpoint must not erase a healthy daily checkpoint.
    # Legacy/mocked reports without per-source completion keep their old rules.
    if report.source_completion_tracked or report.completed_sources:
        states={}
        for source in selected_sources:
            completed=report.completed_sources.get(source)
            prior=source_states.get(source)
            if not completed and prior and prior['signature'] in (signature,legacy_signature) and prior.get('completed_at'):
                states[source]=prior
            else:states[source]=dict(signature=signature,started_at=started_at,completed_at=completed)
        database.store_refresh_results(profile['id'],report.candidates,storage_mode,partial=True,source_checkpoints=states)
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
    replaced={source for source in pending_sources if source in report.completed_sources and not source_dates[source]}
    for article in old:
        if not within_window(article):
            continue
        if not since and set(selected_sources)<=replaced:
            # Complete recovery of the entire selected set supersedes even old
            # legacy snapshot rows whose source labels no longer match it.
            continue
        article_sources={source for source in selected_sources if
            (source=='PubMed' and 'PubMed' in str(article.get('source_name',''))) or
            (source=='bioRxiv' and any(name in str(article.get('source_name','')) for name in ('bioRxiv','medRxiv')))}
        if article_sources and article_sources<=replaced:
            # A completed recovery supplies the full, revalidated matching set,
            # including reused records. Drop stale baseline rows not in that set.
            continue
        # Do not persist transient UI/favorite state in the metadata snapshot.
        article = {key: value for key, value in article.items() if key not in
                   ('is_favorite', 'favorite_saved_at', 'tags', 'personal_note', 'doi_visited', 'fetched_at','is_incremental_new')}
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
    report.incremental_new_keys=sorted(incremental_new_keys)
    report.ai_search_window={source:{'from':str(day) if day else None,'to':str(date.today())} for source,day in source_dates.items()}
    report.refresh_modes={source:('增量检索' if source_dates[source] else '续查未完成检索' if source in pending_sources else '完整检索') for source in selected_sources}
    report.new_count, report.reused_count = new_count, reused
    checkpoint = previous if since and report.source_errors else None
    if not report.source_errors:
        checkpoint = dict(signature=signature, started_at=started_at, completed_at=datetime.now(timezone.utc).isoformat())
    return report, checkpoint
