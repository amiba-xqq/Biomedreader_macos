"""Group-scoped removal; only the last membership destroys favorite resources."""
import sqlite3

def plan(connection,keys,group_ids=None):
    scope=None if group_ids is None else {str(value) for value in group_ids}
    result=dict(final_keys=[],retained_keys=[],skip_keys=[],memberships={})
    for key in dict.fromkeys(keys):
        favorite=connection.execute('SELECT 1 FROM favorites WHERE article_key=? AND is_favorite=1',(key,)).fetchone()
        groups={str(row[0]) for row in connection.execute('SELECT group_id FROM favorite_group_articles WHERE article_key=?',(key,))}
        removing=groups if scope is None else groups&scope
        applies=bool(favorite and (scope is None or removing or (not groups and '__ungrouped__' in scope)))
        if not applies:result['skip_keys'].append(key);continue
        remaining=groups-removing
        result['memberships'][key]=dict(remove=sorted(removing),remaining=sorted(remaining))
        result['retained_keys' if remaining else 'final_keys'].append(key)
    return result

def remove(database,keys,group_ids=None,confirmed_final_keys=None):
    removed=[];retained=[];failed=[];final=[]
    confirmed=None if confirmed_final_keys is None else set(confirmed_final_keys)
    for key in dict.fromkeys(keys):
        try:
            with database.connect() as c:
                c.execute('BEGIN IMMEDIATE');current=plan(c,[key],group_ids)
                if key in current['skip_keys']:continue
                if key in current['final_keys'] and confirmed is not None and key not in confirmed:raise ValueError('分组关系已变化，需要重新确认删除关联信息；本篇未取消。')
                for group in current['memberships'][key]['remove']:c.execute('DELETE FROM favorite_group_articles WHERE article_key=? AND group_id=?',(key,group))
                if key in current['final_keys']:
                    # The existing trigger invalidates AI summaries/epochs.
                    c.execute("UPDATE favorites SET is_favorite=0,tags='',personal_note='' WHERE article_key=?",(key,))
                    database._remove_unfavorited_pdf(c,key)
                    c.execute('DELETE FROM favorites WHERE article_key=?',(key,))
                    final.append(key)
                else:retained.append(key)
            removed.append(key)
        except (OSError,sqlite3.Error,ValueError) as error:
            if key in final:final.remove(key)
            if key in retained:retained.remove(key)
            failed.append(dict(article_key=key,error=str(error)))
    cleanup_pending=False
    if final:
        try:
            from cache_cleanup import collect_unused
            with database.connect() as c:collect_unused(c,final)
        except (sqlite3.Error,ValueError):cleanup_pending=True
    return dict(removed=removed,retained=retained,final=final,failed=failed,cleanup_pending=cleanup_pending)
