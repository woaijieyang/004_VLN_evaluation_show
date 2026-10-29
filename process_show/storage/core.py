import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from datetime import datetime, timezone, timedelta

SCHEMA = 1
SGT = timezone(timedelta(hours=8))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(dumps(value))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def confined(root, path):
    """Reject links in every component, including the root itself."""
    root, path = Path(root).absolute(), Path(path).absolute()
    if os.name=='nt':
        def extended(value):
            text=str(value)
            if text.startswith('\\\\?\\'):return value
            return Path('\\\\?\\UNC\\'+text[2:] if text.startswith('\\\\') else '\\\\?\\'+text)
        root,path=extended(root),extended(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise ValueError("Path outside configured root")
    if '..' in parts:raise ValueError('Parent traversal outside configured root is forbidden')
    def linked(value):
        if value.is_symlink():return True
        if os.name=='nt':
            import ctypes
            query=ctypes.windll.kernel32.GetFileAttributesW
            query.argtypes=[ctypes.c_wchar_p];query.restype=ctypes.c_uint32
            attributes=query(str(value))
            return attributes not in (-1,0xFFFFFFFF) and bool(attributes&0x400)
        return False
    for parent in (root, *root.parents):
        if linked(parent):
            raise ValueError("Symlink root forbidden")
    current = root
    for part in parts:
        current /= part
        if linked(current):
            raise ValueError("Symlink forbidden")
    if root.resolve() != path.resolve() and root.resolve() not in path.resolve().parents:
        raise ValueError("Path escape")
    return path


def slug(value, limit=60):
    text = re.sub(r"[^\w.-]+", "_", str(value), flags=re.UNICODE).strip(". _")[:limit]
    if not text or text.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        text = "id_" + hashlib.sha256(str(value).encode()).hexdigest()[:8]
    return text


def validate_meta(meta):
    meta = dict(meta)
    if meta.get("schema_version", SCHEMA) != SCHEMA:
        raise ValueError("Unsupported schema_version")
    for key in ("run_id", "attempt", "model", "stage", "dataset", "split", "scene_id", "episode_id", "instruction"):
        if key not in meta or meta[key] is None:
            raise ValueError("Missing metadata: " + key)
    meta.setdefault("started_at", datetime.now(SGT).isoformat())
    dt = datetime.fromisoformat(meta["started_at"].replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("started_at must include timezone")
    meta["date"] = dt.astimezone(SGT).date().isoformat()
    meta["schema_version"] = SCHEMA
    meta.setdefault("instruction_id", None)
    meta.setdefault("coordinate_system", {"units": "m", "axes": "x-right,y-up,z-back", "quaternion": "xyzw", "forward": "-z"})
    if meta["coordinate_system"] != {"units": "m", "axes": "x-right,y-up,z-back", "quaternion": "xyzw", "forward": "-z"}:
        raise ValueError("Adapter must normalize coordinates to meters, x-right/y-up/z-back, xyzw, forward=-z")
    dumps(meta)
    return meta


def validate_step(step):
    step = dict(step)
    n = step.get("step")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("step must be a nonnegative integer")
    if n and "action" not in step:
        raise ValueError("Missing action")
    if step.get("pose"):
        p = step["pose"].get("position")
        if not isinstance(p, (list, tuple)) or len(p) != 3 or not all(isinstance(x, (float, int)) and math.isfinite(x) for x in p):
            raise ValueError("Invalid position")
        q = step["pose"].get("rotation_xyzw")
        if q is not None and (len(q) != 4 or not all(math.isfinite(x) for x in q) or sum(x*x for x in q) < 1e-10):
            raise ValueError("Invalid quaternion")
    if step.get("map_transform") is not None:
        t=step["map_transform"]
        if len(t)!=2 or any(len(r)!=3 or not all(math.isfinite(v) for v in r) for r in t):
            raise ValueError("map_transform must be a finite 2x3 x,z-to-pixel affine matrix")
    step.setdefault("timestamp", time.time())
    step.setdefault("observation_phase", "reset" if n == 0 else "after_action")
    if step["observation_phase"] not in ("reset", "after_action"):
        raise ValueError("Observation must be reset or after_action")
    dumps(step)
    return step


class Store:
    def __init__(self, root, clock=time.time):
        self.root = confined(Path(root).absolute(),Path(root).absolute())
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.dbpath = self.root / "catalog.sqlite3"
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY, directory TEXT UNIQUE, meta TEXT,
              status TEXT, result TEXT, created REAL, updated REAL, ready REAL, pinned INTEGER DEFAULT 0,
              selected INTEGER DEFAULT 0, priority REAL, outcome TEXT, error TEXT DEFAULT '', deleted REAL);
            CREATE TABLE IF NOT EXISTS steps(episode TEXT, step INTEGER, data TEXT, PRIMARY KEY(episode,step));
            CREATE TABLE IF NOT EXISTS files(episode TEXT, path TEXT UNIQUE, PRIMARY KEY(episode,path));
            CREATE TABLE IF NOT EXISTS leases(token TEXT PRIMARY KEY, episode TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS imports(path TEXT PRIMARY KEY, offset INTEGER, state TEXT);
            CREATE TABLE IF NOT EXISTS render_requests(episode TEXT PRIMARY KEY, requested REAL);
            ''')
            for key, value in {"seed": secrets.token_hex(32), "policy": {"count": 5, "datasets": {}, "rules": [], "selections": {}, "ttl_seconds": 3600, "max_bytes": 20 * 1024**3}}.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, dumps(value)))

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key):
        with self.db() as db:
            return json.loads(db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0])

    def create(self, meta):
        meta = validate_meta(meta)
        identity = [meta.get(k) for k in ("run_id", "attempt", "model", "dataset", "split", "scene_id", "episode_id", "instruction_id")]
        legacy_eid = hashlib.sha256(dumps(identity).encode()).hexdigest()
        eid = legacy_eid
        if meta.get('source_run'):
            from process_show.service.evaluations import source_key
            source = source_key(meta['source_run'])
            eid = hashlib.sha256(dumps(identity + [source]).encode()).hexdigest()
            # Existing trace cursors and links keep their original identity.
            with self.db() as db:
                old = db.execute('SELECT meta FROM episodes WHERE id=?',(legacy_eid,)).fetchone()
            if old and source_key(json.loads(old['meta']).get('source_run')) == source:
                eid = legacy_eid
        group = "-".join(slug(meta[k]) for k in ("date", "stage", "dataset"))
        name = "-".join([slug(meta["dataset"]), slug(meta["split"]), slug(meta["scene_id"]), "ep"+slug(meta["episode_id"]), "inst"+slug(meta["instruction_id"] or "NA"), "run"+eid[:12], "attempt"+slug(meta["attempt"])])
        rel = str(Path(group) / name)
        priority = int(hashlib.sha256((self.setting("seed")+eid).encode()).hexdigest()[:13], 16) / 16**13
        with self.db() as db:
            existing = db.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone()
            if existing:
                old = json.loads(existing["meta"])
                for field in ("instruction", "stage", "coordinate_system"):
                    if old.get(field) != meta.get(field):
                        raise ValueError("Episode identity reused with different " + field)
                return eid
            folder = confined(self.root, self.root / rel)
            folder.mkdir(parents=True, exist_ok=False)
            now = self.clock()
            db.execute("INSERT INTO episodes(id,directory,meta,status,created,updated,priority) VALUES (?,?,?,?,?,?,?)", (eid, rel, dumps(meta), "live", now, now, priority))
        self.save_json(eid, "episode.json", meta)
        return eid

    def get(self, eid):
        with self.db() as db:
            row = db.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone()
        if row is None:
            raise KeyError(eid)
        result = dict(row)
        result["meta"] = json.loads(result["meta"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def path(self, eid, name=""):
        return confined(self.root, self.root / self.get(eid)["directory"] / name)

    def own(self, eid, path):
        path = confined(self.root, path)
        folder = self.path(eid)
        if folder not in path.parents:
            raise ValueError("Asset outside episode")
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO files VALUES (?,?)", (eid, str(path.relative_to(self.root))))

    def save_json(self, eid, name, value):
        path = self.path(eid, name)
        self.own(eid, path)
        self.own(eid, path.with_name(path.name + ".tmp"))
        atomic_json(path, value)

    def put_step(self, eid, step):
        step = validate_step(step)
        with self.db() as db:
            if db.execute("SELECT status FROM episodes WHERE id=?", (eid,)).fetchone()[0] != "live":
                raise ValueError("Cannot modify sealed episode")
            db.execute("INSERT INTO steps VALUES (?,?,?) ON CONFLICT(episode,step) DO UPDATE SET data=excluded.data", (eid, step["step"], dumps(step)))
            db.execute("UPDATE episodes SET updated=? WHERE id=?", (self.clock(), eid))

    def steps(self, eid):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM steps WHERE episode=? ORDER BY step", (eid,))]

    def list(self):
        with self.db() as db:
            records=db.execute("SELECT e.*,COALESCE((SELECT MAX(s.step) FROM steps s WHERE s.episode=e.id),0) AS max_step,(SELECT COUNT(*) FROM steps s WHERE s.episode=e.id) AS recorded_steps FROM episodes e WHERE deleted IS NULL ORDER BY created DESC").fetchall()
        rows = []
        for record in records:
            item=dict(record)
            item['meta']=json.loads(item['meta'])
            item['result']=json.loads(item['result']) if item['result'] else None
            item["catching_up"]=bool(item["meta"].get("source_completed") and item["status"]=="live")
            item.pop("priority", None)
            rows.append(item)
        return rows

    def error(self, eid, message):
        with self.db() as db:
            db.execute("UPDATE episodes SET error=?,updated=? WHERE id=?", (str(message), self.clock(), eid))

    def set_policy(self, policy):
        allowed = {"count", "datasets", "rules", "selections", "ttl_seconds", "max_bytes"}
        if set(policy) - allowed:
            raise ValueError("Unknown policy fields")
        current = self.setting("policy")
        current.update(policy)
        selections = current.setdefault("selections", {})
        if not isinstance(selections, dict) or any(
            not isinstance(dataset, str) or not dataset.strip() or not isinstance(ids, list)
            or len(ids) > 100000 or any(not isinstance(i, str) or not i.strip() or len(i) > 256 for i in ids)
            for dataset, ids in selections.items()
        ):
            raise ValueError("selections must map dataset names to lists of Episode IDs")
        current["selections"] = {dataset: list(dict.fromkeys(i.strip() for i in ids)) for dataset, ids in selections.items()}
        if not isinstance(current["datasets"],dict) or not isinstance(current["rules"],list):
            raise ValueError("datasets must be an object and rules an array")
        if type(current["ttl_seconds"]) is not int or type(current["max_bytes"]) is not int:
            raise ValueError("TTL and storage budget must be integers")
        for n in [current["count"], *current["datasets"].values()]:
            if type(n) is not int or not 0 <= n <= 100000:
                raise ValueError("Retention count must be 0..100000")
        if not 60 <= current["ttl_seconds"] <= 30*86400 or current["max_bytes"] < 1024**2:
            raise ValueError("Cache duration must be 1 minute..30 days; max_bytes >=1MiB")
        for rule in current["rules"]:
            if not isinstance(rule,dict) or not rule or set(rule) - {"dataset", "episode_id", "scene_id", "stage", "run_id"}:
                raise ValueError("Invalid keep rule")
        with self.db() as db:
            db.execute("UPDATE settings SET value=? WHERE key='policy'", (dumps(current),))
        self.cleanup(dry_run=True)
        return current

    def pin(self, eid, value):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT deleted FROM episodes WHERE id=?", (eid,)).fetchone()
            if row is None or row[0] is not None:
                raise ValueError("Video already removed")
            db.execute("UPDATE episodes SET pinned=? WHERE id=?", (int(bool(value)), eid))
        self.cleanup(dry_run=True)

    @contextlib.contextmanager
    def lease(self, eid):
        token = secrets.token_hex(16)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT deleted FROM episodes WHERE id=?", (eid,)).fetchone()
            if row is None or row[0] is not None:
                raise FileNotFoundError("Expired video")
            db.execute("INSERT INTO leases VALUES (?,?,?)", (token, eid, self.clock()+120))
        try:
            yield token
        finally:
            with self.db() as db:
                db.execute("DELETE FROM leases WHERE token=?", (token,))

    def renew(self, token):
        with self.db() as db:
            db.execute("UPDATE leases SET expires=? WHERE token=?", (self.clock()+120, token))

    def cleanup(self, dry_run=False):
        policy = self.setting("policy")
        removed, chosen = [], set()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [dict(r) for r in db.execute("SELECT * FROM episodes WHERE deleted IS NULL")]
            datasets = {}
            for r in rows:
                m = json.loads(r["meta"])
                r["meta"] = m
                explicit = str(m["episode_id"]) in policy.get("selections", {}).get(m["dataset"], [])
                r["keep"] = bool(r["pinned"]) or explicit or any(all(str(m.get(k)) == str(v) for k,v in rule.items()) for rule in policy["rules"])
                datasets.setdefault(m["dataset"], []).append(r)
                if r["keep"]:
                    chosen.add(r["id"])
            for dataset, group in datasets.items():
                count = policy["datasets"].get(dataset, policy["count"])
                fixed = [r for r in group if r["keep"]]
                budget = max(0, count - len(fixed))
                pools = {o: sorted([r for r in group if r["status"] == "ready" and not r["keep"] and r["outcome"] == o], key=lambda r:r["priority"]) for o in ("success", "failure")}
                picked = []
                for outcome, target in (("success", (count+1)//2), ("failure", count//2)):
                    quota = max(0, target - sum(r["outcome"] == outcome for r in fixed))
                    take = min(quota, budget-len(picked))
                    picked.extend(pools[outcome][:take])
                used = {r["id"] for r in picked}
                spare = sorted([r for pool in pools.values() for r in pool if r["id"] not in used], key=lambda r:r["priority"])
                picked.extend(spare[:budget-len(picked)])
                chosen.update(r["id"] for r in picked)
            db.execute("UPDATE episodes SET selected=0")
            for eid in chosen:
                db.execute("UPDATE episodes SET selected=1 WHERE id=?", (eid,))
            for r in rows:
                if r["id"] in chosen or r["ready"] is None or self.clock()-r["ready"] < policy["ttl_seconds"]:
                    continue
                if db.execute("SELECT 1 FROM leases WHERE episode=? AND expires>?", (r["id"], self.clock())).fetchone():
                    continue
                files = [confined(self.root, self.root / f[0]) for f in db.execute("SELECT path FROM files WHERE episode=?", (r["id"],))]
                # Validate ALL paths before deleting even the first file.
                for p in files:
                    if p.exists() and not p.is_file():
                        raise ValueError("Registered asset is not a regular file")
                if not dry_run:
                    for p in files:
                        p.unlink(missing_ok=True)
                    folder = confined(self.root, self.root / r["directory"])
                    for directory in (folder / "previews", folder, folder.parent):
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
                    db.execute("UPDATE episodes SET status='expired',deleted=? WHERE id=?", (self.clock(), r["id"]))
                    db.execute("DELETE FROM steps WHERE episode=?", (r["id"],))
                    db.execute("DELETE FROM files WHERE episode=?", (r["id"],))
                    for cursor in db.execute("SELECT path,state FROM imports WHERE state LIKE ?",('%'+r["id"]+'%',)).fetchall():
                        state=json.loads(cursor["state"])
                        if state.get("eid")==r["id"]:
                            db.execute("UPDATE imports SET state=? WHERE path=?",(dumps({"eid":r["id"],"expired":True}),cursor["path"]))
                removed.append(r["id"])
        return {"selected": sorted(chosen), "expired": removed, "dry_run": dry_run}

    def usage(self):
        total = 0
        with self.db() as db:
            paths = [r[0] for r in db.execute("SELECT path FROM files")]
        parents=set()
        for rel in paths:
            p = self.root / rel
            if p.parent not in parents:
                confined(self.root,p.parent)
                parents.add(p.parent)
            try:
                info=p.lstat()
                if stat.S_ISLNK(info.st_mode):raise ValueError("Symlink asset forbidden")
                if stat.S_ISREG(info.st_mode):total+=info.st_size
            except FileNotFoundError:
                pass
        return total

    def capacity(self):
        if time.monotonic()-getattr(self,"_capacity_checked",0)<.5:
            return
        self._capacity_checked=time.monotonic()
        if self.usage() >= self.setting("policy")["max_bytes"]:
            self.cleanup()
            if self.usage() >= self.setting("policy")["max_bytes"]:
                raise OSError("Visualization storage limit reached; capture paused")
