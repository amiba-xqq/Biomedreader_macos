"""Endpoint-specific keywords with lossless fallback for existing profiles."""
def profile_queries(profile):
    legacy=str(profile.get('query_expression') or '').strip()
    return {source:legacy if profile.get(field) is None else str(profile[field]).strip()
            for source,field in [('PubMed','pubmed_query_expression'),('bioRxiv','preprint_query_expression')]}

def primary_query(profile):
    queries=profile_queries(profile)
    return queries['bioRxiv'] if profile.get('search_sources')=='biorxiv' else queries['PubMed']

def article_query(profile,article):
    queries=profile_queries(profile)
    return queries['bioRxiv'] if any(name in str(article.get('source_name','')) for name in ('bioRxiv','medRxiv')) else queries['PubMed']
