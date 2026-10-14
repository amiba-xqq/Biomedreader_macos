"""Repartition the original IF-directory union using the stored 2025 annual metric."""
import json
import math
from jif_supplement import issn_key

SCOPE_IDS=('builtin_if_6_10','builtin_if_10_20','builtin_if_20_500')


def regroup_if_scopes(connection,original_issns,stamp):
    aliases={};direct={}
    for row in connection.execute('SELECT issn,aliases FROM journals'):
        direct[issn_key(row['issn'])]=row['issn']
        for alias in {row['issn'],*(row['aliases'] or '').split(';')}:
            key=issn_key(alias)
            if key:aliases.setdefault(key,set()).add(row['issn'])
    canonical=set();unmatched=[];ambiguous=[]
    for issn in sorted(set(original_issns)):
        if issn_key(issn) in direct:
            canonical.add(direct[issn_key(issn)]);continue
        matches=aliases.get(issn_key(issn),set())
        if len(matches)==1:canonical.update(matches)
        elif matches:ambiguous.append(issn)
        else:unmatched.append(issn)
    metrics={row['issn']:row['jif'] for row in connection.execute('SELECT issn,jif FROM journal_metrics WHERE year=2025')}
    groups={identifier:[] for identifier in SCOPE_IDS};missing=[];outside=[]
    for issn in sorted(canonical):
        value=metrics.get(issn)
        if value is None or not math.isfinite(float(value)):
            missing.append(issn);continue
        value=float(value)
        bucket=SCOPE_IDS[0] if 6<=value<10 else SCOPE_IDS[1] if 10<=value<20 else SCOPE_IDS[2] if 20<=value<=500 else None
        if bucket:groups[bucket].append(issn)
        else:outside.append(issn)
    for identifier,members in groups.items():
        connection.execute('UPDATE journal_scopes SET issns_json=?,updated_at=? WHERE id=? AND is_builtin=1',
            (json.dumps(members),stamp,identifier))
    return dict(year=2025,original_identifiers=len(set(original_issns)),journals=len(canonical),
        counts={identifier:len(members) for identifier,members in groups.items()},
        missing=missing,outside=outside,unmatched=unmatched,ambiguous=ambiguous)
