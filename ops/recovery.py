"""Encrypted, offline SQLite backup/restore; PostgreSQL uses the provider's full-database backup.

python -m ops.recovery backup --state PATH --file BACKUP
python -m ops.recovery restore --state NEW_PATH --file BACKUP
ATEZAIN_BACKUP_KEY is a separate Fernet key held outside application storage.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
import zipfile

from cryptography.fernet import Fernet
from ops.lease import lease

LIMIT=256*1024*1024
PATTERN=re.compile(r"(?:sessions|limits)\.db|[a-f0-9]{16}/(?:records|policy|checkpoints)\.db")


def backup(root, destination, key):
    root,destination=Path(root).resolve(),Path(destination).resolve()
    if destination.exists() or root in destination.parents:
        raise ValueError("backup destination must be new and outside application state")
    started=time.monotonic()
    with lease(root,exclusive=True), tempfile.TemporaryDirectory() as directory:
        paths=sorted(p for p in root.rglob("*.db") if PATTERN.fullmatch(p.relative_to(root).as_posix()))
        if not (root/"sessions.db") in paths or not (root/"limits.db") in paths:
            raise ValueError("complete control storage is required")
        manifest={"format":"atezain-sqlite-backup-v1","created_at":time.time(),"files":{},"heads":{}}
        size=0
        for path in paths:
            if path.is_symlink() or root not in path.resolve().parents:
                raise ValueError("state paths must not escape the root")
            rel=path.relative_to(root).as_posix()
            target=Path(directory)/rel;target.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(f"file:{path}?mode=ro",uri=True) as source, sqlite3.connect(target) as output:
                source.backup(output)
                if output.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("database integrity failure")
                if path.name=="policy.db":
                    manifest["heads"][path.parent.name]=list(output.execute("SELECT seq,hash FROM audit_head WHERE id=1").fetchone())
            raw=target.read_bytes();size+=len(raw)
            if size>LIMIT:
                raise ValueError("local backup size limit exceeded; use a database backup service")
            manifest["files"][rel]=hashlib.sha256(raw).hexdigest()
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json",json.dumps(manifest,sort_keys=True))
            for rel in manifest["files"]:
                archive.write(Path(directory)/rel,rel)
        encrypted=Fernet(key.encode()).encrypt(buffer.getvalue())
        with destination.open("xb") as output:
            os.chmod(destination,0o600);output.write(encrypted)
    return {"files":len(paths),"seconds":round(time.monotonic()-started,3),"heads":manifest["heads"],
            "backup_sha256":hashlib.sha256(encrypted).hexdigest()}


def restore(source, root, key):
    source,root=Path(source),Path(root).resolve()
    if root.exists():
        raise ValueError("restore requires a new state directory")
    if source.stat().st_size>LIMIT*2:
        raise ValueError("backup size limit exceeded")
    raw=Fernet(key.encode()).decrypt(source.read_bytes())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist()
        if sum(i.file_size for i in infos)>LIMIT or len(infos)>10000 or len({i.filename for i in infos})!=len(infos):
            raise ValueError("invalid backup size or duplicate entries")
        manifest=json.loads(archive.read("manifest.json"))
        if manifest["format"]!="atezain-sqlite-backup-v1" or set(archive.namelist())!=set(manifest["files"])|{"manifest.json"}:
            raise ValueError("invalid backup manifest")
        for rel,digest in manifest["files"].items():
            if not PATTERN.fullmatch(rel) or hashlib.sha256(archive.read(rel)).hexdigest()!=digest:
                raise ValueError("backup content verification failed")
        # Build and verify beside the destination. Only a completely validated restore is promoted.
        root.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent,prefix=".atezain-restore-") as directory:
            staging=Path(directory)/"state";staging.mkdir(mode=0o700)
            for rel in manifest["files"]:
                target=staging/rel;target.parent.mkdir(exist_ok=True,mode=0o700)
                target.write_bytes(archive.read(rel));target.chmod(0o600)
                with sqlite3.connect(f"file:{target}?mode=ro",uri=True) as conn:
                    if conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                        raise ValueError("restored database is not intact")
            from policy import Store
            for sid,head in manifest["heads"].items():
                store=Store(str(staging/sid/"policy.db"))
                try:
                    if not store.audit_verify(tuple(head)) or store.audit_anomalies():
                        raise ValueError("restored audit requires investigation")
                finally:
                    store.close()
            staging.rename(root)
    return {"files":len(manifest["files"]),"heads":manifest["heads"],"restored":True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["backup","restore"])
    parser.add_argument("--state",required=True)
    parser.add_argument("--file",required=True)
    args=parser.parse_args()
    key=os.environ.get("ATEZAIN_BACKUP_KEY","")
    try:
        result=backup(args.state,args.file,key) if args.command=="backup" else restore(args.file,args.state,key)
    except Exception as exc:
        print(json.dumps({"ok":False,"error":type(exc).__name__,"note":"backup/restore refused; check offline state, destination and backup key"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
