"""Stable volume ownership, with materialized paths for existing index queries.

Volume ID owns each row. Relative paths are derived from its namespace so the
database need not duplicate millions of full paths. `path` is moved by volume ID.
Disconnected conflicting mounts use private database namespaces (never I/O).
"""
import json
import os
import sqlite3
import time
import uuid
import re
import shutil
from pathlib import Path

SCHEMA='''
CREATE TABLE IF NOT EXISTS volumes(
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL UNIQUE, last_root TEXT NOT NULL,
 label TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 1,
 present INTEGER NOT NULL DEFAULT 0);
'''


def check_upgrade_space(db_path):
    """Reserve headroom for the backup, expanded records and transaction journal.

    Check before any writes. An interrupted upgrade has no completed marker and
    must pass this check again, even if its volumes table already exists.
    """
    path=Path(db_path).resolve()
    if not path.exists() or not path.stat().st_size:return
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='app_meta'").fetchone():
            if con.execute("SELECT 1 FROM app_meta WHERE key='volume_schema'").fetchone():return
    finally:con.close()
    required=path.stat().st_size*3+256*1024*1024
    free=shutil.disk_usage(path.parent).free
    if free<required:
        gib=1024**3
        raise OSError(
            f'Not enough free space to upgrade the saved index.\n\n'
            f'Data folder: {path.parent}\n'
            f'Available: {free/gib:.2f} GiB\n'
            f'Conservative working-space requirement: {required/gib:.2f} GiB free\n\n'
            'Free space on this drive, then start File Burglar again. '
            'The upgrade has not started. Existing indexes and safety backups are kept. '
            'Do not delete fileburglar.db to make room.')


def migrate(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        con.execute(SCHEMA)
        columns={
            'drives':{'volume_id':'TEXT'},
            'folders':{'volume_id':'TEXT','relative_path':'TEXT'},
            'file_log':{'volume_id':'TEXT','relative_path':'TEXT','observed_path':'TEXT','observed_root':'TEXT','legacy_unverified':'INTEGER NOT NULL DEFAULT 1'},
            'folder_daily':{'volume_id':'TEXT','relative_path':'TEXT'},
        }
        for table,additions in columns.items():
            existing={r[1] for r in con.execute('PRAGMA table_info('+table+')')}
            for name,kind in additions.items():
                if name not in existing:con.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        # A per-row index of 49-character volume IDs cost ~1 GB on busy systems.
        # Only rare drive-letter remaps filter the bounded activity log by volume.
        con.execute('DROP INDEX IF EXISTS ix_log_volume')
        if con.execute("SELECT 1 FROM app_meta WHERE key='volume_schema'").fetchone():
            con.commit();return
        identities=dict(con.execute('SELECT root,identity FROM volume_identity'))
        roots=set()
        for table,column in (('folders','path'),('file_log','path'),('folder_daily','folder')):
            roots.update(r[0] for r in con.execute(f'SELECT DISTINCT substr({column},1,3) FROM {table}'))
        for root in roots:
            if re.fullmatch(r'[A-Za-z]:\\',root):
                con.execute('INSERT OR IGNORE INTO drives(root) VALUES(?)',(root,))
        used=set()
        for (root,) in con.execute('SELECT root FROM drives').fetchall():
            ident=identities.get(root)
            verified=bool(ident and list(identities.values()).count(ident)==1 and ident not in used)
            ident=ident if verified else 'legacy:'+uuid.uuid4().hex
            used.add(ident)
            con.execute('INSERT INTO volumes(id,namespace,last_root,verified) VALUES(?,?,?,?)',(ident,root,root,int(verified)))
            con.execute('UPDATE drives SET volume_id=? WHERE root=?',(ident,root))
            lo=root;hi=root.rstrip('\\')+']'
            for table,column in (('folders','path'),('file_log','path'),('folder_daily','folder')):
                if table=='file_log':
                    con.execute(f'UPDATE {table} SET volume_id=?,observed_root=? WHERE {column}>=? AND {column}<?',(ident,root,lo,hi))
                else:con.execute(f'UPDATE {table} SET volume_id=? WHERE {column}>=? AND {column}<?',(ident,lo,hi))
        con.execute('CREATE INDEX IF NOT EXISTS ix_folders_volume ON folders(volume_id)')
        con.execute("INSERT INTO app_meta VALUES('volume_schema','1')")
        con.commit()
    except BaseException:
        con.rollback();raise


def rows(con):
    return [dict(zip(('id','namespace','last_root','label','verified','present'),r)) for r in con.execute(
        'SELECT id,namespace,last_root,label,verified,present FROM volumes')]


def owner(con,path):
    root=os.path.splitdrive(path)[0]+'\\'
    row=con.execute('SELECT volume_id FROM drives WHERE root=?',(root,)).fetchone()
    return row[0] if row else None


def archive_namespace(ident):
    return '@'+uuid.uuid5(uuid.NAMESPACE_URL,ident).hex+':\\'


def move(con,ident,new_root):
    old=con.execute('SELECT namespace FROM volumes WHERE id=?',(ident,)).fetchone()
    if not old or old[0]==new_root:return
    old=old[0]
    con.execute('UPDATE folders SET path=? || COALESCE(relative_path,substr(path,?)) WHERE volume_id=?',(new_root,len(old)+1,ident))
    con.execute('UPDATE file_log SET path=? || COALESCE(relative_path,substr(path,?)) WHERE volume_id=?',(new_root,len(old)+1,ident))
    con.execute('UPDATE folder_daily SET folder=? || COALESCE(relative_path,substr(folder,?)),drive=? WHERE volume_id=?',
                (new_root,len(old)+1,new_root.rstrip('\\'),ident))
    con.execute('UPDATE drives SET root=? WHERE volume_id=?',(new_root,ident))
    con.execute('UPDATE volumes SET namespace=? WHERE id=?',(new_root,ident))


def bind(con,ident,root,label=''):
    """Caller quiesces displaced workers first; transaction rolls back as a unit."""
    if not ident:raise ValueError('Volume identity is unavailable; refusing to associate saved drive data.')
    con.execute('SAVEPOINT bind_volume')
    try:
        occupied=con.execute('SELECT id FROM volumes WHERE namespace=? AND id!=?',(root,ident)).fetchone()
        if occupied:
            move(con,occupied[0],archive_namespace(occupied[0]))
            con.execute('UPDATE volumes SET present=0 WHERE id=?',(occupied[0],))
        existing=con.execute('SELECT namespace FROM volumes WHERE id=?',(ident,)).fetchone()
        if existing:move(con,ident,root)
        else:
            con.execute('INSERT INTO volumes(id,namespace,last_root) VALUES(?,?,?)',(ident,root,root))
            con.execute('INSERT INTO drives(root,volume_id) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET volume_id=excluded.volume_id',(root,ident))
        con.execute('UPDATE volumes SET last_root=?,label=CASE WHEN ?!=\'\' THEN ? ELSE label END,verified=1,present=1 WHERE id=?',(root,label,label,ident))
        con.execute('INSERT INTO volume_identity VALUES(?,?) ON CONFLICT(root) DO UPDATE SET identity=excluded.identity',(root,ident))
        con.execute('RELEASE bind_volume')
    except BaseException:
        con.execute('ROLLBACK TO bind_volume');con.execute('RELEASE bind_volume');raise


def offline(con,ident):
    con.execute('UPDATE volumes SET present=0 WHERE id=?',(ident,))


def display_root(info):
    root=info.get('last_root') or info.get('namespace') or ''
    label=info.get('label')
    if not label and info.get('namespace','').startswith('@'):label='Saved '+str(info.get('id',info.get('volume_id','')))[-6:]
    text=root[:2]+(' · '+label if label else '')
    if not info.get('verified',True):text+=' (unverified saved drive)'
    elif not info.get('present',False):text+=' (offline)'
    return text


def display_path(path,volume_rows):
    for volume in volume_rows:
        namespace=volume['namespace']
        if (path.startswith(namespace) or path==namespace.rstrip('\\')) and namespace.startswith('@'):
            label=volume['label'] or volume['id'][-8:]
            return volume['last_root'].rstrip('\\')+' ['+label+'; offline]\\'+path[len(namespace):]
    return path


def link_legacy(con,source,destination):
    src=con.execute('SELECT namespace,verified FROM volumes WHERE id=?',(source,)).fetchone()
    dst=con.execute('SELECT namespace,present FROM volumes WHERE id=?',(destination,)).fetchone()
    if not src or src[1] or not dst or not dst[1] or source==destination:
        raise ValueError('Choose an unverified saved drive and a connected identified volume.')
    root=dst[0]
    current=con.execute('SELECT baselined,last_scan FROM drives WHERE volume_id=?',(destination,)).fetchone()
    keep_current=bool(current and current[0] and current[1]>0)
    con.execute('SAVEPOINT link_legacy')
    try:
        while True:
            ids=con.execute('SELECT id FROM folders WHERE volume_id=? LIMIT 256',(source if keep_current else destination,)).fetchall()
            if not ids:break
            con.executemany('DELETE FROM files WHERE folder_id=?',ids)
            con.executemany('DELETE FROM folders WHERE id=?',ids)
        con.execute('UPDATE folders SET volume_id=?,path=? || COALESCE(relative_path,substr(path,?)) WHERE volume_id=?',(destination,root,len(src[0])+1,source))
        con.execute('UPDATE file_log SET volume_id=?,path=? || COALESCE(relative_path,substr(path,?)) WHERE volume_id=?',(destination,root,len(src[0])+1,source))
        con.execute('''INSERT INTO folder_daily(day,drive,folder,events,added,freed,last_ts,volume_id,relative_path)
            SELECT day,?,? || COALESCE(relative_path,substr(folder,?)),events,added,freed,last_ts,?,NULL FROM folder_daily WHERE volume_id=?
            ON CONFLICT(day,drive,folder) DO UPDATE SET events=events+excluded.events,added=added+excluded.added,
            freed=freed+excluded.freed,last_ts=MAX(last_ts,excluded.last_ts)''',(root.rstrip('\\'),root,len(src[0])+1,destination,source))
        con.execute('DELETE FROM folder_daily WHERE volume_id=?',(source,))
        saved=con.execute('SELECT gen,baselined,files,bytes,last_scan FROM drives WHERE volume_id=?',(source,)).fetchone()
        if saved and not keep_current:con.execute('UPDATE drives SET gen=?,baselined=?,files=?,bytes=?,last_scan=? WHERE volume_id=?',(*saved,destination))
        con.execute('DELETE FROM drives WHERE volume_id=?',(source,))
        con.execute('DELETE FROM volumes WHERE id=?',(source,))
        con.execute('RELEASE link_legacy')
    except BaseException:
        con.execute('ROLLBACK TO link_legacy');con.execute('RELEASE link_legacy');raise


def original_path(path,observed,observed_root,identity,metadata):
    if observed:return observed
    if observed_root:
        namespace=next((v['namespace'] for v in metadata if v['id']==identity),None)
        if namespace and path.startswith(namespace):return observed_root+path[len(namespace):]
    return path


BACKUP_KEEP_DAYS=7


def migration_backups(db_path):
    return sorted(Path(db_path).parent.glob('pre-volume-migration-*.db'))


def cleanup_migration_backups(db_path,keep_days=BACKUP_KEEP_DAYS):
    """Delete pre-upgrade copies once the upgrade has completed and they have aged out."""
    path=Path(db_path)
    if not path.exists():return []
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='app_meta'").fetchone():return []
        if not con.execute("SELECT 1 FROM app_meta WHERE key='volume_schema'").fetchone():return []
    finally:con.close()
    removed=[]
    for backup in migration_backups(db_path):
        try:
            if time.time()-backup.stat().st_mtime>=keep_days*86400:
                backup.unlink();removed.append(backup)
        except OSError:pass
    return removed


def backup_before_migration(db_path):
    path=Path(db_path)
    if not path.exists() or not path.stat().st_size:return None
    con=sqlite3.connect(path)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drives'").fetchone():return None
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='volumes'").fetchone():return None
        existing=migration_backups(db_path)
        if existing:
            # An interrupted upgrade already saved the untouched original. Never
            # make another full-size copy on every retry.
            return existing[0]
        destination=path.with_name('pre-volume-migration-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.db')
        copy=sqlite3.connect(destination)
        try:con.backup(copy,pages=256)
        finally:copy.close()
        return destination
    finally:con.close()
