# macOS mount discovery via subprocess

## Intent

Replace the fragile manual Darwin `statfs` ABI binding with a readable invocation of the system `/sbin/mount` command while preserving disk discovery on both supported operating systems.

## Goals

- Keep Linux discovery based on `/proc/self/mountinfo`.
- Discover all macOS mount points through `/sbin/mount` invoked with `subprocess.run` and no shell.
- Preserve the existing `mount`, `filesystem`, and `device` result fields consumed by `list_disks()`.
- Convert command failures, timeouts, and malformed output into a clear `ManagerError`.

## Non-goals

- Unifying Linux and macOS behind one text parser.
- Adding third-party dependencies.
- Changing disk size calculation or the `DiskService` public contract.

## Constraints

- Python 3.12 or newer.
- `/sbin/mount` must be invoked by absolute path, with captured text output, a finite timeout, and no shell.
- Valid mount records use the macOS format `<device> on <mount> (<filesystem>, <options...>)`.

## Approaches

- **Selected: `/sbin/mount` via `subprocess.run`.** Small standard-library implementation that preserves virtual and network mount points; requires a tested text parser.
- **Rejected: `diskutil` plist output.** Structured, but disk-oriented and may omit virtual or network filesystems included by the current contract.
- **Rejected: keep `getmntinfo` via `ctypes`.** Direct system API, but retains the unsafe hand-maintained `struct statfs` layout that motivated the change.

## Design

`_macos_mounts()` runs `/sbin/mount`, parses each non-empty output line from the right-hand filesystem/options suffix and then at the ` on ` separator, and returns the same dictionaries as before. It rejects malformed lines rather than silently returning incomplete disk data. `list_disks()` and Linux discovery remain unchanged.
