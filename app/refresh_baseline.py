"""Reuse a verified broader preprint baseline when narrowing the selected servers."""
import hashlib,json
from itertools import product,permutations
from article_types import normalize_types
from source_modes import cache_mode

def filter_signature(filters):
    """Hash retrieval meaning, rather than SQLite/UI serialization details."""
    values=dict(filters)
    for name in ('year_from','year_to','maximum_results','pubmed_maximum_results','biorxiv_maximum_results','biorxiv_years','jif_year'):
        if values.get(name) is not None:values[name]=int(values[name])
    values['minimum_jif']=float(values.get('minimum_jif') or 0)
    values['article_type']=sorted(normalize_types(values.get('article_type','all')))
    # The old common budget has no effect when endpoint budgets are explicit.
    active=('pubmed_maximum_results',) if values.get('sources')=='pubmed' else ('biorxiv_maximum_results',) if values.get('sources')=='biorxiv' else ('pubmed_maximum_results','biorxiv_maximum_results')
    if all(values.get(name) is not None for name in active):values.pop('maximum_results',None)
    if values.get('sources')=='pubmed':
        for name in ('biorxiv_years','biorxiv_maximum_results'):values.pop(name,None)
    if values.get('sources')=='biorxiv':values.pop('pubmed_maximum_results',None)
    return 'v2:'+hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def compatible_signatures(filters):
    """Prove legacy hashes using only semantically identical representations.

    No date or keyword is guessed; row counts and NEW flags are never evidence
    of a completed search. Unknown old hashes remain invalid.
    """
    variants=[dict(filters)]
    if filters.get('sources')=='pubmed':
        reduced=dict(filters);reduced.pop('biorxiv_years',None);reduced.pop('biorxiv_maximum_results',None);variants.append(reduced)
    signatures={filter_signature(filters)}
    selected=normalize_types(filters.get('article_type','all'))
    orders=list(permutations(selected)) if len(selected)<=4 else [selected,sorted(selected)]
    types={json.dumps(list(order),separators=separators) for order in orders for separators in (None,(',',':'))}
    if len(selected)==1:types.add(selected[0])
    for template in variants:
        numbers=[name for name in ('maximum_results','pubmed_maximum_results','biorxiv_maximum_results','year_from','year_to') if template.get(name) is not None]
        choices=[[int(template[name]),str(int(template[name]))] for name in numbers]
        for representation in product(*choices):
            values={**template,**dict(zip(numbers,representation))}
            for kind in [*types,template.get('article_type','all')]:
                values['article_type']=kind
                signatures.add(hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    return signatures

def compatible_baseline(database,profile_id,settings,filters,signature,legacy_signature):
    mode=cache_mode(settings)
    previous=database.refresh_checkpoint(profile_id,mode)
    states=database.source_refresh_checkpoints(profile_id,mode)
    valid=compatible_signatures(filters)|{signature,legacy_signature}
    # Normalize proven old checkpoints in memory; subsequent successful commits
    # persist the stable signature without replacing their completion dates.
    if previous and previous['signature'] in valid:previous={**previous,'signature':signature}
    states={source:({**state,'signature':signature} if state['signature'] in valid else state) for source,state in states.items()}
    if (previous and previous['signature'] in valid) or any(s['signature'] in valid and s.get('completed_at') for s in states.values()):
        return previous,states,mode,False
    # Both -> one server is a subset of an already searched result set. The
    # exact old hash proves every OTHER retrieval criterion is unchanged.
    # Never extrapolate a checkpoint from row counts or last_refreshed_at.
    if settings.get('search_sources')=='pubmed' or settings.get('preprint_servers','biorxiv') not in ('biorxiv','medrxiv'):
        return previous,states,mode,False
    broad_settings={**settings,'preprint_servers':'both'};broad_mode=cache_mode(broad_settings)
    broad_filters={**filters,'preprint_servers':'both'}
    broad_signatures=compatible_signatures(broad_filters)
    broad_previous=database.refresh_checkpoint(profile_id,broad_mode)
    broad_states=database.source_refresh_checkpoints(profile_id,broad_mode)
    broad_previous=broad_previous if broad_previous and broad_previous['signature'] in broad_signatures and broad_previous.get('completed_at') else None
    broad_states={source:{**state,'signature':signature} for source,state in broad_states.items() if state['signature'] in broad_signatures}
    if not broad_previous and not any(s.get('completed_at') for s in broad_states.values()):return previous,states,mode,False
    if broad_previous:broad_previous={**broad_previous,'signature':signature}
    return broad_previous,broad_states,broad_mode,True

def baseline_articles(database,profile_id,mode,settings,adopted):
    articles=database.cached_articles(profile_id,mode)
    if not adopted:return articles
    allowed=['bioRxiv' if settings.get('preprint_servers','biorxiv')=='biorxiv' else 'medRxiv']
    if settings.get('search_sources')!='biorxiv':allowed.append('PubMed')
    return [article for article in articles if any(source in str(article.get('source_name','')) for source in allowed)]
