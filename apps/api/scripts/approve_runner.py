# -*- coding: utf-8 -*-
"""轮询 /agent/permissions/pending 并自动允许（execute_code SSE 冒烟用）。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8010"


def main() -> None:
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/agent/permissions/pending", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(0.3)
            continue
        for rq in data.get("requests") or []:
            if rq.get("status") == "pending":
                body = json.dumps({"request_id": rq["id"], "allowed": True, "remember": False}).encode("utf-8")
                req = urllib.request.Request(BASE + "/agent/approve", data=body,
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as r:
                    print("[approver] approved", r.read().decode("utf-8"), flush=True)
                return
        time.sleep(0.3)
    print("[approver] no pending request seen", flush=True)


if __name__ == "__main__":
    sys.exit(main())
