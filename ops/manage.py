"""Operator retention job. Dry run by default: python -m ops.manage purge-expired [--apply]."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["purge-expired"])
    parser.add_argument("--apply", action="store_true", help="delete expired workspaces from active storage")
    args = parser.parse_args()
    from api.app import sessions, purge_session
    expired = sessions.expired()
    failed = []
    if args.apply:
        for sid in expired:
            try:
                with sessions.lock(sid):
                    purge_session(sid)
            except Exception as exc:
                failed.append({"session": sid, "error": type(exc).__name__})
    print(json.dumps({"eligible": len(expired), "applied": args.apply, "failed": failed}))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
