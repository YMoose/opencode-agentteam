#!/usr/bin/env python3
"""Run the public-plugin probe against an isolated, dependency-ready source snapshot."""

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import tempfile
import time
from urllib.parse import urlencode, urlparse


class Host:
    def __init__(self, source, state, plugin, output, generation):
        self.seq = 0
        self.responses = queue.Queue()
        self.log = (output / f"host-{generation}.log").open("w")
        env = {"PATH": os.environ["PATH"], "OPENCODE_TEST_HOME": str(state / "home")}
        for key, suffix in [("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"), ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("TMPDIR", "tmp")]:
            env[key] = str(state / suffix)
            Path(env[key]).mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [shutil.which("bun"), str(source / "packages/server/p0/worker.ts"), str(state), str(plugin)],
            cwd=source / "packages/server", env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            ready = self.responses.get(timeout=45)
            assert ready.get("ready"), ready
        except BaseException:
            self.stop(force=True)
            raise

    def read(self):
        for line in self.process.stdout:
            self.log.write(line)
            self.log.flush()
            try:
                item = json.loads(line)
                if isinstance(item, dict) and ("ready" in item or "id" in item):
                    self.responses.put(item)
            except json.JSONDecodeError:
                pass
        self.responses.put({"exit": self.process.wait()})

    def request(self, path, body=None, method=None, status=200):
        self.seq += 1
        payload = {"id": self.seq, "path": path, "method": method or ("POST" if body is not None else "GET")}
        if body is not None:
            payload["body"] = body
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        result = self.responses.get(timeout=45)
        assert result.get("id") == self.seq, result
        assert all(urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"} for url in result.get("blocked", [])), result
        assert result.get("status") == status, result
        return result["body"]

    def probe(self, project, op, **args):
        return self.request("/api/rpc/team-p0/probe?" + urlencode({"location[directory]": str(project)}), {"input": {"op": op, "args": args}}).get("output")

    def stop(self, force=False):
        if self.process.poll() is None:
            if force:
                self.process.kill()
            else:
                self.process.stdin.write('{"stop":true}\n')
                self.process.stdin.flush()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.log.close()


def until(callback, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        value = callback()
        if predicate(value):
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"condition did not converge: {json.dumps(value)[:1500]}")
        time.sleep(0.02)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode", type=Path, required=True, help="Isolated source snapshot with matching installed dependencies")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    source = args.opencode.resolve()
    if (source / ".git").exists():
        raise SystemExit("Use a git archive snapshot; this probe does not write into a working checkout")
    manifest = source / "p0-source.json"
    if not manifest.exists() or json.loads(manifest.read_text()).get("commit") != args.source_commit:
        raise SystemExit("Source provenance is missing or mismatched; export the snapshot with prepare.py")
    here = Path(__file__).resolve().parent
    output = here / "results" / time.strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    state = Path(tempfile.mkdtemp(prefix="opencode-team-p0-state-"))
    stage = source / "packages/server/p0"
    stage.mkdir(exist_ok=True)
    shutil.copy2(here / "worker.ts", stage / "worker.ts")
    shutil.copytree(here / "plugin", stage / "plugin", dirs_exist_ok=True)
    projects = [state / "project-a", state / "project-b"]
    for project in projects:
        project.mkdir()
    report = {"sourceCommit": args.source_commit, "bun": subprocess.check_output(["bun", "--version"], text=True).strip(), "checks": [], "output": str(output), "state": str(state)}

    def record(name, evidence):
        report["checks"].append({"name": name, "status": "passed", "evidence": evidence})
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print("PASS", name, flush=True)

    host = None
    try:
        host = Host(source, state, stage / "plugin", output, 1)
        a, b = projects
        inspect = host.probe(a, "inspect")
        record("public plugin loads and registers RPC", {"instance": inspect["instance"], "sessionMethods": inspect["sessionMethods"]})
        if args.smoke:
            report["status"] = "smoke-passed"
            return
        spec = {"agent": "p0-worker", "model": {"providerID": "team-p0", "id": "fixture"}}
        root = host.probe(a, "create", id="ses_p0_root", title="P0 root", metadata={"rootMarker": "inherited"}, permissions=[{"action": "*", "resource": "*", "effect": "deny"}], **spec)
        inherited = host.probe(a, "create", id="ses_p0_inherited", parentID=root["id"], **spec)
        alpha = host.probe(a, "create", id="ses_p0_alpha", parentID=root["id"], metadata={"member": "alpha"}, permissions=[{"action": "*", "resource": "*", "effect": "allow"}], **spec)
        beta = host.probe(a, "create", id="ses_p0_beta", parentID=root["id"], metadata={"member": "beta"}, permissions=[{"action": "*", "resource": "*", "effect": "allow"}], **spec)
        adopted = host.probe(a, "create", id=alpha["id"], title="must-not-replace", metadata={"wrong": True}, **spec)
        assert adopted["parentID"] == root["id"] and adopted["metadata"] == {"member": "alpha"}, adopted
        assert inherited["metadata"] == root["metadata"] and inherited["permissions"] == root["permissions"], inherited
        record("explicit child identity, inheritance and ID adoption", {"root": root, "alpha": alpha, "inherited": inherited, "adopted": adopted})
        for member, token in [(alpha, "P0_SPEC_ALPHA"), (beta, "P0_SPEC_BETA")]:
            host.probe(a, "put", sessionID=member["id"], key="team.member", value={"instructions": token, "version": 1})
        denied = host.probe(a, "permission", sessionID=inherited["id"], action="edit", resources=["/tmp/p0"])
        allowed = host.probe(a, "permission", sessionID=alpha["id"], action="edit", resources=["/tmp/p0"])
        assert not denied["allowed"] and allowed["allowed"], (denied, allowed)
        record("explicit Session permissions replace parent inheritance", {"denied": denied, "allowed": allowed})
        first = host.probe(a, "synthetic", sessionID=beta["id"], id="msg_p0_pending", text="first", metadata={"originCallId": "call-first"}, resume=False, delivery="queue")
        second = host.probe(a, "synthetic", sessionID=beta["id"], id="msg_p0_pending", text="changed", metadata={"originCallId": "call-changed"}, resume=False, delivery="steer")
        assert first == second, (first, second)
        pending = host.request(f'/api/session/{beta["id"]}/inbox')
        assert "call-first" in json.dumps(pending) and "call-changed" not in json.dumps(pending), pending
        host.request(f'/api/session/{alpha["id"]}/synthetic', {"id": "msg_p0_pending", "text": "wrong-session", "resume": False}, status=409)
        host.request(f'/api/session/{beta["id"]}/prompt', {"id": "msg_p0_pending", "text": "wrong-type", "resume": False}, status=409)
        record("pending input retry retains original content and rejects cross-session reuse", {"receipt": first, "inbox": pending})
        host.probe(a, "prompt", sessionID=alpha["id"], id="msg_p0_alpha", text="P0_BARRIER_A")
        host.probe(a, "prompt", sessionID=beta["id"], id="msg_p0_beta", text="P0_BARRIER_B")
        running = until(lambda: host.probe(a, "inspect"), lambda item: set(item["barriers"]) == {"A", "B"})
        for session, own, other in [(alpha, "P0_SPEC_ALPHA", "P0_SPEC_BETA"), (beta, "P0_SPEC_BETA", "P0_SPEC_ALPHA")]:
            own_requests = [item for item in running["requests"] if item["sessionID"] == session["id"]]
            assert own_requests and own in json.dumps(own_requests) and other not in json.dumps(own_requests), own_requests
        record("two sessions execute concurrently with isolated durable instructions", {"barriers": running["barriers"], "requests": running["requests"]})
        host.probe(a, "release")
        host.probe(a, "wait", sessionID=alpha["id"])
        host.probe(a, "wait", sessionID=beta["id"])
        retried = host.probe(a, "synthetic", sessionID=beta["id"], id="msg_p0_pending", text="changed-after-delivery", resume=False)
        assert "first" in json.dumps(retried) and "changed-after-delivery" not in json.dumps(retried), retried
        assert host.request(f'/api/session/{beta["id"]}/inbox')["data"] == []
        record("delivered input retry does not enqueue a duplicate", retried)
        host.probe(a, "prompt", sessionID=alpha["id"], id="msg_p0_tool", text=f'P0_TOOL_SEND:{beta["id"]}')
        sent = until(lambda: host.probe(a, "inspect"), lambda item: len(item["calls"]) >= 1)
        host.probe(a, "wait", sessionID=alpha["id"])
        tool_inbox = host.request(f'/api/session/{beta["id"]}/inbox')
        assert "call-p0-send" in json.dumps(tool_inbox) and alpha["id"] in json.dumps(tool_inbox), tool_inbox
        record("real tool execution preserves source message and call identity in peer inbox", {"calls": sent["calls"], "inbox": tool_inbox})
        before_compact = len(host.probe(a, "inspect")["requests"])
        host.probe(a, "compact", sessionID=alpha["id"], id="msg_p0_compact")
        host.probe(a, "wait", sessionID=alpha["id"])
        compact_context = host.probe(a, "context", sessionID=alpha["id"])
        assert any(item.get("type") == "compaction" and item.get("status") == "completed" for item in compact_context), compact_context
        host.probe(a, "prompt", sessionID=alpha["id"], id="msg_p0_postcompact", text="after compaction")
        host.probe(a, "wait", sessionID=alpha["id"])
        compacted = host.probe(a, "inspect")
        latest = [item for item in compacted["requests"][before_compact:] if item["sessionID"] == alpha["id"]]
        assert latest and "P0_SPEC_ALPHA" in json.dumps(latest[-1]), latest
        record("instruction remains in model context after compaction", {"context": host.probe(a, "context", sessionID=alpha["id"]), "latest": latest[-1]})
        host.probe(a, "set", key="p0-persist", value={"revision": 7, "member": alpha["id"]})
        instance_b = host.probe(b, "inspect")
        shared = host.probe(b, "getStorage", key="p0-persist")
        assert inspect["instance"] != instance_b["instance"] and shared["value"]["revision"] == 7
        race = host.probe(a, "race")
        assert race == {"before": [0, 0], "after": 1}, race
        record("storage is shared across Location instances and read-modify-write lacks CAS", {"otherInstance": instance_b["instance"], "shared": shared, "race": race})
        host.probe(a, "sqliteSet", key="native-db", value={"revision": 8})
        native_db = host.probe(b, "sqliteGet", key="native-db")
        assert json.loads(native_db["value"]) == {"revision": 8}, native_db
        record("public plugin loads bun sqlite and shares explicit database across Locations", native_db)
        event_snapshot = host.probe(a, "inspect")["events"]
        event_types = {event["type"] for event in event_snapshot}
        assert {"session.inbox.enqueued", "session.inbox.delivered", "session.execution.succeeded"} <= event_types, event_types
        assert all(set(event["data"]) == {"sessionID"} for event in event_snapshot if event["type"] in {"session.execution.started", "session.execution.succeeded"})
        record("live events expose input lifecycle and Session execution facts", event_snapshot)
        host.stop(force=True)
        host = Host(source, state, stage / "plugin", output, 2)
        entries = host.probe(a, "entries", sessionID=alpha["id"])
        persisted = host.probe(a, "getStorage", key="p0-persist")
        resumed_inbox = host.request(f'/api/session/{beta["id"]}/inbox')
        assert "P0_SPEC_ALPHA" in json.dumps(entries) and persisted["value"]["revision"] == 7
        assert "call-p0-send" in json.dumps(resumed_inbox), resumed_inbox
        after_boot = host.probe(a, "inspect")
        assert not after_boot["events"], after_boot["events"]
        log = host.request(f'/api/experimental/session/{alpha["id"]}/log?follow=false')
        logged = [json.loads(line[5:].strip()) for line in log.splitlines() if line.startswith("data:")]
        durable = [event for event in logged if "durable" in event]
        assert durable and any(event["type"] == "session.inbox.delivered" for event in durable), logged
        synced = [event for event in logged if event["type"] == "log.synced"]
        assert len(synced) == 1, logged
        cursor = synced[0]["seq"]
        tail = host.request(f'/api/experimental/session/{alpha["id"]}/log?follow=false&after={cursor}')
        tail_events = [json.loads(line[5:].strip()) for line in tail.splitlines() if line.startswith("data:")]
        assert not [event for event in tail_events if "durable" in event], tail_events
        assert "inbox" not in after_boot["sessionMethods"] and "log" not in after_boot["sessionMethods"]
        record("HTTP durable log replays history with cursor while plugin event subscription stays live-only", {"eventCount": len(durable), "watermark": cursor, "emptyTail": tail_events})
        assert json.loads(host.probe(a, "sqliteGet", key="native-db")["value"]) == {"revision": 8}
        host.probe(a, "prompt", sessionID=alpha["id"], id="msg_p0_restart", text="after process restart")
        host.probe(a, "wait", sessionID=alpha["id"])
        restarted = host.probe(a, "inspect")
        assert "P0_SPEC_ALPHA" in json.dumps(restarted["requests"]), restarted
        record("process kill and restart preserve Session, instruction, pending inbox and storage", {"entries": entries, "persisted": persisted, "inbox": resumed_inbox, "requests": restarted["requests"]})
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = repr(error)
        raise
    finally:
        if host:
            host.stop()
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print("REPORT", output / "report.json", flush=True)


if __name__ == "__main__":
    main()
