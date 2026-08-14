#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Owner-gate board bridge: one Craft session card per open owner gate.

A deterministic operator-side projection — no LLM decides anything. Every open
owner gate gets a `🚦 <project> · <gateId>` card whose notes carry the exact
question and choices. The owner resolves a gate by typing exactly one of its
choices as a message into the card; the bridge reads only owner-typed user
messages from the durable session log, resolves through `owner-gate.py` with
`direct-owner` authority and the message as auditable evidence, then marks and
archives the card. A gate resolved elsewhere archives its card on the next
pass. Project HOLD gates require the exact `RESUME` choice, unchanged.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

RUNTIME = common.RUNTIME
GATES = RUNTIME / "owner-gates"
BOARD = RUNTIME / "owner-gate-board.json"
LOCK = RUNTIME / "owner-gate-board.lock"
OWNER_GATE = HERE / "owner-gate.py"
CLI = os.environ.get("CRAFT_RPC_CLI", "")
WORKSPACE_ID = os.environ.get("CRAFT_WORKSPACE_ID", "")
# A gate card is an inert projection. It is created on an explicitly configured
# cheap connection/model so an accidental reply never spends an expensive
# provider turn, and any turn the owner's choice starts is cancelled at once.
CARD_MODEL = os.environ.get("CRAFT_BOARD_MODEL", "")
CARD_CONNECTION = os.environ.get("CRAFT_BOARD_CONNECTION", "")
SCHEMA = 1


def fail(message: str) -> None:
    raise SystemExit(message)


def cli(*args: str) -> dict[str, Any]:
    if not CLI:
        fail("CRAFT_RPC_CLI is not configured")
    proc = subprocess.run([CLI, *args], text=True, capture_output=True, timeout=30)
    if proc.returncode:
        fail(f"craft cli failed: {' '.join(args[:3])}: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {}


def open_gates() -> list[dict[str, Any]]:
    return [g for p in sorted(GATES.glob("*/*.json"))
            if (g := common.read_json(p)) and g.get("state") == "open"]


def gate_record(project: str, gate_id: str) -> dict[str, Any] | None:
    return common.read_json(GATES / project / f"{gate_id}.json")


def board_key(gate: dict[str, Any]) -> str:
    return f"{gate.get('project')}::{gate.get('gateId')}"


def user_messages_after(session_id: str, after_ms: int) -> list[tuple[int, str]]:
    """Owner-typed messages from the durable session log, oldest first."""
    rows: list[tuple[int, str]] = []
    path = common.SESSIONS / session_id / "session.jsonl"
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            next(fh, None)
            for line in fh:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                ts = int(event.get("timestamp") or 0)
                if event.get("type") == "user" and ts > after_ms:
                    content = event.get("content")
                    if isinstance(content, str) and content.strip():
                        rows.append((ts, content.strip()))
    except FileNotFoundError:
        pass
    rows.sort()
    return rows


def match_choice(message: str, gate: dict[str, Any]) -> str | None:
    """Exact choice match; case-insensitive fallback only when unambiguous.
    Project HOLD gates accept only the exact `RESUME`."""
    text = message.strip()
    choices = [c for c in (gate.get("choices") or []) if isinstance(c, str)]
    if str(gate.get("gateId") or "").startswith("project-hold"):
        return "RESUME" if text == "RESUME" and "RESUME" in choices else None
    if text in choices:
        return text
    lowered = [c for c in choices if c.lower() == text.lower()]
    return lowered[0] if len(lowered) == 1 else None


def card_notes(gate: dict[str, Any]) -> str:
    choices = "\n".join(f"- `{c}`" for c in gate.get("choices") or [])
    return (f"# 🚦 Owner gate: {gate.get('project')} · {gate.get('gateId')}\n\n"
            f"**Вопрос:** {gate.get('question')}\n\n"
            f"**Чтобы решить — отправьте в эту сессию ровно один из вариантов:**\n{choices}\n\n"
            f"Категория: `{gate.get('ownerOnlyCategory')}` · scope: `{gate.get('blockingScope')}`\n"
            f"Мост читает только ваши сообщения; решение уходит в `owner-gate.py resolve` "
            f"с authority `direct-owner` и ссылкой на это сообщение как evidence.\n")


def close_card(session_id: str, title: str) -> None:
    cli("invoke", "sessions:command", json.dumps(session_id), json.dumps({"type": "rename", "name": title}))
    cli("invoke", "sessions:command", json.dumps(session_id), json.dumps({"type": "setSessionStatus", "state": "done"}))
    cli("invoke", "sessions:command", json.dumps(session_id), json.dumps({"type": "archive"}))


def cmd_sync(args: argparse.Namespace) -> int:
    now = common.now_ms()
    actions: list[dict[str, Any]] = []
    with common.file_lock(LOCK):
        board = common.read_json(BOARD) or {"schemaVersion": SCHEMA, "cards": {}}
        cards: dict[str, Any] = board.get("cards") or {}
        gates = {board_key(g): g for g in open_gates()}

        # Cards whose gate closed (resolved anywhere) are completed and archived.
        for key in list(cards):
            card = cards[key]
            project, gate_id = key.split("::", 1)
            gate = gate_record(project, gate_id)
            if not gate or gate.get("state") != "open":
                choice = (gate or {}).get("choice") or "resolved"
                actions.append({"action": "complete-card", "gate": key, "choice": choice})
                if args.apply:
                    close_card(card["sessionId"], f"✅ {project} · {gate_id} → {choice}")
                cards.pop(key)

        # Open gates without a card get one.
        for key, gate in gates.items():
            if key in cards:
                continue
            actions.append({"action": "create-card", "gate": key})
            if args.apply:
                if not WORKSPACE_ID:
                    fail("CRAFT_WORKSPACE_ID is required to create gate cards")
                options: dict[str, Any] = {
                    "name": f"🚦 {gate.get('project')} · {gate.get('gateId')}",
                    "labels": ["owner-gate", f"project::{gate.get('project')}"],
                    "isFlagged": True, "sessionStatus": "todo", "workingDirectory": "none",
                }
                # Never inherit an expensive workspace default for an inert card.
                if CARD_MODEL:
                    options["model"] = CARD_MODEL
                if CARD_CONNECTION:
                    options["llmConnection"] = CARD_CONNECTION
                created = cli("invoke", "sessions:create", json.dumps(WORKSPACE_ID), json.dumps(options))
                session_id = str(created.get("id") or "")
                if not session_id:
                    fail(f"card creation returned no session id for {key}")
                cli("invoke", "sessions:setNotes", json.dumps(session_id), json.dumps(card_notes(gate)))
                cards[key] = {"sessionId": session_id, "createdAt": now, "lastSeenTs": now}

        # Owner-typed choices resolve their gates.
        for key, gate in gates.items():
            card = cards.get(key)
            if not card:
                continue
            messages = user_messages_after(str(card["sessionId"]), int(card.get("lastSeenTs") or 0))
            if not messages:
                continue
            ts, text = messages[-1]
            card["lastSeenTs"] = ts
            if args.apply:
                # The owner's choice is data, not a prompt: stop any turn it started.
                cli("invoke", "sessions:cancel", json.dumps(card["sessionId"]))
            choice = match_choice(text, gate)
            if choice is None:
                actions.append({"action": "unrecognized-choice", "gate": key, "message": text[:80]})
                continue
            actions.append({"action": "resolve", "gate": key, "choice": choice})
            if args.apply:
                proc = subprocess.run(
                    [sys.executable, str(OWNER_GATE), "resolve", "--project", gate["project"],
                     "--gate", gate["gateId"], "--choice", choice, "--authority", "direct-owner",
                     "--evidence", f"owner typed choice in Craft session {card['sessionId']} at {ts}"],
                    text=True, capture_output=True, timeout=30)
                if proc.returncode:
                    actions.append({"action": "resolve-failed", "gate": key,
                                    "error": (proc.stderr or proc.stdout).strip()[:200]})
                    continue
                close_card(str(card["sessionId"]),
                           f"✅ {gate.get('project')} · {gate.get('gateId')} → {choice}")
                cards.pop(key, None)

        board["cards"] = cards
        if args.apply:
            common.atomic_json(BOARD, board)
    print(json.dumps({"applied": args.apply, "actions": actions,
                      "openGates": len(gates), "cards": len(cards)}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    board = common.read_json(BOARD) or {"cards": {}}
    print(json.dumps({"cards": board.get("cards") or {},
                      "openGates": [board_key(g) for g in open_gates()]}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync"); s.add_argument("--apply", action="store_true"); s.set_defaults(func=cmd_sync)
    r = sub.add_parser("report"); r.set_defaults(func=cmd_report)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))
