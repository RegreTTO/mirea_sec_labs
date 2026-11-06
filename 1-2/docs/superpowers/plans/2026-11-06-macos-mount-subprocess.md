# macOS Mount Subprocess Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace macOS `ctypes`/`getmntinfo` disk discovery with `/sbin/mount` executed through `subprocess.run` while preserving Linux behavior and the existing result contract.

**Architecture:** Keep platform dispatch and Linux `/proc/self/mountinfo` parsing unchanged. Make `_macos_mounts()` own command execution and parsing, returning the same dictionaries consumed by `list_disks()`.

**Tech Stack:** Python 3.12 standard library (`subprocess`), pytest 8.4.2

**Spec:** `docs/superpowers/specs/2026-11-06-macos-mount-subprocess-design.md`

## Global Constraints

- Support both macOS and Linux.
- Invoke absolute `/sbin/mount` without a shell and with a finite timeout.
- Add no third-party dependency.
- Preserve result keys `mount`, `filesystem`, and `device`.

## Review Focus

- Mount paths containing spaces must remain intact.
- Device descriptions containing ` on ` must parse at the separator adjacent to the mount suffix.
- A non-zero command exit must become `ManagerError`, not leak `CalledProcessError`.
- A command timeout must become `ManagerError`, not leak `TimeoutExpired`.
- Malformed non-empty output must fail clearly instead of being silently skipped.

---

### Task 1: Replace Darwin ABI binding with the mount CLI

**Files:**
- Modify: `src/file_manager/disks/service.py:3-72`
- Test: `tests/test_disks.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: macOS `/sbin/mount` records in `<device> on <mount> (<filesystem>, <options...>)` format.
- Produces: `_macos_mounts() -> list[dict[str, object]]`, preserving `mount`, `filesystem`, and `device`.

- [ ] **Step 1: Write failing tests for successful parsing and failures**

Add tests that patch `subprocess.run`, assert exact invocation safety, preserve a mount path with spaces and a device containing ` on `, and assert `ManagerError` for non-zero exit, timeout, and malformed output.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_disks.py -v`
Expected: FAIL because `_macos_mounts()` still calls `ctypes.CDLL`.

- [ ] **Step 3: Implement `_macos_mounts() -> list[dict[str, object]]`**

Replace `ctypes` with `subprocess`; execute `/sbin/mount` using an argument list, captured UTF-8 text, `check=True`, and a finite timeout. Parse from the right so spaces in device and mount values survive. Wrap subprocess and parse failures in `ManagerError` with user-facing Russian messages.

- [ ] **Step 4: Document the platform-specific discovery sources**

Update the disk section in `README.md` to state that Linux reads `/proc/self/mountinfo` and macOS invokes `/sbin/mount`.

- [ ] **Step 5: Run focused and full verification**

Run: `uv run --frozen pytest tests/test_disks.py -v`
Expected: all disk tests PASS.

Run: `uv run --frozen pytest`
Expected: full suite PASS.

Run: `uv run --frozen ruff check .`
Expected: no lint errors.

Run the real CLI disk command under an authenticated temporary workspace and confirm it returns JSON mount records.
