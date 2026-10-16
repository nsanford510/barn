"""Scripted stand-in for ``codex app-server`` speaking the v2 JSONL protocol over stdio.

Usage: python fake_app_server.py <mode> [log_path]
Modes: ok, tool, unknown_tool, approval, input, fail, interrupt, hang, exit_after_init,
       no_init_response, noisy
Every received message is appended (as JSON) to log_path when provided; the first log line
records the process cwd and selected environment values.
"""

import json
import os
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
LOG = sys.argv[2] if len(sys.argv) > 2 else None
_next_id = 1000
_turns = 0


def log(obj):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps(obj) + "\n")


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    msg = json.loads(line)
    log({"recv": msg})
    return msg


def server_request(method, params):
    global _next_id
    _next_id += 1
    rid = _next_id
    send({"id": rid, "method": method, "params": params})
    while True:
        msg = read()
        if msg.get("id") == rid and "method" not in msg:
            return msg


def complete_turn(thread_id, turn_id, status="completed", error=None):
    send({"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread_id, "turnId": turn_id,
        "tokenUsage": {"total": {"inputTokens": 100 * _turns, "outputTokens": 10 * _turns,
                                 "totalTokens": 110 * _turns, "cachedInputTokens": 0,
                                 "reasoningOutputTokens": 0},
                       "last": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110,
                                "cachedInputTokens": 0, "reasoningOutputTokens": 0}}}})
    send({"method": "account/rateLimits/updated",
          "params": {"rateLimits": {"primary": {"usedPercent": 12.5}}}})
    send({"method": "item/completed", "params": {
        "threadId": thread_id, "turnId": turn_id, "completedAtMs": 0,
        "item": {"type": "agentMessage", "id": "m1", "text": f"done turn {_turns}"}}})
    turn = {"id": turn_id, "status": status, "items": []}
    if error:
        turn["error"] = {"message": error}
    send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})


def main():
    global _turns
    log({"cwd": os.getcwd(), "env_LINEAR_API_KEY": os.environ.get("LINEAR_API_KEY"),
         "env_SYMPHONY_TEST_SECRET": os.environ.get("SYMPHONY_TEST_SECRET")})
    if MODE == "noisy":
        sys.stderr.write("diagnostic noise that is not JSON\n")
        sys.stderr.flush()
    thread_id = "thr_1"
    while True:
        msg = read()
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            if MODE == "no_init_response":
                continue
            send({"id": rid, "result": {"userAgent": "fake/0.0"}})
            if MODE == "exit_after_init":
                sys.exit(3)
        elif method == "initialized":
            if MODE == "noisy":
                sys.stdout.write("this line is not json\n")
                sys.stdout.flush()
        elif method == "thread/start":
            send({"id": rid, "result": {"thread": {"id": thread_id}, "model": "fake",
                                        "cwd": params.get("cwd")}})
            send({"method": "thread/started", "params": {"thread": {"id": thread_id}}})
        elif method == "thread/name/set":
            send({"id": rid, "result": {}})
        elif method == "turn/start":
            _turns += 1
            turn_id = f"turn_{_turns}"
            send({"id": rid, "result": {"turn": {"id": turn_id, "status": "inProgress",
                                                 "items": []}}})
            send({"method": "turn/started", "params": {"threadId": thread_id,
                                                       "turn": {"id": turn_id}}})
            if MODE == "hang":
                time.sleep(3600)
            if MODE == "tool":
                resp = server_request("item/tool/call", {
                    "threadId": thread_id, "turnId": turn_id, "callId": "c1",
                    "tool": "file_tracker_update", "arguments": {"comment": "hello"}})
                log({"tool_response": resp})
            if MODE == "tool_done":
                resp = server_request("item/tool/call", {
                    "threadId": thread_id, "turnId": turn_id, "callId": "c3",
                    "tool": "file_tracker_update",
                    "arguments": {"state": "Done", "comment": "finished by fake agent"}})
                log({"tool_response": resp})
            if MODE == "unknown_tool":
                resp = server_request("item/tool/call", {
                    "threadId": thread_id, "turnId": turn_id, "callId": "c2",
                    "tool": "does_not_exist", "arguments": {}})
                log({"tool_response": resp})
            if MODE == "approval":
                r1 = server_request("item/commandExecution/requestApproval", {
                    "threadId": thread_id, "turnId": turn_id, "itemId": "i1"})
                r2 = server_request("item/fileChange/requestApproval", {
                    "threadId": thread_id, "turnId": turn_id, "itemId": "i2"})
                r3 = server_request("execCommandApproval", {"conversationId": "x"})
                log({"approvals": [r1, r2, r3]})
            if MODE == "input":
                server_request("item/tool/requestUserInput", {
                    "threadId": thread_id, "turnId": turn_id, "itemId": "i3",
                    "isBlocking": True,
                    "questions": [{"id": "q", "header": "h", "question": "Which branch?"}]})
                time.sleep(3600)
            if MODE == "fail":
                complete_turn(thread_id, turn_id, status="failed", error="model exploded")
            elif MODE == "interrupt":
                complete_turn(thread_id, turn_id, status="interrupted")
            else:
                complete_turn(thread_id, turn_id)
        elif rid is not None:
            send({"id": rid, "error": {"code": -32601, "message": f"unknown {method}"}})


if __name__ == "__main__":
    main()
