"""Append-only audit chain (format compatible with the previous hub version)."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from hub.models import AuditEntry
from hub.state import StateDb
from hub.vault import Vault, verify_signature

GENESIS = "0" * 64
ANCHOR = "audit.anchor"
HEAD_TRANSPORT: httpx.BaseTransport | None = None  # tests swap the webhook transport


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def entry_hash(seq: int, prev: str, event: str) -> str:
    return hashlib.sha256(f"hub-audit:v1|{seq}|{prev}|{event}".encode()).hexdigest()


def _head_msg(seq: int, digest: str, event_id: int) -> bytes:
    return f"hub-audit-head:v1:{seq}:{digest}:{event_id}".encode()


def _check_event(
    line: dict[str, Any], seq: int, previous: str, event_id: int
) -> tuple[str | None, tuple[int, str, int]]:
    event = line["event"]
    digest = entry_hash(line["seq"], line["prev"], event)
    if line["seq"] != seq + 1 or line["prev"] != previous or line["hash"] != digest:
        return "invalid chain", (seq, previous, event_id)
    parsed = json.loads(event)
    AuditEntry.model_validate(parsed)
    if parsed["id"] <= event_id:
        return "event id did not increase", (seq, previous, event_id)
    return None, (line["seq"], digest, parsed["id"])


@dataclass
class Verification:
    ok: bool
    entries: int
    last_seq: int
    last_hash: str
    problems: list[str]


CHECKPOINT_EVERY = 100


class AuditChain:
    """Append and verify standard chain.jsonl/head.json files."""

    def __init__(self, directory: Path, vault: Vault | None = None, db: StateDb | None = None) -> None:
        self.dir, self.vault, self.db = directory, vault, db
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "archive").mkdir(exist_ok=True)
        self.path = self.dir / "chain.jsonl"
        self.head_path = self.dir / "head.json"
        self.blocked: str | None = None
        self.file_error = self.hook_error = ""
        self.last_checkpoint = 0
        # Head webhook (HUB_AUDIT_HEAD_URL): one thread always sends the most recent head.
        self._hook = threading.Condition()
        self._hook_next: dict[str, Any] | None = None
        self._hook_busy = False
        self._hook_thread: threading.Thread | None = None
        self._hook_stop = False
        v = self.verify()
        if not db:  # with a shared database, only under the lock of `append_shared`
            self._checkpoint_if_due(v.last_seq, v.last_hash)

    def _lines(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(s) for s in self.path.read_text(encoding="utf-8").splitlines() if s]

    def _check_checkpoint(self, line: dict[str, Any], seq: int, previous: str) -> str | None:
        msg = f"hub-audit-checkpoint:v1:{line['seq']}:{line['hash']}:{line['at']}".encode()
        if self.vault and (
            line["pk"] not in self.vault.trusted_public_keys
            or not verify_signature(line["pk"], msg, line["sig"])
        ):
            return "invalid checkpoint signature"
        if line["seq"] != seq or line["hash"] != previous:
            return "checkpoint mismatch"
        return None

    def _scan(self, problems: list[str]) -> tuple[int, str, int]:
        """Walks chain.jsonl; returns (seq, hash, event id) of the last valid event."""
        seq, previous, event_id = 0, GENESIS, 0
        try:
            for n, line in enumerate(self._lines(), 1):
                if line["t"] == "e":
                    problem, state = _check_event(line, seq, previous, event_id)
                    if problem:
                        problems.append(f"line {n}: {problem}")
                        break
                    seq, previous, event_id = state
                elif line["t"] == "c":
                    if problem := self._check_checkpoint(line, seq, previous):
                        problems.append(f"line {n}: {problem}")
                        break
                    self.last_checkpoint = seq
                else:
                    problems.append(f"line {n}: unknown record")
                    break
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"invalid chain: {e}")
        return seq, previous, event_id

    def _check_head(self, seq: int, previous: str, event_id: int) -> str | None:
        try:
            head = json.loads(self.head_path.read_text(encoding="utf-8"))
            if (head["seq"], head["hash"], head["event_id"]) != (seq, previous, event_id):
                return "signed head does not match chain"
            if self.vault and (
                head.get("pk") not in self.vault.trusted_public_keys
                or not verify_signature(head["pk"], _head_msg(seq, previous, event_id), head.get("sig", ""))
            ):
                return "invalid head signature"
        except FileNotFoundError:
            if seq:
                return "signed head missing"
        except (OSError, ValueError, KeyError, TypeError) as e:
            return f"invalid head: {e}"
        return None

    def _check_anchor(self, seq: int, previous: str) -> str | None:
        if not self.vault:
            return None
        try:
            raw = self.vault.protected(ANCHOR)
            if raw:
                anchor = json.loads(raw)
                if seq < anchor["seq"] or (seq == anchor["seq"] and previous != anchor["hash"]):
                    return "chain older than protected anchor"
        except (ValueError, KeyError) as e:
            return f"invalid protected anchor: {e}"
        return None

    def verify(self) -> Verification:
        problems: list[str] = []
        seq, previous, event_id = self._scan(problems)
        for problem in (self._check_head(seq, previous, event_id), self._check_anchor(seq, previous)):
            if problem:
                problems.append(problem)
        if (ext := self.external_head()) and (
            seq < ext["seq"] or (seq == ext["seq"] and previous != ext["hash"])
        ):
            problems.append("chain older than external head anchor")
        self.blocked = problems[0] if problems else None
        return Verification(not problems, seq, seq, previous, problems)

    def append(self, event: dict[str, Any], migrated: bool = False) -> int:
        if self.db:
            raise RuntimeError("use append_shared() with a state database")
        return self._append(event, migrated)

    async def append_shared(self, event: dict[str, Any], migrated: bool = False) -> int:
        if self.db:
            async with self.db.file_lock("audit/chain.jsonl", write=True):
                self.verify()
                return self._append(event, migrated)
        return self._append(event, migrated)

    def _append(self, event: dict[str, Any], migrated: bool) -> int:
        AuditEntry.model_validate(event)
        if self.blocked:
            raise RuntimeError(f"audit chain blocked: {self.blocked}")
        report = self.verify()
        seq, prev = report.last_seq + 1, report.last_hash
        encoded = _json(event)
        digest = entry_hash(seq, prev, encoded)
        line: dict[str, Any] = {"t": "e", "seq": seq, "prev": prev, "event": encoded, "hash": digest}
        if migrated:
            line["migrated"] = True
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(_json(line) + "\n")
            f.flush()
            os.fsync(f.fileno())
        head: dict[str, Any] = {"seq": seq, "hash": digest, "event_id": event["id"], "sig": "", "pk": ""}
        if self.vault:
            head["pk"] = self.vault.signing_public_key
            head["sig"] = self.vault.sign(_head_msg(seq, digest, event["id"]))
        tmp = self.head_path.with_suffix(".json.tmp")
        tmp.write_text(_json(head), encoding="utf-8")
        os.replace(tmp, self.head_path)
        if self.vault:
            self.vault.set_protected(
                ANCHOR,
                _json(
                    {
                        "initialized": True,
                        "seq": seq,
                        "hash": digest,
                        "event_id": event["id"],
                        "floor_seq": 0,
                        "archived": 0,
                        "at": time.time_ns() // 1_000_000,
                    }
                ),
            )
        self.blocked = None
        self._publish_head(head)
        self._checkpoint_if_due(seq, digest)
        return seq

    @staticmethod
    def head_file() -> Path | None:
        """External anchor (`HUB_AUDIT_HEAD_FILE`): JSONL with the signed head, outside the data volume."""
        raw = os.getenv("HUB_AUDIT_HEAD_FILE", "").strip()
        return Path(raw) if raw else None

    def external_head(self) -> dict[str, Any] | None:
        f = self.head_file()
        if f is None:
            return None
        try:
            last = f.read_bytes().rstrip(b"\n").rsplit(b"\n", 1)[-1]
            return dict(json.loads(last)) if last else None
        except (OSError, ValueError):
            return None

    @property
    def head_error(self) -> str:
        return "; ".join(e for e in (self.hook_error, self.file_error) if e)

    @staticmethod
    def head_url() -> str:
        return os.getenv("HUB_AUDIT_HEAD_URL", "").strip()

    def head_target(self) -> str:
        """Where the head is published (URL and/or file); empty = local only."""
        f = self.head_file()
        return " e ".join(x for x in (self.head_url(), str(f) if f else "") if x)

    def _queue_head(self, head: dict[str, Any]) -> None:
        with self._hook:
            self._hook_next = head
            if self._hook_thread is None:
                self._hook_thread = threading.Thread(target=self._hook_loop, name="audit-head", daemon=True)
                self._hook_thread.start()
            self._hook.notify_all()

    def _hook_loop(self) -> None:
        while True:
            with self._hook:
                while self._hook_next is None and not self._hook_stop:
                    self._hook.wait()
                if self._hook_stop:
                    return
                head, self._hook_next, self._hook_busy = self._hook_next, None, True
            token = os.getenv("HUB_AUDIT_HEAD_TOKEN", "").strip()
            headers = {"authorization": f"Bearer {token}"} if token else {}
            try:
                with httpx.Client(transport=HEAD_TRANSPORT, timeout=5) as c:
                    r = c.post(self.head_url(), json=head, headers=headers)
                self.hook_error = "" if r.status_code < 300 else f"webhook respondeu HTTP {r.status_code}"
            except httpx.HTTPError as e:
                self.hook_error = f"webhook: {type(e).__name__}: {e}"[:200]
            with self._hook:
                self._hook_busy = False
                self._hook.notify_all()

    def flush_head(self, timeout: float) -> bool:
        """Waits for the webhook to send what is queued (tests and shutdown)."""
        with self._hook:
            return self._hook.wait_for(lambda: self._hook_next is None and not self._hook_busy, timeout)

    def close(self) -> None:
        with self._hook:
            self._hook_stop = True
            self._hook.notify_all()

    def _publish_head(self, head: dict[str, Any]) -> None:
        if self.head_url():
            self._queue_head({**head, "at": time.time_ns() // 1_000_000, "host": socket.gethostname()})
        f = self.head_file()
        if f is None:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a", encoding="utf-8", newline="\n") as out:
                out.write(
                    _json({**head, "at": time.time_ns() // 1_000_000, "host": socket.gethostname()}) + "\n"
                )
                out.flush()
                os.fsync(out.fileno())
            self.file_error = ""
        except OSError as e:
            self.file_error = f"{type(e).__name__}: {e}"[:200]

    def _checkpoint_if_due(self, seq: int, digest: str) -> None:
        """Every `CHECKPOINT_EVERY` events, a signed line (Ed25519) pins the position in the chain."""
        if not self.vault or self.blocked or seq - self.last_checkpoint < CHECKPOINT_EVERY or seq == 0:
            return
        at = time.time_ns() // 1_000_000
        sig = self.vault.sign(f"hub-audit-checkpoint:v1:{seq}:{digest}:{at}".encode())
        line = {
            "t": "c",
            "seq": seq,
            "hash": digest,
            "at": at,
            "pk": self.vault.signing_public_key,
            "sig": sig,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(_json(line) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.last_checkpoint = seq

    def report(self) -> dict[str, Any]:
        """Result of the "Verificar integridade" (Verify integrity) action, in the previous hub format (single chain, no archived segments)."""
        v, lines = self.verify(), self._lines()
        anchor = 0
        if self.vault and (raw := self.vault.protected(ANCHOR)):
            anchor = int(json.loads(raw).get("seq", 0))
        return {
            "ok": v.ok,
            "entries": v.entries,
            "checkpoints": sum(1 for x in lines if x.get("t") == "c"),
            "segments": 1,
            "from_seq": 0,
            "last_seq": v.last_seq,
            "last_hash": v.last_hash,
            "migrated": sum(1 for x in lines if x.get("migrated")),
            "anchor_seq": anchor,
            "problems": v.problems[:50],
        }

    def export(self) -> str:
        """Whole chain (JSONL) and, on the last line, the proof: signed head, public keys and the verification."""
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        try:
            head = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            head = None
        proof = {
            "t": "proof",
            "head": head,
            "archive_manifest": None,
            "public_keys": list(self.vault.trusted_public_keys) if self.vault else [],
            "algorithm": 'sha256("hub-audit:v1|" + seq + "|" + prev + "|" + event); checkpoints Ed25519 sobre "hub-audit-checkpoint:v1:{seq}:{hash}:{at}"',
            "verification": self.report(),
        }
        return text + _json(proof) + chr(10)
