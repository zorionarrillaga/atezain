"""Operator retention job. Dry run by default: python -m ops.manage purge-expired [--apply]."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["purge-expired", "sync-accounting"])
    parser.add_argument("--apply", action="store_true", help="apply the selected maintenance operation")
    args = parser.parse_args()
    from api.app import sessions, purge_session
    if args.command == "sync-accounting":
        from api.app import xero, state_of, sync_accounting
        if xero is None:
            print(json.dumps({"ok": False, "error": "accounting_not_configured"}))
            return 1
        expired = set(sessions.expired())
        eligible = [sid for sid in sessions.ids() if sid not in expired and xero.store.get(sid) and xero.store.get(sid)["state"] != "disconnected"]
        failed = []
        if args.apply:
            for sid in eligible:
                try:
                    with sessions.lock(sid):
                        sync_accounting(state_of(sid))
                except Exception as exc:
                    failed.append({"session": sid, "error": type(exc).__name__})
        print(json.dumps({"eligible":len(eligible),"applied":args.apply,"failed":failed}))
        return bool(failed)
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
