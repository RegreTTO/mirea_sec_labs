# Code Quality and Logic Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user explicitly forbids commits and push; all commit steps are replaced by verification checkpoints.

**Goal:** Закрыть все замечания `CODE_REVIEW.md` и унифицировать Python-стиль без нового публичного API.

**Architecture:** Сохранить доменные сервисы и CLI. Исправить тестовый контракт, затем ввести минимальные typed helpers; сделать lifecycle blobs явным внутри `Operation`; разделить короткую авторизацию/аудит и медленный disk I/O; запускать Build/Test/SAST в закреплённых job containers.

**Tech Stack:** Python 3.12, sqlite3, Typer, pytest 8.4.2, Ruff 0.17.3, uv, GitHub Actions, Docker.

**Spec:** `docs/superpowers/specs/2026-11-05-code-quality-design.md`

## Global Constraints

- Ветка: `review/code-quality-fixes`.
- Не выполнять `git commit`, `git push`, создание PR или публикацию образа.
- SQLite остаётся встроенной БД; ORM и новый фасад `FileManager` запрещены.
- Публичный CLI и схема существующих таблиц сохраняются.
- Все изменения поведения идут RED → GREEN; проверки конфигурации и форматирование подтверждаются исполняемыми командами.
- Ruff — единственный formatter/linter; правила `E`, `F`, `I`, `B`, `UP`, target `py312`, line length 100.
- Build/Test/SAST используют закреплённый `python:3.12.12-slim-bookworm` digest из текущего `Dockerfile`.

## Review Focus

- Ошибка после SQLite commit, но до удаления старого blob: новое содержимое остаётся доступным, а результат явно сообщает post-commit cleanup failure — Task 3.
- Ошибка до commit после создания нового blob: метаданные и старое содержимое сохраняются, новый blob удаляется — Task 3.
- Read-only операция при наличии orphan blob не выполняет полный recovery scan; следующая blob-mutating операция очищает orphan — Task 3.
- Сессия заменяется во время медленного disk I/O: результат не аудируется от имени нового пользователя — Task 4.
- Disk I/O блокируется, но файловая операция того же хранилища продолжает выполняться — Task 4.

---

### Task 1: Единый pytest-контракт и dev-зависимости

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Remove: `tests/test_manager.py`
- Modify: `tests/test_workflows.py`
- Modify: `tests/test_imports.py`
- Modify: `ci/requirements.in`
- Regenerate: `ci/requirements.txt`

**Interfaces:**
- Produces: `[dependency-groups].dev = ["pytest==8.4.2", "ruff==0.17.3"]`.
- Produces: единая команда `uv run --frozen pytest tests -q`.
- Preserves: поведенческие сценарии CRUD, user isolation, JSON/XML, ZIP atomicity, audit, session expiry, CLI subprocess persistence.

- [ ] Удалить устаревший `test_manager.py`: его уникальные сценарии сопоставить с `test_workflows.py`/`test_cli.py`; перенести только отсутствующие consumer-visible проверки.
- [ ] Переписать `Workflows` и `Imports` с `unittest.TestCase` на pytest fixtures/parametrize/обычные assertions; Windows boundary сохранить только если он нужен поддерживаемому тестовому окружению, иначе удалить вместе с заявлением о Windows-поддержке тестов.
- [ ] Запустить `uv run --frozen --with pytest pytest tests -q` и зафиксировать RED старого контракта до удаления; ожидаемая причина — `file_manager.manager` отсутствует.
- [ ] Добавить dev group pytest/Ruff, удалить pytest из `ci/requirements.in`, обновить `uv.lock` и hash-locked `ci/requirements.txt` штатными командами uv.
- [ ] Запустить `uv sync --frozen`, затем `uv run --frozen pytest tests -q`; ожидается полный PASS без внешнего `--with`.
- [ ] Запустить `uv run --frozen pytest tests --collect-only -q` и проверить, что все сохранённые сценарии собираются один раз.

### Task 2: Ruff, типизированный CLI-контекст и style refactor

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/file_manager/cli/common.py`
- Modify: `src/file_manager/cli/application.py`
- Modify: `src/file_manager/auth/commands.py`
- Modify: `src/file_manager/audit/commands.py`
- Modify: `src/file_manager/disks/commands.py`
- Modify: Python files under `src/file_manager/`, `tests/`, `ci/check_sast.py` as required by Ruff

**Interfaces:**
- Produces: `AppSettings(TypedDict)` containing `root: Path`, `user: str`, optional `identity: Identity` after middleware.
- Produces: typed service helpers in `cli/common.py` for file/auth/audit/disk command construction.
- Produces: `DocumentKind = Literal["json", "xml"]`.
- Preserves: current CLI output, exit codes and command names.

- [ ] Run `uv run --frozen ruff check src tests ci/check_sast.py` and `ruff format --check`; record the initial RED findings.
- [ ] Add `[tool.ruff]`, `[tool.ruff.lint]` and formatter settings from the approved design.
- [ ] Introduce `AppSettings` and typed accessors; use helpers in command modules instead of repeated `settings(context)[...]` construction.
- [ ] Add concrete annotations for public functions/services, sqlite connections and `DocumentKind`; do not create DTO wrappers around `sqlite3.Row`.
- [ ] Replace cleanup catches of `BaseException` with `Exception`; retain deterministic descriptor/transaction cleanup in `finally` and context managers.
- [ ] Apply Ruff fixes/formatting to project Python only; manually resolve remaining findings without suppressions unless a rule is demonstrably wrong for platform code.
- [ ] Run Ruff check, format check and full pytest; expected PASS.

### Task 3: Targeted blob lifecycle and unambiguous post-commit cleanup

**Files:**
- Modify: `src/file_manager/auth/operations.py`
- Modify: `src/file_manager/db/storage.py`
- Modify: `src/file_manager/files/service.py`
- Modify: `src/file_manager/documents/service.py`
- Modify: `src/file_manager/archives/service.py`
- Modify: `src/file_manager/errors.py`
- Test: `tests/test_storage_lifecycle.py`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Produces: `Storage.remove(name: str) -> None` validating blob names and deleting one immutable blob.
- Produces: `Operation.put(content: bytes) -> str` and `Operation.retire(blob: str | None) -> None` tracking created/retired blobs.
- Produces: `operation_scope(..., recover_blobs: bool = False)`; recovery scan occurs only for blob-mutating operations.
- Produces: `CommittedCleanupError(ManagerError)` whose message states that the domain change committed but physical cleanup failed.
- Consumes: all file/document/archive writes use `Operation.put` and `retire`, never raw `Storage.put` for transactional blobs.

- [ ] Add RED test: create a valid orphan blob, perform a read/list operation, assert orphan remains; perform a create/write/delete operation, assert orphan is recovered.
- [ ] Add RED test: force audit/SQLite failure after `Operation.put`, assert rollback preserves prior DB/content and removes only the new blob.
- [ ] Add RED test: force deletion of a retired blob to fail after commit, assert CLI reports committed-cleanup state and a subsequent read returns newly committed content.
- [ ] Add RED test: successful write/delete/extract leaves only DB-referenced blobs.
- [ ] Query all references to `operation_scope`/`Storage.put` with LSP; if no Python server is available, use a scoped repository search and record that fallback.
- [ ] Implement per-operation created/retired sets, rollback cleanup, post-commit targeted cleanup and mutation-only recovery scan.
- [ ] Keep failure audit outside rolled-back transactions; ensure cleanup audit does not replace the committed domain state.
- [ ] Run the four lifecycle tests, then full pytest and Ruff; expected PASS.

### Task 4: Disk I/O outside the storage lock

**Files:**
- Modify: `src/file_manager/disks/service.py`
- Modify: `src/file_manager/auth/operations.py` only if a small reusable authorization/audit primitive is required
- Test: `tests/test_disks.py`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Produces: `DiskService.list() -> list[dict[str, object]]` with two short authenticated scopes around unprotected system I/O.
- Preserves: same session token is revalidated after I/O; success audit belongs only to that identity.

- [ ] Add RED concurrency test: patched `list_disks` waits on an event while a real file-list operation completes before release; old code fails because it holds global `flock`.
- [ ] Add RED session-race test: replace/logout the session while `list_disks` is running; disk call is denied and no success event is attributed to the replacement user.
- [ ] Add RED error-audit test: system disk enumeration raises; when the original token is still valid, an `error` audit event is recorded without exception text.
- [ ] Refactor `DiskService.list` into short auth scope → unlocked system I/O → short re-auth/audit scope; avoid new general-purpose abstraction unless two callers need it.
- [ ] Run disk tests, full pytest and Ruff; expected PASS without deadlocks/timeouts.

### Task 5: Containerized Build, Test and SAST jobs

**Files:**
- Modify: `../.github/workflows/main.yml`
- Modify: `ci/setup/action.yml`
- Modify: `ci/run-local.sh` only if required for job-container compatibility
- Modify: `docs/ci.md`

**Interfaces:**
- Produces: Build/Test/SAST job-level `container.image` pinned to the existing Python image digest.
- Preserves: DAG `build → (test, sast) → image → publish`, artifacts, caches, GHCR conditions and local `act` guard.
- Produces: Test job installs dev tools from `uv.lock`, installs the built wheel with `uv pip`, runs Ruff and pytest against that wheel.

- [ ] Add job containers to Build/Test/SAST; simplify `ci/setup` so it does not install a second Python and remains compatible with container jobs.
- [ ] Keep Docker daemon access only where Image needs it; Publish consumes the saved image artifact.
- [ ] Run a YAML parser/load check available in the repository environment and inspect the resolved job graph; do not add a source-text assertion test.
- [ ] If `act` and Docker are available, run `bash 1-2/ci/run-local.sh`; otherwise record the exact missing prerequisite and run equivalent build/test/SAST/image commands manually where possible.
- [ ] Run pytest and Ruff after workflow changes; expected PASS.

### Task 6: Documentation, build and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `docs/ci.md`
- Modify: `CODE_REVIEW.md`
- Modify: `docs/superpowers/specs/2026-11-05-code-quality-design.md` only for implementation rulings
- Keep: `docs/superpowers/plans/2026-11-05-code-quality.md`

**Interfaces:**
- Produces: accurate local setup/test/lint commands and status for each original review finding.
- Preserves: original findings in `CODE_REVIEW.md`; append resolution status rather than erase evidence.

- [ ] Update README for `uv sync --frozen`, pytest and Ruff commands; remove obsolete unittest/Windows-boundary statements.
- [ ] Update CI documentation for job containers, lockfile-sourced dev tools and actual local limitations.
- [ ] Append per-finding status and changed files to `CODE_REVIEW.md`.
- [ ] Run `uv lock --check`, `uv sync --frozen`, Ruff check/format check and full pytest.
- [ ] Run `uv build --build-constraint ci/build-constraints.txt` into a temporary output directory and inspect both wheel and sdist creation.
- [ ] Install the built wheel in an isolated temporary environment and run actual CLI scenario: register/login, file CRUD, JSON/XML, ZIP, audit and disks.
- [ ] Run Bandit plus `ci/check_sast.py`; expected no blocking findings.
- [ ] Verify `git status --short --branch`: branch is `review/code-quality-fixes`, changes are uncommitted, no commit/push was created.
- [ ] Request an independent final code review over the complete working-tree diff; fix Critical/Important findings via RED → GREEN, defer only documented Minor findings.
