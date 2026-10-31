"""Read-only indexed reports and streaming, cancellable CSV exports."""
import csv
import os
import sqlite3
import time
import volumes
from contextlib import closing
from pathlib import Path


def readonly(db):
    con=sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    con.execute('PRAGMA query_only=ON')
    con.execute('PRAGMA cache_size=-2048')
    return con


def growth_query(hours,drive=None,path=None,limit=300,volume_id=None):
    clauses=['ts>=?'];args=[time.time()-hours*3600]
    if volume_id:
        clauses.append('volume_id=?');args.append(volume_id)
    elif drive:
        clauses.append('path>=? AND path<?');args.extend((drive+'\\',drive+']'))
    if path:
        prefix=path.rstrip('\\')+'\\';clauses.append('path>=? AND path<?');args.extend((prefix,path.rstrip('\\')+']'))
    # Direct-parent aggregation: every file change contributes once, without
    # ancestor rollups that would double-count nested folders.
    sql=('SELECT parent_path(path) AS folder, COUNT(*) AS events, '
         'SUM(CASE WHEN delta>0 THEN delta ELSE 0 END) AS written, '
         'SUM(CASE WHEN delta<0 THEN -delta ELSE 0 END) AS freed, SUM(delta) AS net '
         'FROM file_log WHERE '+' AND '.join(clauses)+' GROUP BY volume_id,folder ORDER BY net DESC')
    if limit is not None:sql+=' LIMIT ?';args.append(limit)
    return sql,args


def prepare(con):
    con.create_function('parent_path',1,os.path.dirname,deterministic=True)


def safe_csv(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):
        return "'"+value
    return value


def export(db,sql,args,headers,destination,cancel):
    destination=Path(destination)
    temporary=destination.with_name(destination.name+'.partial')
    count=0
    try:
        with closing(readonly(db)) as con:
            con.execute('BEGIN')
            metadata=volumes.rows(con)
            prepare(con)
            con.set_progress_handler(lambda:int(cancel.is_set()),1000)
            with temporary.open('x',encoding='utf-8-sig',newline='') as file:
                writer=csv.writer(file);writer.writerow(headers)
                for row in con.execute(sql,args):
                    if cancel.is_set():raise InterruptedError('Export cancelled')
                    writer.writerow([safe_csv(volumes.display_path(x,metadata) if isinstance(x,str) and x.startswith('@') else x) for x in row]);count+=1
            if cancel.is_set():raise InterruptedError('Export cancelled')
        os.replace(temporary,destination)
        return count
    finally:
        # Only clean up the file created by this export, never a pre-existing
        # partial belonging to another export.
        if temporary.exists() and 'file' in locals():temporary.unlink()
