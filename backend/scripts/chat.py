"""Drive the running server from the terminal and print the agent's trace.

usage: uv run python scripts/chat.py "message" ["follow-up" ...] [--approve | --reject]
Each message is sent in the same session; pending trade confirmations are
approved/rejected automatically when a flag is given.
"""

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"


def stream(client: httpx.Client, url: str, body: dict, state: dict) -> list[dict]:
    confirms = []
    with client.stream("POST", url, json=body, timeout=600) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            e = json.loads(line[6:])
            t = e["type"]
            if t == "session":
                state["sid"] = e["session_id"]
            elif t == "text":
                print(e["delta"], end="", flush=True)
            elif t == "tool_call":
                print(f"\n  → {e['name']} {json.dumps(e['args'], ensure_ascii=False)}")
            elif t == "tool_result":
                print(f"  {'✓' if e['ok'] else '✗'} {e['name']}: {json.dumps(e['result'], ensure_ascii=False)[:180]}")
            elif t == "confirm":
                print(f"  ⏸ CONFIRM {e['action']['summary']}  risk={e['action']['risk']}")
                confirms.append(e["action"])
            elif t == "action_update":
                print(f"  ● action {e['action']['status']}: {json.dumps(e['action']['result'], ensure_ascii=False)[:180]}")
            elif t == "error":
                print(f"\n  !! {e['message']}")
    print()
    return confirms


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    decide = "--approve" in sys.argv or ("--reject" in sys.argv and False)
    auto = "--approve" in sys.argv or "--reject" in sys.argv
    state: dict = {"sid": None}
    with httpx.Client(base_url=BASE) as c:
        for msg in args:
            print(f"\n### USER: {msg}")
            t0 = time.time()
            confirms = stream(c, "/api/chat", {"message": msg, "session_id": state["sid"]}, state)
            for a in confirms if auto else []:
                print(f"### {'APPROVE' if decide else 'REJECT'} {a['id']}")
                stream(c, f"/api/actions/{a['id']}", {"approve": decide}, state)
            print(f"[{time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
