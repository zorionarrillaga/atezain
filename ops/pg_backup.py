"""Encrypted complete PostgreSQL backups using the vendor pg_dump/pg_restore tools.

Uses ATEZAIN_BACKUP_DSN for backup, ATEZAIN_RESTORE_DSN for restore and ATEZAIN_BACKUP_KEY.
Restore accepts only an empty database and never cleans or overwrites an existing one.
Provider snapshots remain preferable for larger databases; local encryption is bounded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from cryptography.fernet import Fernet
from psycopg import connect
from psycopg.conninfo import conninfo_to_dict

LIMIT=256*1024*1024


def pg_environment(dsn):
    # libpq environment fields keep passwords out of process arguments and subprocess output.
    fields=conninfo_to_dict(dsn)
    names={"host":"PGHOST","hostaddr":"PGHOSTADDR","port":"PGPORT","dbname":"PGDATABASE","user":"PGUSER",
        "password":"PGPASSWORD","sslmode":"PGSSLMODE","sslrootcert":"PGSSLROOTCERT","sslcert":"PGSSLCERT",
        "sslkey":"PGSSLKEY","channel_binding":"PGCHANNELBINDING","options":"PGOPTIONS","connect_timeout":"PGCONNECT_TIMEOUT"}
    if set(fields)-set(names):
        raise ValueError("unsupported backup DSN option")
    env={k:v for k,v in os.environ.items() if not k.startswith("PG")}
    env.update({names[k]:str(v) for k,v in fields.items()});env["PGCONNECT_TIMEOUT"]="10"
    return env


def run(command,env):
    result=subprocess.run(command,env=env,capture_output=True,timeout=900)
    if result.returncode:
        raise RuntimeError("PostgreSQL backup tool failed; inspect configuration without logging credentials")


def backup(path,dsn,key):
    tool=shutil.which("pg_dump")
    if not tool or not dsn:
        raise ValueError("pg_dump and an explicit backup DSN are required")
    destination=Path(path)
    if destination.exists():
        raise ValueError("backup destination exists")
    with tempfile.TemporaryDirectory() as directory:
        dump=Path(directory)/"database.dump"
        run([tool,"--format=custom","--no-owner","--no-acl","--file",str(dump)],pg_environment(dsn))
        if dump.stat().st_size>LIMIT:
            raise ValueError("backup encryption size limit exceeded; use provider backup")
        encrypted=Fernet(key.encode()).encrypt(dump.read_bytes())
        with destination.open("xb") as output:
            destination.chmod(0o600);output.write(encrypted)
    return {"backup_sha256":hashlib.sha256(encrypted).hexdigest(),"bytes":len(encrypted)}


def restore(path,dsn,key):
    tool=shutil.which("pg_restore")
    if not tool or not dsn:
        raise ValueError("pg_restore and an explicit restore DSN are required")
    if Path(path).stat().st_size>LIMIT*2:
        raise ValueError("restore size limit exceeded")
    with connect(dsn,connect_timeout=10) as conn:
        count=conn.execute("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
            "AND c.relkind IN ('r','p','v','m','S','f')").fetchone()[0]
        if count:
            raise ValueError("restore database is not empty")
    raw=Fernet(key.encode()).decrypt(Path(path).read_bytes())
    env=pg_environment(dsn)
    with tempfile.TemporaryDirectory() as directory:
        dump=Path(directory)/"restore.dump";dump.write_bytes(raw);dump.chmod(0o600)
        run([tool,"--exit-on-error","--single-transaction","--no-owner","--no-acl","--dbname",env.get("PGDATABASE","postgres"),str(dump)],env)
    return {"restored":True,"note":"keep this instance isolated; reconcile deletions and verify independently saved audit anchors before enabling access"}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("command",choices=["backup","restore"]);parser.add_argument("--file",required=True);args=parser.parse_args()
    started=time.monotonic()
    try:
        key=os.environ["ATEZAIN_BACKUP_KEY"]
        result=backup(args.file,os.getenv("ATEZAIN_BACKUP_DSN"),key) if args.command=="backup" else restore(args.file,os.getenv("ATEZAIN_RESTORE_DSN"),key)
    except Exception as exc:
        print(json.dumps({"ok":False,"error":type(exc).__name__,"note":"operation refused; verify tool, isolated database, destination and backup key"}));return 1
    print(json.dumps({**result,"seconds":round(time.monotonic()-started,3)}));return 0


if __name__=="__main__":
    raise SystemExit(main())
