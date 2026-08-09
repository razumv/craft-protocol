# Multi-Agent Orchestration Protocol v3.1

**Назначение:** надёжная coordinator → worker → auditor система для Craft Agents с сохранением незавершённой работы, heartbeat/lease monitoring, безопасной ротацией сессий и deterministic cleanup процессов.

**Версия:** 3.1, 2026-08-08
**Платформа реализации:** macOS + Craft Agents; Codex как основной provider, Claude как fallback.
**Главный принцип:** **preserve before terminate**.

> Patch extension `v3.1.1` adds opt-in deterministic recovery incidents and a bounded agentic controller without changing v3.1 runtime schemas or authority. See [SELF-HEALING-v3.1.1.md](SELF-HEALING-v3.1.1.md).

---

## 1. Какие проблемы решает протокол

1. Worker может замолчать посреди задачи, не отправив terminal handoff.
2. Долгий build/test может закончиться, а coordinator продолжает считать PID активным.
3. Archiving Craft session не гарантирует остановку model harness процесса.
4. Несколько sessions в одном worktree делают `cwd → PID` неоднозначным.
5. Rework/audit loops создают десятки старых процессов и расходуют RAM.
6. Долгоживущий coordinator раздувает context и получает request-buffer/context errors.
7. Простое убийство процесса может потерять dirty/unpushed работу или случайно задеть Craft Agents app.
8. Параллельные UE/Blender/full-test задачи перегружают машину.

---

## 2. Архитектура

Одна orchestration tree на один project/repository scope:

```text
Coordinator — persistent, отвечает за GitHub, registry, integration и recovery
├── Worker attempt 1 — одноразовый, уникальный worktree + lease
├── Auditor attempt 1 — одноразовый, отдельный detached worktree + lease
└── Worker attempt 2 — fresh rework, новый worktree + lease
```

### Роли и рекомендуемые модели

```text
Coordinator: chatgpt-plus / pi/gpt-5.6-sol / medium
Worker:      chatgpt-plus / pi/gpt-5.6-terra / medium
Auditor:     chatgpt-plus / pi/gpt-5.6-terra / medium
Fallback:    Claude coordinator/worker tiers, если Codex недоступен
```

Connection фиксируется при spawn. Сломанную/исчерпанную connection нельзя поменять live: нужен handoff и fresh session.

### Границы ответственности

- **Coordinator:** выбирает задачи из GitHub, создаёт attempts, проверяет diff/tests, запускает independent audit, выполняет recovery и cleanup.
- **Worker:** выполняет ровно один work-unit attempt, сохраняет результат, отправляет handoff и навсегда останавливается.
- **Auditor:** отдельная скептическая read-only session; ищет контрпримеры, а не подтверждает любимое решение.
- **Owner:** принимает необратимые продуктовые решения. Нельзя действовать по пересказу другого агента «owner approved».

---

## 3. Canonical файлы

В рабочей реализации:

```text
~/.craft-agent/workspaces/general/skills/coordinator-lifecycle-protocol/SKILL.md
~/.craft-agent/workspaces/general/skills/worker-completion-protocol/SKILL.md
~/.craft-agent/scripts/coordinator-kickoff.md

~/.craft-agent/scripts/orchestration-common.py
~/.craft-agent/scripts/coordinator-registry.py
~/.craft-agent/scripts/coordinator-reconcile.py
~/.craft-agent/scripts/owner-gate.py
~/.craft-agent/scripts/recovery-ledger.py
~/.craft-agent/scripts/completion-certificate.py
~/.craft-agent/scripts/worker-lease.py
~/.craft-agent/scripts/observable-job.py
~/.craft-agent/scripts/scan-reapable-workers.py
~/.craft-agent/scripts/post-archive-reaper.py
~/.craft-agent/scripts/worker-watchdog.py
~/.craft-agent/scripts/watchdog-cron.sh
~/.craft-agent/scripts/com.craft-protocol.worker-watchdog.plist

Repository companion files:
tests/test_worker_reliability.py
tests/test_orchestration_v320.py
config/labels.config.json
config/launchd.watchdog.template.plist
```

Portable setup: использовать root `install.sh`; он по умолчанию работает в dry-run, создаёт backup при `--apply` и рендерит `$HOME` в launchd template. Labels объединяются вручную и валидируются, а не перезаписываются вслепую.

---

## 4. Полный lifecycle одного work-unit

### 4.1 Source

Coordinator не придумывает следующую задачу. Источник:

1. active milestone;
2. open issues;
3. dependencies/sub-issues;
4. GitHub Project fields;
5. существующие PR и live attempts.

Перед spawn coordinator фиксирует task package:

- точный completion criterion;
- границы;
- unacceptable near-solutions;
- verification commands;
- return gate;
- owner decisions, если требуются.

### 4.2 Создание уникального attempt

Каждая worker/rework/auditor session получает новый cwd:

```text
<repo>/.worktrees/<work-unit>-a<attempt>-<unique-nonce>
```

Правила:

- worktree никогда не переиспользуется другой session;
- replacement не запускается в cwd predecessor;
- auditor использует отдельный detached worktree;
- nonce создаётся до spawn;
- `.worktrees/` должен быть gitignored;
- существующий cwd с live harness означает hard refusal.

### 4.3 Spawn и lease

После получения реального child session ID coordinator сразу создаёт lease:

```bash
~/.craft-agent/scripts/worker-lease.py create \
  --session <CHILD_SESSION_ID> \
  --parent <COORDINATOR_SESSION_ID> \
  --work-unit <WORK_UNIT> \
  --attempt <N> \
  --worktree <ABSOLUTE_UNIQUE_WORKTREE> \
  --phase task-assigned
```

Рекомендуемые labels:

```text
agent-role::worker | agent-role::auditor
parent-session::<coordinator-id>
work-unit::<id>
attempt::<N>
github-issue::<URL>
protocol-version::1.0.0
```

`attempt` — numeric valued label.

Если coordinator упал между spawn и lease creation, deterministic reconciliation найдёт live worker manifest и создаст missing lease.

### 4.4 Worker startup

Worker сначала делает:

```bash
get_session_info
~/.craft-agent/scripts/worker-lease.py heartbeat \
  --session <SELF_ID> \
  --state running \
  --phase task-started \
  --evidence "task package acknowledged"
```

Worker не должен менять cwd или принимать новую задачу после handoff.

### 4.5 Progress heartbeat

Heartbeat отправляется после meaningful phase и примерно каждые 10–15 минут активной работы:

```bash
~/.craft-agent/scripts/worker-lease.py heartbeat \
  --session <SELF_ID> \
  --phase <PHASE> \
  --evidence "<SHA, test result, artifact, log progress>"
```

Фраза «still working» не является evidence.

Допустимое evidence:

- новый commit SHA;
- test result;
- изменившийся artifact;
- log mtime/size;
- активный child PID;
- completed phase;
- сохранённый checkpoint.

### 4.6 Long-running jobs

Команды дольше 10 минут запускаются через observable wrapper:

```bash
~/.craft-agent/scripts/observable-job.py start \
  --session <SELF_ID> \
  --cwd <WORKTREE> \
  --log <ABSOLUTE_LOG_PATH> \
  -- <COMMAND> <ARGS...>
```

Проверка:

```bash
~/.craft-agent/scripts/observable-job.py status --session <SELF_ID>
```

Receipt содержит:

```text
sessionId, supervisorPid, childPid, cwd, logPath, command,
startedAt, updatedAt, exitCode, finishedAt, heavy
```

Если PID исчез, а успешного receipt нет — job считается failed, а не «наверное ещё работает».

### 4.7 Heavyweight global lock

Для UE/Blender/full builds и тяжёлых suites:

```bash
~/.craft-agent/scripts/observable-job.py start \
  --session <SELF_ID> --cwd <WORKTREE> --log <LOG> \
  --heavy -- <COMMAND> <ARGS...>
```

`--heavy` удерживает global `fcntl` lock:

```text
~/.craft-agent/runtime/heavy-job.lock
~/.craft-agent/runtime/heavy-job-owner.json
```

Второй heavy job получает exit code `75` (`global heavyweight lane busy`) вместо одновременного запуска.

### 4.8 Terminal worker handoff

Iron rule:

```text
NO TERMINAL HANDOFF UNTIL WORK IS PRESERVED IN GIT.
```

Проверки:

```bash
git status --porcelain
git branch --show-current
git push -u origin HEAD
```

Структура сообщения coordinator:

```text
STATUS: done | needs-rework | blocked
WORK-UNIT: <id>
ATTEMPT: <N>
BRANCH: <branch>   PR: <url or none>
DONE:
- <verified facts>
NOT DONE / OPEN:
- <remaining work and reason>
FILES: <key files>
VERIFY: <commands and exact results>
PRESERVATION: clean + pushed at <SHA/ref>
LEASE/JOB: <last phase and job exit>
```

После report:

```bash
~/.craft-agent/scripts/worker-lease.py finish \
  --session <SELF_ID> --preservation pushed
```

Затем worker устанавливает session status `needs-review` и навсегда останавливается.

### 4.9 Verification и audit

Coordinator самостоятельно:

1. читает diff;
2. запускает verification;
3. проверяет exact acceptance criterion;
4. создаёт fresh auditor в новом detached worktree;
5. получает PASS/FAIL с immutable SHA.

Audit FAIL → fresh worker/rework.
Audit PASS → item может перейти в `In review`; не закрывать/не ставить Done без owner authority.

После двух последовательных audit FAIL на одном work-unit automatic rework loop останавливается. Coordinator выполняет root-cause/spec review перед attempt 3.

### 4.10 Archive и reap

Порядок обязателен:

1. clean + pushed/merged proof;
2. pre-archive report;
3. `archive_session`;
4. guarded post-archive process cleanup;
5. lease reconciliation.

```bash
python3 ~/.craft-agent/scripts/scan-reapable-workers.py \
  --parent <COORDINATOR_ID>

# archive_session(<CHILD_ID>) — Craft session tool, FIRST

python3 ~/.craft-agent/scripts/post-archive-reaper.py \
  --session <CHILD_ID> --apply

~/.craft-agent/scripts/worker-lease.py reconcile --apply
```

После archive/absence автоматически удаляются:

```text
worker-leases/<session-id>.json
worker-jobs/<session-id>.json
pids/<session-id>.pid
```

Terminal evidence остаётся в coordinator registry/session/GitHub, но активный heartbeat удаляется.

---

## 5. Lease/watchdog state machine

Runtime leases:

```text
~/.craft-agent/runtime/worker-leases/<session-id>.json
```

States:

```text
starting       — session создана, task назначен
running        — evidence свежее или жив observable child
suspect        — 15–30 минут без evidence
stalled        — >30 минут без evidence/child/log progress
error          — terminal model/transport/job error
handoff-ready  — terminal report + preservation/status
```

Полезные команды:

```bash
worker-lease.py report
worker-lease.py reconcile
worker-lease.py reconcile --apply
worker-lease.py remove --session <ID>
```

Reconciliation:

- создаёт missing lease для live worker/auditor;
- синхронизирует parent/work-unit/attempt после takeover;
- классифицирует stalls/errors;
- удаляет leases архивных/отсутствующих sessions;
- удаляет orphan PID/job receipts;
- показывает live cwd collisions.

Coordinator sessions не получают worker leases. Они могут иметь standalone observable job receipt, но новые тяжёлые продуктовые jobs должны выполняться leased workers.

---

## 6. Deterministic watchdog

Watchdog не делает LLM calls, не создаёт sessions и не использует prompt automations.

Каждые 5 минут:

1. post-archive reaper обрабатывает bounded batch;
2. lease reconciliation обновляет runtime state;
3. логирует результаты.

Файлы:

```text
~/.craft-agent/scripts/worker-watchdog.py
~/.craft-agent/scripts/watchdog-cron.sh
~/Library/LaunchAgents/com.<USER>.craft-worker-watchdog.plist
~/.craft-agent/logs/worker-watchdog.log
```

Установка:

```bash
chmod +x ~/.craft-agent/scripts/{worker-lease.py,observable-job.py,post-archive-reaper.py,worker-watchdog.py,watchdog-cron.sh}
mkdir -p ~/.craft-agent/runtime/{worker-leases,worker-jobs} ~/.craft-agent/logs ~/.craft-agent/pids
cp ~/.craft-agent/scripts/com.<USER>.craft-worker-watchdog.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.<USER>.craft-worker-watchdog.plist
launchctl enable gui/$(id -u)/com.<USER>.craft-worker-watchdog
launchctl kickstart -k gui/$(id -u)/com.<USER>.craft-worker-watchdog
```

Проверка:

```bash
launchctl print gui/$(id -u)/com.<USER>.craft-worker-watchdog
tail -f ~/.craft-agent/logs/worker-watchdog.log
```

Отключение:

```bash
launchctl bootout gui/$(id -u)/com.<USER>.craft-worker-watchdog
```

Prompt/SchedulerTick janitor использовать нельзя: каждый запуск создаёт новую leaking model session.

---

## 7. Reaper safety gates

Post-archive cleanup разрешён только если:

- session archived;
- role worker/auditor;
- cwd не используется live session;
- worker worktree clean;
- worker HEAD доказан на origin branch/default;
- либо clean read-only auditor lane;
- либо worktree уже удалён после archive;
- PID command содержит `pi-agent-server` или `claude-agent-sdk-binary/claude`;
- PID не является `.../MacOS/Craft Agents`.

Hard refusal:

- dirty/uncommitted worktree;
- unpushed worker commit;
- live shared cwd;
- неизвестный/non-harness PID;
- app PID;
- неоднозначная ownership.

`cwd → PID` всегда хранится как список. Несколько PID в одном cwd — collision, а не повод выбрать случайный процесс.

---

## 8. Recovery policy

### Silent worker

1. 15 минут без evidence → `suspect`.
2. 30 минут без child/log progress → `stalled`.
3. Проверить git, process, receipt, log.
4. Сохранить commit/push или patch/backup branch.
5. Archive/reap predecessor.
6. Создать fresh attempt в новом worktree.

### Stuck mid-turn

Если `archive_session` отвечает `currently processing a turn`:

1. не угадывать PID;
2. сохранить dirty diff как patch/branch;
3. проверить unique cwd;
4. если cwd shared — сопоставить session/process по точному creation/start time или эскалировать owner-facing infrastructure session;
5. проверить `pi-agent-server/claude` guard и исключить app PID;
6. остановить только доказанный harness;
7. архивировать session;
8. fresh replacement применяет patch в новом worktree;
9. старый cwd очищается только после нового commit+push proof.

### Connection Error

Один retry. Повтор → preservation/checkpoint + fresh worker.

### Request buffer/context error

Written handoff + fresh coordinator. Не продолжать раздувать старую session.

### Policy false positive

Один neutral retry с формулировкой ordinary application development. Повторный отказ → fresh session с handoff.

### Command timeout

Не считать worker зависшим автоматически. Сначала проверить child PID, receipt, log mtime и exit code.

### Coordinator takeover

1. Старый coordinator пишет handoff.
2. Spawn replacement в том же project/repo scope.
3. Проверить model/connection/project/scope.
4. Reparent live workers: заменить `parent-session` label.
5. `worker-lease.py reconcile --apply` синхронизирует lease ownership.
6. Новый coordinator подтверждает takeover.
7. Старый coordinator прекращает работу и архивируется.

---

## 9. Coordinator rotation

Coordinator не ротируется слепо посреди wave.

Rotation trigger:

- первый request-buffer/context error;
- примерно 200–220k cumulative input tokens;
- примерно 500 сообщений;
- потеря registry/повторный запуск закрытых lanes;
- сломанная connection.

Rotation выполняется на safe barrier:

```text
freeze new spawns
→ reconcile children
→ preserve live work
→ write handoff
→ spawn replacement
→ transfer live worker ownership
→ verify takeover
→ archive old coordinator
```

Workers/auditors не ротируются внутри одного attempt: они одноразовые по определению. Rework/audit всегда fresh session + fresh worktree.

---

## 10. Registry и concurrency

Coordinator registry row:

```text
session, role, work-unit, attempt, issue, worktree, branch/PR,
dependencies, lease state, last evidence, preservation, verdict, next action
```

Default limits:

```text
1 worker + 1 auditor per work-unit
2 workers + 1 auditor per project
1 global heavyweight job per machine
```

При каждом child/owner сообщении coordinator:

1. запускает lease reconcile;
2. проверяет все children;
3. архивирует terminal attempts;
4. только затем создаёт дополнительные sessions.

---

## 11. Labels

Минимальная workspace label config:

```json
{
  "id": "agent-role",
  "name": "Agent Role",
  "color": "info",
  "valueType": "string"
}
```

Аналогично создать:

```text
parent-session  string
work-unit       string
attempt         number
protocol-version string
github-issue    link
```

После изменения labels config выполнить workspace config validation.

---

## 12. Validation

В companion test suite проверяются:

1. missing lease создаётся для live worker;
2. archive удаляет lease/job/PID runtime state;
3. terminal/error classification;
4. heartbeat отсутствующей session self-removes;
5. observable job создаёт exit receipt;
6. global heavy jobs сериализуются;
7. non-harness/app process никогда не принимается reaper’ом;
8. clean archived auditor/absent worktree cleanup gates;
9. coordinator heartbeat не создаёт worker lease и не удаляет standalone job receipt.

Запуск:

```bash
python3 ~/.craft-agent/scripts/tests/test_worker_reliability.py -v
python3 -m py_compile ~/.craft-agent/scripts/*.py
```

Skills после изменения валидируются штатным Craft `skill_validate`.

---

## 13. Operational checklist

### Перед spawn

- [ ] Task sourced from GitHub.
- [ ] Attempt number allocated.
- [ ] Fresh unique nonce/worktree.
- [ ] No live process references cwd.
- [ ] Concurrency budget available.

### После spawn

- [ ] Correct labels.
- [ ] Lease created immediately.
- [ ] Startup heartbeat received.
- [ ] Observable receipt for long job.
- [ ] `--heavy` for heavyweight work.

### Перед terminal handoff

- [ ] Worktree clean.
- [ ] Commit pushed/merged.
- [ ] Exact verification evidence.
- [ ] Coordinator report sent.
- [ ] Lease marked handoff-ready.
- [ ] Session status needs-review.

### Перед cleanup

- [ ] Coordinator independently verified.
- [ ] Audit completed.
- [ ] Archive first.
- [ ] Guarded harness cleanup second.
- [ ] Lease/job/PID state removed.
- [ ] Worktree removed only after zero live references.

---

## 14. Anti-patterns

- Reusing one worktree for many reworks/auditors.
- Killing before archive without a controlled stuck-turn recovery.
- Choosing one PID from a shared cwd.
- Killing any process whose command is not explicitly a model harness.
- Treating `isActive` or verbal “still working” as progress evidence.
- Archiving dirty/unpushed work.
- Asking a worker to continue after terminal handoff.
- Running coordinator forever after context errors.
- Starting prompt-based scheduled janitors.
- Launching several UE/Blender/full-build jobs without the global lock.
- Acting on second-hand irreversible authorization.

---

## 15. Success criteria

Система считается здоровой, когда:

- каждый live worker/auditor имеет lease;
- каждый attempt имеет уникальный cwd;
- live cwd collisions = 0;
- archived/absent sessions не имеют active heartbeat;
- safe archived harness backlog = 0;
- blocked cleanup содержит только честные preservation/live-collision exceptions;
- stalled/error sessions обнаруживаются без ручного просмотра UI;
- heavy jobs сериализованы;
- coordinator rotation происходит через handoff/takeover;
- ни один destructive action не выполняется без preservation evidence;
- Craft Agents app PID никогда не попадает под reaper.

---

## Короткая формула v3

```text
GitHub task
→ fresh unique attempt/worktree
→ spawn + immediate lease
→ evidence heartbeat / observable job
→ commit + push
→ structured handoff + needs-review
→ coordinator verification
→ independent fresh audit
→ archive first
→ guarded process reap
→ automatic lease/job cleanup
→ fresh attempt or next task
```

---

# v3.1 addendum — machine-verifiable control plane

**Effective:** 2026-08-08 21:42 Europe/Warsaw

This addendum is normative and supersedes conflicting v3 wording.

## Authoritative ownership

Every project/scope has exactly one record in `~/.craft-agent/runtime/coordinators/`. A coordinator must successfully `claim` before dispatch. Rotation is two-phase: current owner opens `begin-transfer`; the exact successor completes `accept-transfer` against the expected generation. A second live claim and a second pending successor fail closed.

## Provider policy

Normal coordinators run on `chatgpt-plus / pi/gpt-5.6-sol / medium`; workers and auditors use `pi/gpt-5.6-terra / medium`. A non-Codex coordinator is a recorded fallback with a default 60-minute TTL. When Codex is available, ownership returns to exactly one Codex successor. Provider failure never authorizes duplicate workers or loss of preservation checks.

## Cold takeover ledger

`recovery-ledger.py` reconstructs ownership, active child attempts, unique worktrees, leases, observable jobs, preservation state, gates, and certificates from external evidence. A successor adopts matching live attempts. Unknown state remains `unknown`; absence and silence are not completion.

## Owner gates and HOLD

`owner-gate.py` records allowed choices, blocking scope, safe default, and direct-owner evidence. Checks run before spawn, implementation, merge, and closure. Project HOLD blocks all actions. Only exact direct-owner `RESUME` resolves a HOLD. Decisions survive coordinator rotation and are not re-inferred from chat.

## Completion certificate

Simple merge/closure authority requires a valid machine-readable certificate containing an unchanged candidate/audited SHA, independent PASS, distinct immutable required-CI IDs, merge SHA, distinct merged-main readback IDs, and zero unresolved gates. Reused runs, mutable head evidence, relayed claims, and missing readback fail closed.

## Metadata and rotation

New coordinator name: `Coordinator <Project> (Codex/Sol) — v3`. Required labels: `coordinators`, `agent-role::coordinator`, `project::<slug>`, `protocol-version::3`, plus predecessor/replacement metadata. Metadata drift is detected read-only; live session JSONL is never rewritten. Rotation is recommended at provider/context failure, about 200k tokens, 500 messages, three active lanes, eight gates, or equivalent complexity, with cooldown against loops.

## Deterministic watchdog

The launchd watchdog remains non-LLM. It reports ownership/lease drift, expired fallback, metadata mismatch, HOLD/gate state, duplicate lanes/cwd, and worker progression. It may perform only existing preservation-safe reconciliation. Dirty/unpushed work, shared cwd, non-harness PID, and the Craft Agents app process remain hard cleanup refusals.

## Canonical commands

```bash
~/.craft-agent/scripts/coordinator-registry.py validate
~/.craft-agent/scripts/coordinator-reconcile.py
~/.craft-agent/scripts/recovery-ledger.py reconstruct --project <slug>
~/.craft-agent/scripts/owner-gate.py check --project <slug> --work-unit <unit> --action <spawn|implement|merge|close>
~/.craft-agent/scripts/completion-certificate.py validate --file <certificate.json>
~/.craft-agent/scripts/worker-watchdog.py --apply
```
