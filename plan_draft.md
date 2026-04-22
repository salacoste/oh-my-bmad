Проект: автономная multi-agent платформа разработки с удалённым управлением через Telegram
1. Идея проекта

Цель проекта — собрать единую платформу, в которой несколько AI-агентов разработки могут:

автономно выполнять инженерные задачи;
работать локально на macOS и удалённо на сервере в Docker-контейнерах;
координироваться между собой;
сообщать о ходе работы, блокерах и результатах через Telegram;
принимать управляющие команды от пользователя из Telegram;
переключаться между разными провайдерами и стилями исполнения задач без перестройки всей системы;
при необходимости выполнять реальные задачи в браузере через отдельный browser runtime.

Платформа должна быть ориентирована не на один конкретный агент, а на слой оркестрации и событий, в котором Claude Code, Codex, Gemini, GLM и browser workers могут выступать как взаимозаменяемые исполнители или специализированные роли.

2. Основные принципы
Человек задаёт направление, агенты выполняют работу.
Управление отделено от исполнения. Telegram и событийный слой не должны быть частью внутреннего контекста кодовых сессий.
Оркестрация отделена от runtime. OMC/OMX отвечают за workflow и координацию, а не за доставку уведомлений.
События отделены от текстового лога. Система должна жить на типизированных событиях, а не на парсинге случайного stdout.
Каждая сессия должна быть восстанавливаемой. После рестарта хоста, контейнера или Telegram-бота проект не должен терять управление задачами.
Провайдеры и runtime заменяемы. Claude / Codex / Gemini / GLM / browser-harness / claw-code должны входить в общую схему как адаптеры, а не как жёстко зашитые зависимости всей платформы.
Состояние должно быть прозрачным. Для каждой задачи должна существовать наблюдаемая правда: кто выполняет, на каком шаге, что заблокировано, что завершено.
Browser automation — отдельная способность, а не дефолтный tool для всех агентов.
Максимум контекста отдаётся как ресурсы, минимум действий — как инструменты, рискованные действия — только через approval.
3. Целевая модель платформы

Платформа состоит из шести логических слоёв:

Control plane — команды, маршрутизация, реестр задач, доступ пользователя через Telegram.
Orchestration plane — OMC/OMX как workflow-движки и multi-agent оркестраторы.
Execution plane — реальные runtime-сессии Claude Code / Codex / Gemini / GLM / claw-code / browser-harness.
Browser plane — специализированный слой браузерной автоматизации, изолированный от обычных coding agents.
Event plane — clawhip как единый маршрутизатор событий и уведомлений.
Persistence plane — task registry, session registry, артефакты, логи, память, маршрутизация и конфигурация.
4. Роли конкретных репозиториев и инструментов
4.1 oh-my-claudecode (OMC)

Главный orchestration-layer для Claude-first сценария.

Роль в проекте:

основной workflow-движок;
лидер-сессия для сложных задач;
запуск multi-agent execution pipelines;
управление ролями planner / critic / executor / verifier и т.д.;
запуск tmux-worker’ов через CLI team runtime;
интеграция с Codex и Gemini как внешними исполнителями и советниками.

OMC должен быть центральным интеллектом системы там, где нужен общий инженерный workflow.

4.2 oh-my-codex (OMX)

Отдельный Codex-first orchestration-layer.

Роль в проекте:

автономный runtime для Codex-centric задач;
альтернативный режим работы платформы, когда основной исполнитель — Codex;
durable state в .omx/;
team runtime, explore/wiki/hud/operator surfaces.

OMX не должен быть вложен внутрь OMC. Это соседний режим исполнения, а не подслой.

4.3 clawhip

Событийная шина и router уведомлений.

Роль в проекте:

приём типизированных событий от runtime-систем;
маршрутизация событий по sink’ам;
отправка уведомлений;
хранение конфигурации маршрутов;
tmux/git/github/provider-native monitoring;
Telegram sink и Telegram-friendly delivery contract.

clawhip — это не главный оркестратор задач, а транспорт и event bus.

4.4 claw-code

Экспериментальный или специализированный Rust runtime.

Роль в проекте:

отдельный исполняющий контур;
sandbox runtime для автономных сценариев;
event-native harness для задач, где нужен более машинно-управляемый цикл;
потенциальная основа для некоторых server-side workers.

На старте проекта claw-code не должен быть control plane всей системы. Его лучше использовать как один из execution backends.

4.5 browser-harness

Выделенный browser automation runtime, построенный как тонкий self-healing harness поверх CDP.

Роль в проекте:

выполнение реальных браузерных задач в живом Chrome или удалённом browser instance;
специализированный backend для UI validation, web automation, scraping, form flows, file uploads, manual portals и browser-based operator tasks;
выделенный browser-worker runtime для подзадач, где API отсутствует или UI проверка критична;
основа для Browser Automation Server и browser-specialist agent profile.

browser-harness не должен быть обычным общим tool для всех агентов. Его следует использовать как привилегированный browser backend с session isolation, policy-ограничениями и отдельным routing слоем.

5. Где что работает
5.1 macOS workstation

На macOS размещается:

основной пользовательский control surface для разработчика;
OMC leader runtime;
OMX leader runtime;
локальные Claude/Codex/Gemini CLI-auth профили;
локальный tmux;
доступ к активным рабочим репозиториям;
ручные operator-сценарии;
часть Telegram routing logic для “interactive control”;
live browser runtime для задач, которые должны идти в реальном пользовательском Chrome.

macOS — это primary operator node.

5.2 Server / Docker host

На сервере размещается:

Docker execution pool;
контейнеры отдельных worker-сессий;
Telegram bot service;
clawhip daemon;
task/session registry backend;
webhook ingress;
долговременные watchers и scheduled jobs;
тяжёлые тесты, сборки, интеграционные пайплайны;
remote browser workers и browser-harness bridge для headless / cloud browser use cases.

Server — это primary execution node.

6. Подробная архитектура
6.1 Telegram Control Plane

Telegram становится главным человеческим интерфейсом системы.

Задачи слоя
принимать команды от пользователя;
аутентифицировать отправителя;
преобразовывать сообщения в команды платформы;
возвращать статусы, summary, вопросы на подтверждение, блокеры и финальные результаты.
Основные команды
/task <описание> — создать новую задачу;
/status — список активных задач;
/status <task-id> — состояние конкретной задачи;
/logs <task-id> — последние события и краткая выжимка;
/approve <task-id> — разрешить risky step;
/stop <task-id> — остановить выполнение;
/resume <task-id> — возобновить;
/handoff <task-id> <provider> — перевести задачу на другой runtime;
/agent <task-id> — показать, какой агент сейчас лидер;
/runbook <task-id> — показать текущий шаг и следующий recovery action;
/browser <task-id> — показать browser session state, если задача использует browser automation.
Важный принцип

Telegram-бот не должен напрямую управлять shell-командами. Он работает только через внутренний application API и registry.

6.2 API / Session Registry

Это центральный слой правды о системе.

Он хранит
task id;
тип задачи;
owner runtime;
текущий лидер (OMC / OMX / claw-code / GLM adapter / browser-worker);
lifecycle state;
active session ids;
worktree path;
branch / PR / commit context;
retry counters;
approval requirements;
связанные артефакты;
список подписок на события;
Telegram thread / chat binding;
browser session binding, если задача использует browser automation.
Состояния задач
created
queued
planning
awaiting_approval
executing
verifying
browser-executing
blocked
handoff_pending
failed
completed
stopped

Этот registry должен быть внешним к runtime-сессиям, чтобы после перезапуска можно было восстановить управление.

6.3 Orchestration Layer
Режим A: Claude-first
Telegram создаёт задачу.
Router отправляет её в OMC leader.
OMC проводит clarification / plan / PRD / exec / verify / fix loop.
При необходимости OMC вызывает Codex/Gemini workers.
Если задача требует браузера, OMC передаёт browser-подзадачу в Browser Automation Server.
События уходят в clawhip.
clawhip отправляет summaries и alerts в Telegram.

Это основной режим для сложной инженерной работы.

Режим B: Codex-first
Telegram создаёт задачу.
Router назначает OMX owner.
OMX выполняет $deep-interview → $ralplan → $team или $ralph.
При необходимости OMX инициирует browser subtask через browser-worker.
События также публикуются в clawhip.
Telegram получает статусы и может прислать команды дальше.

Этот режим нужен для отдельных типов Codex-heavy задач.

Режим C: Experimental Rust runtime
Router направляет задачу в claw-code worker.
claw-code исполняет bounded runtime / sandbox workflow.
Вся телеметрия уходит в typed events.
clawhip маршрутизирует delivery наружу.

Это режим для специальных машинно-управляемых сценариев и экспериментов.

Режим D: Browser-specialist execution
Router распознаёт, что задача требует реального browser interaction.
Создаётся выделенный browser task owner или browser subtask.
Browser Automation Server поднимает browser-harness session.
Browser-specialist agent или orchestration owner вызывает browser tools через безопасный bridge.
Browser state и artifacts публикуются в registry.
События и summaries уходят через clawhip в Telegram.

Этот режим обязателен для UI automation, portal workflows, web scraping without API, browser-based verification и ручных внешних систем.

6.4 Execution Layer

Execution layer состоит из нескольких типов workers.

Типы workers
omc-leader
omx-leader
claude-cli-worker
codex-cli-worker
gemini-cli-worker
glm-adapter-worker
claw-code-worker
verification-worker
git-ops-worker
browser-worker-live
browser-worker-remote
browser-verification-worker
Общие требования к каждому worker
уникальный session id;
worktree isolation;
ограниченный набор полномочий;
event emission;
health/readiness probe;
tailable logs;
graceful stop;
force stop;
state snapshot;
resumable context when possible.
Дополнительные требования к browser workers
отдельный BU_NAME на каждую сессию;
отдельное browser binding в registry;
явное разделение live-browser и remote-browser режимов;
запрет shared default session для параллельных задач;
browser artifacts: screenshots, DOM extracts, URLs, tab list, action timeline;
policy-ограничения на raw CDP и helper mutation.
6.5 Browser Automation Plane

Browser Automation Plane — это специализированный слой для задач, которые требуют взаимодействия с реальным браузером.

Назначение
автоматизация браузера там, где API нет или он неудобен;
UI verification;
browser-based E2E task flows;
file uploads / downloads / forms / portals;
authenticated scraping через реальную пользовательскую сессию или cloud browser profile;
human-visible live browser control.
Почему это отдельный слой

Browser automation имеет другой профиль риска и доступа, чем обычная работа с кодом:

может затрагивать живой пользовательский браузер;
может взаимодействовать с внешними системами;
может требовать cookies / login state / real tabs;
использует coordinate clicks, screenshots, tab/session state и raw CDP.

Поэтому browser automation не должен быть обычным дефолтным инструментом executor-агента.

Режимы browser execution
Live Browser Mode

Использует уже запущенный реальный Chrome/Edge пользователя.

Применяется когда:

важен реальный пользовательский профиль;
нужна работа в живой сессии;
пользователь хочет видеть активную вкладку;
нужно использовать уже существующий login state.
Remote Browser Mode

Использует удалённый browser instance / cloud browser.

Применяется когда:

нужен параллельный sub-agent;
задача идёт на сервере;
нужен более безопасный изолированный browser runtime;
нужно не вмешиваться в live browser пользователя.
Browser session model

Каждая browser session должна иметь:

browser_session_id
task_id
worker_id
mode: live | remote
BU_NAME
active tab target
current url
last screenshot artifact
last successful action
staleness / reconnect state
profile binding
approval level
Browser policy rules
raw cdp() недоступен обычным агентам;
редактирование harness / helpers.py возможно только для browser-specialist профиля или operator mode;
goto() в текущей пользовательской вкладке запрещён как дефолтный первый шаг — предпочитать new_tab(url);
browser workers обязаны публиковать screenshots и page_info после значимых действий;
browser tasks должны использовать event summaries, а не только stdout.
6.6 Event Plane на базе clawhip

clawhip становится сердцем внешней наблюдаемости.

После доработки под Telegram он должен уметь
принимать provider-native events;
нормализовать их в общую схему;
матчить маршруты по provider, event, project, repo, branch, task_id, session_id;
рендерить payload отдельно от транспорта;
доставлять сообщения в Telegram sink;
поддерживать разные форматы: compact, alert, summary, threaded.
Рекомендуемые event families
task.created
task.assigned
task.planning.started
task.plan.ready
task.awaiting_approval
task.execution.started
task.execution.progress
task.verification.started
task.blocked
task.failed
task.completed
session.started
session.ready
session.idle
session.retry-needed
session.failed
session.finished
agent.started
agent.blocked
agent.finished
git.commit
git.branch-changed
github.pr-opened
github.pr-updated
github.pr-merged
github.issue-opened
tmux.keyword
tmux.stale
browser.session.started
browser.session.attached
browser.action.performed
browser.state.changed
browser.blocked
browser.screenshot.captured
browser.session.failed
browser.session.finished
Почему это важно

Telegram не должен читать сырые pane logs. Он должен получать уже нормализованную правду о состоянии системы.

6.7 Telegram Sink Contract

Новая доработка clawhip должна ввести отдельный Telegram sink.

Минимальный контракт sink’а
chat_id
thread_id или routing key для topic-based chats
parse_mode
message template
mention strategy
rate limit policy
deduplication key
reply target
buttons / callback data
Типы сообщений
Status summary — короткий отчёт по задаче.
Blocker alert — нужна реакция пользователя.
Approval request — подтверждение risky action.
Completion summary — результат работы.
Heartbeat / watchdog alert — зависание или простои.
Recovery recommendation — что стоит сделать дальше.
Browser action summary — browser step, screenshot, URL, текущая вкладка.
Callback actions
approve
reject
stop
retry
summarize
show logs
handoff to codex
handoff to claude
handoff to gemini
show browser status
close browser session
6.8 Persistence и память

Нужно разделить несколько типов состояния.

A. Runtime state
состояние worker’ов;
pid / tmux session / container id;
readiness;
resource usage.
B. Task state
lifecycle;
owner;
approvals;
results;
retries;
blockers.
C. Project memory
цели проекта;
локальные правила;
известные решения;
опасные зоны;
историю решений.
D. Artifacts
планы;
PRD;
логи;
summaries;
generated patches;
review reports;
issue / PR links;
browser screenshots;
browser DOM extracts;
browser action traces.
E. Routing config
правила clawhip;
Telegram маршруты;
policies per project.
F. Browser state
BU_NAME
browser mode;
tab bindings;
profile bindings;
screenshot index;
attach/reconnect state;
last page_info snapshot.
7. GLM z.ai в общей системе

GLM стоит подключать не как главный оркестратор, а как adapter-backed provider.

Лучший способ интеграции
отдельный glm-adapter сервис;
OpenAI-compatible API client;
единый внутренний контракт runTask(provider, prompt, context, budget, tools);
возможный use case: second opinion, bulk code generation, low-cost draft execution, review variant.

Таким образом GLM становится ещё одним execution backend без переписывания control plane.

8. Безопасность
Обязательные требования
allowlist Telegram user ids;
разделение read-only и write-capable workers;
подтверждение опасных действий;
секреты только через env/secret store;
project-level sandboxing;
отдельные git credentials для server workers;
изоляция worktree per task;
журналирование команд и решений;
ограничение shell tools и network egress для контейнеров;
policy layer для production repos;
отдельная browser policy для live browser действий.
Рискованные действия, требующие approval
force-push
merge в protected branch
удаление файлов
изменение CI secrets / infra configs
destructive migrations
деплой в production
raw CDP calls в live browser режиме
helper mutation в browser-harness
действия в живом браузере на критичных сайтах
browser flows, меняющие внешние аккаунты / платежи / публикации / отправку данных
Browser-specific security rules
live browser tasks только для доверенных users и доверенных agent profiles;
remote browser предпочтителен для автономных sub-agents;
browser session isolation обязательна;
browser profile reuse только через контролируемый profile binding;
no direct credentials entry from screenshot-only context;
все browser actions аудируются в artifacts/events.
9. Наблюдаемость и диагностика

Система должна отвечать на вопросы:

кто сейчас делает задачу;
где она выполняется;
что было последним успешным шагом;
что сейчас блокирует прогресс;
нужна ли реакция человека;
какой следующий recovery action;
есть ли активная browser session;
какая вкладка сейчас активна;
что было последним browser action.
Минимальные экраны/выводы
task list
task detail
session detail
event stream
worker health
approval queue
artifact index
browser session detail
screenshot timeline

Telegram должен давать user-facing summary, а веб/API/CLI могут давать operator-facing detail.

10. Основные пользовательские сценарии
Сценарий 1. Полностью автономная задача

Пользователь отправляет задачу в Telegram. Система строит план, выполняет изменения, запускает тесты, делает PR и присылает финальную сводку.

Сценарий 2. Задача с подтверждением

Система доходит до risky step, отправляет approval request в Telegram и ждёт решения.

Сценарий 3. Переназначение runtime

Задача началась в OMC, но пользователь хочет перенести исполнение в Codex-first режим. Registry меняет owner, создаёт handoff artifact, новый runtime продолжает работу.

Сценарий 4. Сбой контейнера

Worker падает. Registry фиксирует broken state. clawhip отправляет blocker alert. Система предлагает recovery action: restart / resume / reroute.

Сценарий 5. Ночная автономная работа

Пользователь запускает большую задачу с телефона. Утром получает structured summary: что сделано, какие PR открыты, что требует решения.

Сценарий 6. Browser-backed verification

После кодовых изменений verifier запускает browser subtask, который открывает приложение, проходит UI flow, снимает screenshots и публикует verification summary.

Сценарий 7. Реальная browser automation задача

Пользователь просит пройти web portal flow или собрать данные с сайта. Система назначает browser-worker, выполняет задачу через browser-harness и возвращает event-based summary с screenshot artifacts.

Сценарий 8. Safe live-browser assist

Пользователь просит помочь с реальным сайтом в его уже открытом Chrome. Browser-specialist agent работает в live browser mode, используя безопасные browser tools, при необходимости спрашивая approve на чувствительные действия.

11. Рекомендуемая топология развертывания
Вариант 1. Personal-first
macOS как leader node;
сервер как execution node;
Telegram bot на сервере;
clawhip daemon на сервере;
OMC/OMX локально;
remote workers в Docker;
live browser tasks на macOS;
remote browser tasks на сервере.
Вариант 2. Server-first
основная orchestration-инфраструктура на сервере;
macOS используется как operator console;
все workers в контейнерах;
Telegram и clawhip рядом с registry/API;
browser-harness используется главным образом через remote browser mode.
Рекомендация

Для старта лучше Personal-first, потому что Claude/Codex/Gemini auth и operator flows проще довести на macOS, а тяжёлую работу, телеметрию и remote browser workers вынести на сервер.

12. Техническое решение по интеграции репозиториев и runtime’ов
Что становится ядром
OMC — главный orchestration engine.
Что становится альтернативным runtime mode
OMX — отдельный Codex-first orchestration mode.
Что становится event bus
clawhip — маршрутизация событий и Telegram delivery.
Что становится experimental backend
claw-code — отдельный Rust harness для специальных сценариев.
Что становится browser backend
browser-harness — выделенный browser automation runtime для live/remote browser execution.

Итоговая формула:

Telegram → Control API → Task Registry → OMC/OMX/claw-code/browser-harness runtime → clawhip event bus → Telegram

Принцип интеграции browser-harness
не давать raw harness как общий инструмент всем агентам;
обернуть его Browser Automation Server;
разделить safe browser tools и privileged browser tools;
связать browser session lifecycle с Session Registry;
публиковать browser events и artifacts в общий event plane.
13. Рекомендуемый roadmap реализации
Phase 1 — Control plane baseline
Telegram bot
Task registry
Session registry
базовые команды /task, /status, /stop, /resume
интеграция OMC как primary owner
Phase 2 — Event plane baseline
clawhip как daemon
Telegram sink
typed event schema
routing by task/session/project
compact/alert/summary formats
Phase 3 — MCP / Tooling baseline
workspace server
task-registry server
artifact server
memory/wiki server
build/verification server
git server
github server
clawhip event bridge
Phase 4 — Browser automation
Browser Automation Server поверх browser-harness
live/remote browser session model
browser-worker isolation
screenshot and browser artifact pipeline
browser events in clawhip
browser-specialist capability profile
Phase 5 — Multi-runtime
OMX adapter
Gemini worker adapter
GLM adapter
claw-code adapter
handoff protocol между runtime’ами
Phase 6 — Server execution pool
Docker workers
isolated worktrees
verification workers
build/test pipelines
remote execution policies
remote browser workers
Phase 7 — Reliability
recovery loops
retry policies
dead session detection
stale alerting
resumable workflows
operator dashboards
browser reconnect/runbook flows
14. Ключевая архитектурная мысль

Этот проект не должен быть “ботом, который шлёт уведомления из терминала”.

Он должен быть операционной системой для автономной разработки, где:

Telegram — человеческий интерфейс;
OMC/OMX — слои оркестрации;
Claude/Codex/Gemini/GLM/claw-code/browser-harness — исполнители;
clawhip — событийная нервная система;
registry + artifacts — долговременная память и источник правды.

Именно такая декомпозиция позволит системе быть одновременно:

автономной;
наблюдаемой;
расширяемой;
безопасной;
управляемой с телефона или удалённо.
15. Инструменты агентов: MCP servers, tool calls и capability model

В рамках проекта инструменты агентов должны проектироваться не как единый плоский список функций, а как capability model с несколькими слоями доступа.

15.1. Базовый принцип

Нужно различать три типа возможностей:

Resources — read-only контекст: документы, логи, артефакты, схемы, конфигурации, реестры задач.
Tools — активные действия: запуск тестов, изменение файлов, создание PR, обновление task state, отправка событий.
Prompts — типовые сценарии: review PR, расследовать flaky test, собрать release summary, подготовить migration plan.

Чем больше возможностей можно отдать в виде resources, а не tools, тем безопаснее и стабильнее будет система.

15.2. Правило доступа

Каждый инструмент должен иметь:

owner;
scope;
required role;
permission level;
approval policy;
rate limit / concurrency policy;
audit log.

Нельзя давать всем агентам одинаковый tool surface.

15.3. Уровни доступа
Tier 0 — Read-only core

Доступен почти всем агентам.

Сюда входят:

чтение файлов в рабочем дереве;
поиск по коду;
чтение project memory / notepad / wiki;
чтение task registry;
чтение session registry;
чтение build/test artifacts;
чтение git status / diff / log;
чтение issue/PR metadata;
чтение browser state и screenshot artifacts.
Tier 1 — Local bounded write

Доступен исполнителям и планировщикам в sandbox.

Сюда входят:

write/edit files inside assigned worktree;
update todo/task notes;
write artifacts;
write wiki/project memory через согласованные surfaces;
запуск локальных тестов / линтеров / сборки;
создание локальных patch sets.
Tier 2 — Repo mutation

Только для доверенных runtime и обычно с policy checks.

Сюда входят:

git commit;
branch create/switch;
PR draft create;
issue comment;
label / assignee changes;
merge preparation.
Tier 3 — High-risk actions

Только после явного approval.

Сюда входят:

merge в protected branch;
force push;
delete files/directories;
dangerous shell commands;
production deploy;
destructive migrations;
secret/config mutation;
raw browser control в live browser режиме;
helper mutation для browser-harness.
15.4. Что не стоит делать MCP-инструментом

Нежелательно давать в виде обычного прямого tool call:

произвольный shell без policy envelope;
прямую отправку сообщений пользователю в Telegram в обход control plane;
прямой merge/deploy;
произвольный доступ к Docker host;
прямой доступ ко всем репозиториям сразу;
прямой unrestricted database write;
прямой raw CDP всем агентам;
произвольное редактирование browser-harness helpers без role gate.

Такие действия лучше оформлять как operator-gated workflow поверх registry + approval.

15.5. Рекомендуемые MCP servers в проекте
1. Workspace Server

Назначение: безопасная работа с кодовой базой.

Resources:

file tree
file contents
package manifests
generated summaries

Tools:

read_file
write_file
edit_file
grep_search
glob_search
list_dir
apply_patch

Это базовый сервер для всех execution agents.

2. Task Registry Server

Назначение: управление задачами и их состоянием.

Resources:

task list
task detail
assignment state
approval queue
blockers

Tools:

task_create
task_update_status
task_add_note
task_attach_artifact
task_set_blocker
task_request_approval
task_complete
task_fail
task_handoff

Этот сервер должен быть главным системным источником правды для агентов.

3. Session Registry Server

Назначение: наблюдение за runtime-сессиями.

Resources:

active sessions
worker metadata
tmux/container bindings
health snapshots
last heartbeat
browser session bindings

Tools:

session_register
session_heartbeat
session_mark_idle
session_mark_failed
session_attach_log
session_close
browser_session_register
browser_session_update_state
browser_session_close
4. Artifact Server

Назначение: работа с планами, отчётами и результатами.

Resources:

PRD
планы
summaries
logs
traces
reports
generated docs
screenshots
browser traces

Tools:

artifact_write
artifact_append
artifact_publish
artifact_index
artifact_link_to_task
5. Memory / Wiki Server

Назначение: долговременная память проекта.

Resources:

MEMORY
wiki pages
decisions
known issues
playbooks

Tools:

wiki_query
wiki_add
wiki_update
memory_add_note
memory_add_directive
memory_prune

Этот слой должен быть read-heavy и write-disciplined.

6. Build / Verification Server

Назначение: воспроизводимая проверка изменений.

Resources:

latest build status
test reports
coverage summaries
lint outputs

Tools:

run_unit_tests
run_integration_tests
run_lint
run_typecheck
run_build
run_targeted_test
collect_verification_bundle

Этот сервер особенно важен для verifier/test-engineer ролей.

7. Git Server

Назначение: безопасные git-операции через policy layer.

Resources:

git status
git diff
recent commits
branches
merge base

Tools:

git_status
git_diff
git_add
git_commit
git_checkout_branch
git_create_branch
git_rebase_safe
git_prepare_pr_branch

Force-push и destructive operations не должны быть обычными tools без approval.

8. GitHub Server

Назначение: внешнее взаимодействие с GitHub.

Resources:

issues
PR metadata
comments
CI status

Tools:

pr_create_draft
pr_update_body
pr_comment
issue_comment
label_add
reviewer_request
ci_fetch

Merge лучше держать отдельно как approval-gated action.

9. Event Server / clawhip Bridge

Назначение: публикация типизированных событий.

Resources:

recent event stream
route diagnostics
delivery health

Tools:

emit_event
emit_blocker
emit_summary
emit_approval_request
emit_completion
emit_browser_event

Этот сервер лучше использовать как системный транспорт, а не как chat API.

10. Telegram Control Server

Назначение: взаимодействие с пользователем через control plane.

Resources:

mapped chat bindings
thread bindings
pending callbacks

Tools:

telegram_request_approval
telegram_send_status
telegram_send_summary
telegram_send_question
telegram_bind_task_thread

Но эти tools не должны быть доступны всем агентам. Их должен вызывать либо control plane, либо доверенный coordinator/runtime.

11. Docker / Execution Pool Server

Назначение: управление контейнерами execution layer.

Resources:

worker pool state
container health
image versions
resource usage

Tools:

worker_start
worker_stop
worker_restart
worker_attach_repo
worker_exec_verification
worker_collect_logs

Этот сервер лучше открывать только orchestration-layer и operator agents.

12. External Docs / Research Server

Назначение: controlled web/docs access.

Resources:

cached docs
API references
internal indexes

Tools:

docs_search
docs_fetch
changelog_fetch
package_version_check

Он полезен analyst/document-specialist/architect ролям.

13. DB / Schema Server

Назначение: работа со схемами и безопасными запросами.

Resources:

schema
migrations
explain plans
readonly dataset views

Tools:

schema_introspect
migration_plan_check
run_readonly_query
validate_migration

Write-query path должен быть отдельным и редким.

14. Browser Automation Server

Назначение: безопасный bridge поверх browser-harness.

Resources:

current browser session state
page_info snapshots
tab list
screenshot index
browser action timeline
browser errors / stale session markers

Tools (safe surface):

browser_open_session
browser_new_tab
browser_page_info
browser_screenshot
browser_click
browser_type_text
browser_press_key
browser_wait_for_load
browser_list_tabs
browser_switch_tab
browser_js_eval_bounded
browser_upload_file
browser_http_get
browser_close_session

Privileged tools:

browser_raw_cdp
browser_restart_daemon
browser_start_remote_daemon
browser_sync_profile
browser_edit_helpers

Privileged tools не должны быть доступны всем агентам.

15.6. Как связать это с OMC / OMX / clawhip / claw-code / browser-harness
OMC

OMC уже естественно использует memory/state-oriented surfaces: notepad, project memory, stateful artifacts. Для него нужно дать богатый read surface и ограниченный mutation surface.

OMX

OMX уже живёт вокруг .omx/ state, native hooks, wiki и state tools. Для него стоит сделать отдельный OMX-state bridge и не смешивать его low-level runtime state с общим task registry.

clawhip

clawhip должен получать события через специализированный event server / bridge, а не через произвольные вызовы из агентов.

claw-code

claw-code имеет собственный tool surface, включая task/team/cron/LSP/MCP, но в рамках платформы его лучше оборачивать единым policy/capability слоем, а не делать основным стандартом для всех runtime’ов.

browser-harness

browser-harness должен использоваться через Browser Automation Server. Его safe tool surface можно выдавать browser-specialist агентам и verifier/browser-worker runtime’ам. Raw CDP и helper mutation должны оставаться привилегированными возможностями.

15.7. Ролевые профили tool access
planner / analyst / architect

Нужны в основном:

read-only workspace
docs/research
wiki/memory
task registry read
artifact write
executor

Нужны:

workspace read/write
targeted build/test
git bounded write
artifact write
event emit progress
verifier / test-engineer

Нужны:

workspace read
verification tools
logs/artifacts read
limited repro shell
issue/report writing
browser verification tools при UI/E2E задачах
git-master

Нужны:

git tools
GitHub PR tools
CI status
branch prep
document-specialist / writer

Нужны:

docs/resources
artifact write
wiki write
issue/PR text surfaces
browser-specialist

Нужны:

Browser Automation Server safe surface
browser screenshots / page_info / tabs / uploads / JS extract
event publishing
browser artifacts
в привилегированном режиме: raw CDP, remote daemon control, helper mutation
15.8. Что я рекомендую для v1

Минимальный полезный набор MCP servers:

workspace
task-registry
session-registry
artifact
memory/wiki
build/verification
git
github
clawhip-event-bridge
browser-automation

Отдельно, но не в первом релизе:

docker execution pool
db/schema
external docs crawler
telegram control server как прямой agent surface
privileged browser surfaces для большинства агентов
15.9. Главное архитектурное правило

Не делать каждого агента “всемогущим”.

Правильная модель такая:

максимум контекста давать через resources;
действия ограничивать role-based tools;
рискованные операции проводить через approval;
пользовательские коммуникации централизовать через control plane;
runtime-specific surfaces прятать за единым capability contract;
browser automation держать как выделенную специализированную способность.

Именно тогда multi-agent система останется управляемой, безопасной и пригодной для реальной автономной разработки.

16. Репозитории и внешние зависимости, на которые опирается проект
Основные репозитории
oh-my-claudecode — https://github.com/Yeachan-Heo/oh-my-claudecode
oh-my-codex — https://github.com/Yeachan-Heo/oh-my-codex
clawhip — https://github.com/Yeachan-Heo/clawhip
claw-code — https://github.com/ultraworkers/claw-code
browser-harness — https://github.com/browser-use/browser-harness
Связанные runtime / provider surfaces
Claude Code docs — https://docs.anthropic.com/en/docs/claude-code
Codex CLI — https://github.com/openai/codex
Gemini CLI — https://github.com/google-gemini/gemini-cli
Model Context Protocol — https://modelcontextprotocol.io/
Принцип использования этих зависимостей
OMC — основной orchestration engine.
OMX — альтернативный Codex-first orchestration mode.
clawhip — event bus и notification router.
claw-code — experimental / specialized Rust runtime.
browser-harness — browser automation backend.
Claude Code / Codex CLI / Gemini CLI — базовые execution runtimes.
MCP — контракт для серверов ресурсов, инструментов и prompts.