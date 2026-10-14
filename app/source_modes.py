def cache_mode(settings):
    """Website full-text hits must never be presented as metadata title/abstract hits."""
    mode = settings.get('search_sources','both')
    if mode!='pubmed' and settings.get('biorxiv_search_method','metadata')=='website':
        server=settings.get('preprint_servers','biorxiv')
        server_suffix='' if server=='biorxiv' else '_'+server
        if settings.get('biorxiv_website_scope','title_abstract')=='title_abstract':
            return mode+server_suffix+'_title_abstract_website'
        return mode+server_suffix+'_website'
    return mode


def profile_settings(profile,settings):
    """Retrieval preferences belong to this search; global server/scope affect next refresh."""
    return {**settings,'search_sources':profile.get('search_sources') or 'both',
            'biorxiv_search_method':'website',
            'biorxiv_website_scope':settings.get('preprint_search_scope','default')}
