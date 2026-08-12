# Research disposition for Protocol v3.4.0

## Decision rule

This appendix compares the Geolance proposal and the DeepSeek shared catalog against observed Craft Protocol failures. It is a scope-control record, not an endorsement of every suggested remedy.

- `covered-v3.3`: the portable mechanism already exists; do not rebuild it.
- `adopt-v3.4`: delivery semantics or machine validation changes in v3.4.
- `operator/project-specific`: valid operational concern whose concrete implementation belongs to the affected stack.
- `reject/unsafe`: the proposed general rule is misleading, unbounded, or weakens explicit safety.

## Geolance disposition

| Mechanism | Decision | Minimal use in v3.4 |
|---|---|---|
| Master Design | Adopt narrowly | Customer-visible increment outcome, non-goals, demonstration criterion |
| Dependency-ordered stories | Adopt | Bounded acyclic story DAG in existing product status |
| Parallel DAG lanes | Adopt with limits | Up to two disjoint lightweight lanes; resource-aware heavy exceptions |
| Non-production eval/deploy loop | Adopt | One integrated candidate and real-workflow acceptance before release |
| Product traceability | Adopt | Outcome → stories → candidate → acceptance → deploy/readback |
| 18 roles / four planes | Reject | Existing coordinator/worker/auditor roles are sufficient |
| Vector memory / knowledge graph | Reject | No demonstrated need; existing durable runtime/GitHub evidence is authoritative |
| Judge for every task | Reject | Risk-based acceptance at the increment boundary |
| New orchestration platform | Reject | Reuse v3.3 inbox/status/lease/wait/recovery primitives |

## DeepSeek catalog

The source contains **129**, not 122, problem statements. Titles are reproduced for coverage; stack-specific prescriptions are not automatically imported.

| # | Problem | Disposition | Protocol decision |
|---:|---|---|---|
| 1 | Opencode worker зависает | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 2 | Задачи очереди не выполняются | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 3 | LLM недоступен | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 4 | OOM worker’ов | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 5 | Деплой убивает сессии | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 6 | Деплой застревает в Created | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 7 | Обрыв HTTP-соединения на долгой задаче | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 8 | Пустая сессия при старте | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 9 | maxExceptions=1 убивает задачу | `adopt-v3.4` | Classify infrastructure versus product failures before spending correction budget. |
| 10 | Зацикливание GLM | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 11 | Неверная атрибуция модели | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 12 | Остановка всех агентов из-за смерти Heartbeat | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 13 | Ложное обещание «Retrying» | `adopt-v3.4` | Retry promises require a durable executable transition and bounded terminal branch. |
| 14 | Двойное выполнение задачи | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 15 | Очередь без потребителя | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 16 | Истории зависают в pending | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 17 | История «running» дольше бюджета | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 18 | Осиротевшая running-сессия | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 19 | Исчерпание попыток с пустым выводом | `adopt-v3.4` | Empty terminal outcomes must become an exact blocker, never silent completion. |
| 20 | Перерасход токенов при неверной оценке | `reject/unsafe` | Reject automatic unbounded budget expansion; use explicit bounded resource budgets. |
| 21 | Токен-лимит не срабатывает на бесплатных моделях | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 22 | Блокировка доставки одной упавшей историей | `adopt-v3.4` | Increment delivery preserves and exposes partial successful artifacts. |
| 23 | Перемешивание файлов клиентов | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 24 | Потеря файлов при неудачном мерже | `adopt-v3.4` | Preserve every story artifact before integration or merge recovery. |
| 25 | Превью 404 при отсутствии контекста | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 26 | Статические сайты пропускают Git-публикацию | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 27 | Отсутствие коммитов из-за токена | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 28 | RSC-навигация на статике | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 29 | Ложный статус «0 agents» | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 30 | Удаление ролей при редактировании | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 31 | Зависший milestone | `adopt-v3.4` | Parent increment state advances from dependency-complete story evidence. |
| 32 | Нехватка стендапов | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 33 | Счётчик 0% для доставленных | `adopt-v3.4` | Customer status distinguishes demonstrable/delivered value from final acceptance. |
| 34 | Отсутствие параллелизма | `adopt-v3.4` | Allow bounded parallel disjoint DAG lanes. |
| 35 | Последовательный DAG | `adopt-v3.4` | Require the smallest dependency-valid DAG rather than artificial serialization. |
| 36 | Аппаратный потолок параллелизма | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 37 | Регрессия размера контента | `reject/unsafe` | Reject file-size regression as a universal quality proxy; validate the frozen product criterion. |
| 38 | Короткие страницы по умолчанию | `reject/unsafe` | Reject minimum section/count metrics as universal quality; use outcome-specific acceptance. |
| 39 | Непроверенная адаптивность | `adopt-v3.4` | UI completion requires evidence from the real responsive workflow. |
| 40 | Молчаливый переход на слабую модель | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 41 | Сборка идентичного тонкого сайта на qwen | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 42 | Слоты заняты, таймаут клиента не освобождает | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 43 | Зависание в planning без auto_start | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 44 | Конфликт изоляции при параллельных worktree | `adopt-v3.4` | Parallel lanes retain unique-worktree isolation. |
| 45 | Зомби-история невидима для вотчдога | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 46 | Отсутствие супервизора для очереди agents | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 47 | Отсутствие соединения redis-agents | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 48 | Фильтр историй пропускает ai_executes | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 49 | Зависание delegate_task | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 50 | Мёртвая очередь recruitment | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 51 | Двойная доставка задачи | `adopt-v3.4` | Idempotent attempt ownership and delivery remain mandatory at integration. |
| 52 | Заморозка задач на DEV | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 53 | Залипание супервизора после перезапуска БД | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 54 | Голодание агентов из-за SEO | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 55 | Падение по таймауту тяжёлой сборки | `adopt-v3.4` | Heavy work receives observable realistic deadlines and resource guards. |
| 56 | Молчаливый pcntl-kill | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 57 | Конфликт владения сессией при ретраях | `adopt-v3.4` | Exact attempt binding/generation fencing applies to retries. |
| 58 | Pending-истории без перезапуска | `adopt-v3.4` | Ready pending stories remain executable work and must be swept/recovered. |
| 59 | Падение worker’а из-за Playwright | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 60 | Исчерпание кучи Node | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 61 | SIGKILL от zombie-reaper | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 62 | Read-only /workspace | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 63 | Спин из-за постороннего пути в промпте | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 64 | Крэш OpenCode на кривом session id | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 65 | Единственная попытка убивается деплоем | `adopt-v3.4` | Admission/deploy interruption does not spend product correction budget. |
| 66 | Маршрутизация на provisioning-эндпоинт | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 67 | Весь трафик на одного worker’а | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 68 | Потеря работы при cURL 52 | `adopt-v3.4` | Network interruption preserves work and resumes idempotently. |
| 69 | cleanup убивает легитимные сессии | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 70 | Скрытый фолбек на слабую модель | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 71 | Занятость слотов без освобождения при таймауте | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 72 | Байт-в-байт идентичная сборка на слабой модели | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 73 | Текстовый TOOL_CALL вместо нативного | `reject/unsafe` | Reject heuristic execution of textual tool calls; explicit validated tool invocations are safer. |
| 74 | Зависание LiteLLM с одним воркером | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 75 | Маршрутизация на мёртвый апстрим через БД | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 76 | Балансировка между живым и мёртвым апстримом | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 77 | stream_timeout обрывает медленный ответ | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 78 | Повреждение venv LiteLLM | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 79 | Падения llama-qwen из-за бага repeated seq_id | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 80 | Watchdog убивает рабочую модель | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 81 | Декодирование отменяется из-за пингеров | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 82 | Бесконечный цикл без продукта | `adopt-v3.4` | Progress must produce customer/artifact evidence; loops terminate boundedly. |
| 83 | Сиротская сессия после завершения истории | `adopt-v3.4` | Story completion closes its disposable lane while preserving its handoff. |
| 84 | Зависание на 295k токенов | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 85 | Пустые заглушки вместо историй | `adopt-v3.4` | No placeholder story may count as a delivered increment outcome. |
| 86 | Оркестратор не запускает workflow | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 87 | Запуск ждёт /approve без auto_start | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 88 | Сиротская веха без workflow-истории | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 89 | Зависание из-за дубликата story_id в зависимостях | `adopt-v3.4` | Bounded DAG validation rejects duplicate edges/IDs and cycles. |
| 90 | Запуск не завершается при проваленной истории | `adopt-v3.4` | An increment reaches truthful terminal state even when a story fails. |
| 91 | Недостаточный бюджет для параллельных историй | `adopt-v3.4` | Parallel lanes retain realistic per-story time/resource budgets. |
| 92 | 5-минутные простои между событиями | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 93 | Сброс здоровых сессий из-за заморозки updated_at | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 94 | Карточка «working» при null-сессии | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 95 | Ложный статус «returned» для ИИ-вех | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 96 | Инфраструктурные ошибки исчерпывают лимит попыток | `adopt-v3.4` | Admission/environment failures do not spend product attempts. |
| 97 | История без назначенного исполнителя | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 98 | Неверная классификация на человеческие | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 99 | Меньше трёх стендапов | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 100 | Повреждённые бинарные файлы | `adopt-v3.4` | Artifact preservation is byte-safe and evidence-bound. |
| 101 | Доставка 0 файлов при реальной работе | `adopt-v3.4` | Delivery is based on observed preserved artifacts, not a declaration alone. |
| 102 | Перекрёстное загрязнение проектов | `adopt-v3.4` | Unique worktrees and project-bound evidence prevent cross-project contamination. |
| 103 | Флаг однократной доставки | `adopt-v3.4` | Artifact delivery is idempotent by exact identity, not a one-shot flag. |
| 104 | Потеря файлов упавшей параллельной истории | `adopt-v3.4` | Failed parallel stories retain artifacts for integration/recovery. |
| 105 | Несовпадение _site_root | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 106 | Отсутствующий R2-бакет | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 107 | Токен с ограниченной областью видимости | `covered-v3.3` | Existing leases, inboxes, generation fencing, observable jobs/waits, preservation, recovery or owner-gate rules already cover the portable mechanism; v3.4 does not rebuild it. |
| 108 | Чужое превью из-за глобального поиска | `adopt-v3.4` | Demonstrations and artifacts are scoped to the exact increment/run. |
| 109 | Неинтерактивное превью из-за CSP | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 110 | RSC-fetch на статике | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 111 | Блокировка статических ассетов | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 112 | Превью исчезает для failed-запуска | `adopt-v3.4` | Failed status cannot hide preserved demonstrable artifacts. |
| 113 | Аудит на неправильном хосте | `adopt-v3.4` | Real-workflow acceptance names the exact target/environment. |
| 114 | Отсутствующий GITHUB_TOKEN | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 115 | Неверный ENTRYPOINT образа | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 116 | Слишком много слоёв образа | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 117 | Prune удаляет используемые слои | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 118 | Повреждение Redis AOF после питания | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 119 | Redis OOM при жёстком лимите cgroup | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 120 | Шторм заданий MakeSearchable | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 121 | OOM Horizon при параллельных воркерах | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 122 | Орфанные containerd-директории | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 123 | Зависшие деплои | `adopt-v3.4` | Deploy stages are observable, deadline-bound external work. |
| 124 | 401 для всех инструментов из-за заголовка | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 125 | Жёсткий allowlist инструментов | `reject/unsafe` | Reject replacing explicit capability/permission checks with unconstrained semantic routing. |
| 126 | Пропуск нерусскоязычных команд | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 127 | Egress-блокировка Squid | `operator/project-specific` | Valid incident for the affected stack, but its concrete remedy belongs in project/operator configuration and acceptance—not the portable Craft coordination core. |
| 128 | Заморозка управления останавливает всё | `adopt-v3.4` | HOLD/freeze stays scope-selective; independent safe lanes continue. |
| 129 | Тёмные флаги останавливают запуск | `adopt-v3.4` | Disabled capabilities fail explicitly and degrade truthfully. |

## Coverage totals

- `adopt-v3.4`: 35
- `covered-v3.3`: 33
- `operator/project-specific`: 56
- `reject/unsafe`: 5

Total: **129/129**.

## Anti-complexity conclusion

DeepSeek is most useful as a failure-mode catalog, not as a blueprint. Protocol v3.4 imports only portable mechanisms: classified attempt accounting, bounded dependency graphs, preserved partial artifacts, batch integration/release gates, observable deployment, resource-aware parallelism and customer-first status. It deliberately leaves framework/container/database remedies with their projects and rejects semantic tool execution, simplistic content metrics and unbounded budgets.
