"""Evaluation identity is a source directory plus run/model, never a display name."""
import hashlib
import os


def source_key(value):
    return os.path.normcase(os.path.normpath(str(value).removeprefix('\\\\?\\'))) if value else None


def belongs(meta, chosen):
    if meta['run_id'] != chosen['run_id'] or meta['model'] != chosen.get('source_model', chosen['model']):
        return False
    source = chosen.get('source_run') if chosen.get('catalog_only') else chosen['run_root']
    return source_key(meta.get('source_run')) == source_key(source)


def revision(store, chosen=None, selection_required=False):
    if selection_required:
        return 'choose-evaluation'
    with store.db() as db:
        rows = db.execute('SELECT id,meta,status,updated,pinned,selected,error FROM episodes WHERE deleted IS NULL ORDER BY id').fetchall()
    digest = hashlib.sha256()
    for row in rows:
        import json
        if chosen and not belongs(json.loads(row['meta']), chosen):
            continue
        digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()
