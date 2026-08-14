#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Board projection of machine truth: product-increment cards with story subtasks.

A deterministic operator-side mirror — no LLM participates and nothing here is
authoritative. It reads what coordinators published (`coordinator-status.py`) and
the runtime's own lease truth, then renders it where the owner already looks:

- one 🚀 card per project whose title is a live ticker (stage, accepted/total,
  risk) and whose notes are the customer-first status; the card closes when the
  increment completes;
- one subtask per Product Increment story, parented to that card, so the owner
  sees *what* the counter counts instead of a bare `0/5`. Subtask board status
  follows story state, and a story that leaves the increment is archived;
- truthful lane statuses: an active lane reads `todo`, terminality stays the
  protocol's `needs-review`, so the columns stop lying;
- authoritative coordinators are kept on the board, because the runtime makes
  every session they spawn a subtask of their card — a coordinator with no board
  status hides all of its live work.

State is durable: cards and story subtasks are tracked in `increment-board.json`
so a restart never orphans or duplicates a card the owner is already using.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
from typing import Any

CRAFT = Path(os.environ.get("CRAFT_HOME") or Path.home() / ".craft-agent")
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME") or CRAFT / "runtime")
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS") or CRAFT / "workspaces/general/sessions")
SCRIPTS = Path(os.environ.get("CRAFT_SCRIPTS") or CRAFT / "scripts")
STATE = RUNTIME / "increment-board.json"
CLI = os.environ.get("CRAFT_RPC_CLI", "")
WS = os.environ.get("CRAFT_WORKSPACE_ID", "")
MODEL = os.environ.get("CRAFT_BOARD_MODEL", "pi/gpt-5.4-mini")
CONNECTION = os.environ.get("CRAFT_BOARD_CONNECTION", "chatgpt-plus")
PY = sys.executable
MAX_SUBTASKS = int(os.environ.get("CRAFT_BOARD_MAX_SUBTASKS", "8"))

# A story's board status is derived, never guessed: the owner must be able to
# read the column and know whether anyone is on it.
STORY_STATUS = {
    "accepted": "done", "integrated": "done",
    "executing": "in_progress", "in-progress": "in_progress",
    "failed": "needs-review", "blocked": "needs-review",
}
STORY_ICON = {
    "accepted": "✅", "integrated": "✅", "executing": "⚙️",
    "in-progress": "⚙️", "failed": "⛔", "blocked": "⛔",
}


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}), file=sys.stderr)
    raise SystemExit(2)


def cli(*args: str) -> dict[str, Any]:
    if not CLI:
        fail("CRAFT_RPC_CLI is not configured")
    proc = subprocess.run([CLI, *args], text=True, capture_output=True, timeout=30)
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {}


def read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def manifest(sid: str) -> dict[str, Any]:
    try:
        with (SESSIONS / sid / "session.jsonl").open(encoding="utf-8", errors="ignore") as handle:
            return json.loads(handle.readline())
    except Exception:
        return {}


def status_report() -> dict[str, Any]:
    out = subprocess.run([PY, str(SCRIPTS / "coordinator-status.py"), "report", "--all", "--format", "json"],
                         text=True, capture_output=True, timeout=60).stdout
    try:
        return json.loads(out)
    except Exception:
        return {"projects": []}


def stories_of(report: dict[str, Any]) -> list[dict[str, Any]]:
    increment = ((report.get("declared") or {}).get("productIncrement") or {})
    stories = increment.get("stories") or []
    return [s for s in stories if isinstance(s, dict)][:MAX_SUBTASKS]


def ticker(project: str, report: dict[str, Any]) -> tuple[str, str, bool]:
    declared = report.get("declared") or {}
    inc = declared.get("productIncrement") or {}
    stories = inc.get("stories") or []
    accepted = sum(1 for s in stories if isinstance(s, dict) and s.get("state") in {"accepted", "integrated"})
    stage = inc.get("stage") or declared.get("phase") or report.get("classification") or "unknown"
    risk = inc.get("riskTier") or "-"
    counts = f"{accepted}/{len(stories)}" if stories else "-"
    complete = stage == "complete"
    icon = "✅" if complete else "🚀"
    name = f"{icon} {project} · {stage} · {counts} · risk {risk}"
    lines = [f"# {project} — product increment", ""]
    for label, key in (("What the customer will see", "objective"), ("Demonstrable now", "demonstrableNow"),
                       ("What remains", "remainingOutcome"), ("ETA / confidence", "etaRange"),
                       ("One real blocker", "realBlocker")):
        value = declared.get(key)
        if key == "etaRange" and value:
            value = f"{value} / {declared.get('confidence') or '-'}"
        lines.append(f"- **{label}:** {value or '_not published_'}")
    if inc:
        lines += ["", f"**Increment `{inc.get('id')}`** — stage `{stage}`, risk `{risk}`",
                  f"- demonstration: {inc.get('demonstrationCriterion')}", "",
                  "| story | state | risk |", "|---|---|---|"]
        lines += [f"| {s.get('title')} | `{s.get('state')}` | {s.get('riskContribution')} |"
                  for s in stories if isinstance(s, dict)]
    sync = declared.get("githubSync") or {}
    if sync:
        lines += ["", f"_GitHub `{sync.get('issue')}` synced at stage `{sync.get('syncedStage')}`_"]
    synth = report.get("synthesized") or {}
    lines += ["", f"_freshness `{report.get('classification')}` · active lanes {synth.get('activeWorkerCount', 0)}"
              f" · open gates {synth.get('openGateCount', 0)}_"]
    return name, "\n".join(lines), complete


def story_name(story: dict[str, Any]) -> str:
    state = str(story.get("state") or "planned")
    icon = STORY_ICON.get(state, "•")
    title = str(story.get("title") or story.get("id") or "story")
    return f"{icon} {title}"


def finish_card(session_id: str, name: str, labels: list[str], status: str) -> None:
    """Creation options echo back but do not persist: the observed runtime keeps
    only name and flag. Everything the owner reads on the board — title, labels,
    column, model — is therefore set explicitly, and the rename also emits the
    update event a UI applies when it learned about the card some other way."""
    cli("invoke", "sessions:command", json.dumps(session_id), json.dumps({"type": "rename", "name": name}))
    cli("invoke", "sessions:command", json.dumps(session_id), json.dumps({"type": "setLabels", "labels": labels}))
    cli("invoke", "sessions:command", json.dumps(session_id),
        json.dumps({"type": "setSessionStatus", "state": status}))
    if MODEL:
        cli("invoke", "session:setModel", json.dumps(session_id), json.dumps(WS),
            json.dumps(MODEL), json.dumps(CONNECTION or ""))


def create_session(name: str, labels: list[str], status: str, parent: str | None) -> str:
    if not WS:
        fail("CRAFT_WORKSPACE_ID is required to create board cards")
    options: dict[str, Any] = {
        "name": name, "labels": labels, "sessionStatus": status,
        "workingDirectory": "none", "model": MODEL, "llmConnection": CONNECTION,
    }
    if parent:
        options["parentSessionId"] = parent
    created = cli("invoke", "sessions:create", json.dumps(WS), json.dumps(options))
    return str(created.get("id") or "")


def sync_story_subtasks(project: str, card: dict[str, Any], report: dict[str, Any]) -> list[str]:
    """One subtask per story, so `2/8` on the card has eight readable rows under it."""
    actions: list[str] = []
    parent = str(card.get("sessionId") or "")
    tracked: dict[str, Any] = card.setdefault("stories", {})
    stories = stories_of(report)
    increment_id = str(((report.get("declared") or {}).get("productIncrement") or {}).get("id") or "")
    # A new increment replaces the whole story set; keeping the old rows under it
    # would show the owner work that no longer exists.
    if card.get("incrementId") and card["incrementId"] != increment_id:
        for story_id, sid in list(tracked.items()):
            cli("invoke", "sessions:command", json.dumps(sid), json.dumps({"type": "archive"}))
            tracked.pop(story_id, None)
        actions.append(f"increment-rolled:{project}")
    card["incrementId"] = increment_id
    seen: set[str] = set()
    for story in stories:
        story_id = str(story.get("id") or "")
        if not story_id:
            continue
        seen.add(story_id)
        state = str(story.get("state") or "planned")
        status = STORY_STATUS.get(state, "todo")
        name = story_name(story)
        labels = ["product-story", f"project::{project}", f"story-state::{state}"]
        sid = str(tracked.get(story_id) or "")
        if sid and manifest(sid).get("isArchived"):
            sid = ""
        if not sid:
            sid = create_session(name, labels, status, parent)
            if not sid:
                continue
            tracked[story_id] = sid
            actions.append(f"story-create:{project}/{story_id}")
        finish_card(sid, name, labels, status)
    for story_id, sid in list(tracked.items()):
        if story_id in seen:
            continue
        cli("invoke", "sessions:command", json.dumps(sid), json.dumps({"type": "archive"}))
        tracked.pop(story_id, None)
        actions.append(f"story-archive:{project}/{story_id}")
    return actions


def sync_increment_cards(state: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    cards: dict[str, Any] = state.setdefault("incrementCards", {})
    for report in status_report().get("projects") or []:
        project = report.get("project")
        if not project:
            continue
        name, notes, complete = ticker(project, report)
        status = "done" if complete else "todo"
        labels = ["product-increment", f"project::{project}"]
        card = cards.get(project)
        if not card:
            sid = create_session(name, labels, status, None)
            if not sid:
                continue
            cards[project] = {"sessionId": sid, "stories": {}}
            actions.append(f"create:{project}")
            card = cards[project]
        sid = str(card["sessionId"])
        if manifest(sid).get("isArchived"):
            # The owner archived the card deliberately; its stories go with it.
            for story_sid in (card.get("stories") or {}).values():
                cli("invoke", "sessions:command", json.dumps(story_sid), json.dumps({"type": "archive"}))
            cards.pop(project, None)
            actions.append(f"card-archived-by-owner:{project}")
            continue
        finish_card(sid, name, labels, status)
        cli("invoke", "sessions:setNotes", json.dumps(sid), json.dumps(notes))
        actions.append(f"update:{project}")
        actions += sync_story_subtasks(project, card, report)
    return actions


def sync_coordinator_statuses() -> list[str]:
    """The runtime makes every session a coordinator spawns a subtask of that
    coordinator's card, so a coordinator with no board status hides all of its
    live work along with itself."""
    actions: list[str] = []
    for path in sorted((RUNTIME / "coordinators").glob("*.json")):
        reg = read(path)
        if reg.get("state") not in {"authoritative", "rotating"}:
            continue
        sid = str(reg.get("coordinatorSessionId") or "")
        row = manifest(sid)
        if not row or row.get("isArchived"):
            continue
        if row.get("sessionStatus") in {None, "", "backlog"}:
            cli("invoke", "sessions:command", json.dumps(sid),
                json.dumps({"type": "setSessionStatus", "state": "todo"}))
            actions.append(f"coordinator-visible:{reg.get('project')}")
    return actions


def sync_lane_statuses() -> list[str]:
    """Active lanes read `todo`; the protocol owns `needs-review` terminality."""
    actions: list[str] = []
    for path in sorted((RUNTIME / "worker-leases").glob("*.json")):
        lease = read(path)
        sid = str(lease.get("sessionId") or path.stem)
        row = manifest(sid)
        if not row or row.get("isArchived"):
            continue
        if (lease.get("state") in {"starting", "running", "suspect", "stalled", "error"}
                and row.get("sessionStatus") not in {"todo", "backlog"}):
            cli("invoke", "sessions:command", json.dumps(sid),
                json.dumps({"type": "setSessionStatus", "state": "todo"}))
            actions.append(f"lane-todo:{sid}")
    return actions


def main() -> int:
    apply = "--apply" in sys.argv
    reset = "--reset-cards" in sys.argv
    state = read(STATE) or {}
    actions: list[str] = []
    if reset and apply:
        # Recreating cards is explicit: archive what exists, forget it, and let the
        # normal sync build the current shape. Never leave a stale card behind.
        for project, card in list((state.get("incrementCards") or {}).items()):
            for story_sid in (card.get("stories") or {}).values():
                cli("invoke", "sessions:command", json.dumps(story_sid), json.dumps({"type": "archive"}))
            cli("invoke", "sessions:command", json.dumps(card.get("sessionId")), json.dumps({"type": "archive"}))
            actions.append(f"reset:{project}")
        state["incrementCards"] = {}
    if apply:
        actions += sync_increment_cards(state) + sync_coordinator_statuses() + sync_lane_statuses()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"ok": True, "applied": apply, "actions": actions}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
