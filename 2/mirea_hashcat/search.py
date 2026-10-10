"""Lazy brute force with Windows-compatible worker processes."""

from __future__ import annotations

import multiprocessing as mp
import signal
import time
from dataclasses import dataclass
from ctypes import Array, c_ulonglong
from functools import cached_property, partial
from itertools import islice
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue as ProcessQueue
from multiprocessing.synchronize import Event
from queue import Empty
from typing import Callable, Iterator

from .charsets import CHARSETS
from .hashes import hash_password


open_wordlist = partial(open, encoding="utf-8-sig", errors="surrogateescape")


@dataclass
class SearchConfig:
    charset: str = CHARSETS["alnum"]
    min_length: int = 1
    max_length: int = 6
    workers: int = 1
    timeout: float | None = None
    wordlist: str | None = None

    @cached_property
    def total(self) -> int:
        if self.wordlist is not None:
            with open_wordlist(self.wordlist) as stream:
                return sum(1 for _ in stream)
        base = len(self.charset)
        lengths = self.max_length - self.min_length + 1
        if base == 1:
            return lengths
        return base ** self.min_length * (base ** lengths - 1) // (base - 1)


@dataclass
class Progress:
    attempts: int
    total: int
    elapsed: float
    current_length: int | None = None
    worker_candidates: tuple[str | None, ...] = ()

    @property
    def speed(self) -> float:
        return self.attempts / self.elapsed if self.elapsed else 0.0


@dataclass
class SearchResult:
    status: str
    password: str | None
    attempts: int
    total: int
    elapsed: float


def iter_candidates(charset: str, length: int, start: int, stop: int) -> Iterator[bytes]:
    """Build candidates directly from the indices assigned to this worker."""
    symbols = tuple(character.encode("utf-8", errors="surrogateescape") for character in charset)
    for index in range(start, stop):
        candidate = []
        for _ in range(length):
            index, digit = divmod(index, len(symbols))
            candidate.append(symbols[digit])
        yield b"".join(reversed(candidate))


def partition_ranges(total: int, workers: int) -> list[tuple[int, int]]:
    workers = min(workers, total)
    return [(index * total // workers, (index + 1) * total // workers)
            for index in range(workers)]


def _candidates(config: SearchConfig, length: int | None,
                start: int, stop: int) -> Iterator[bytes]:
    if length is not None:
        yield from iter_candidates(config.charset, length, start, stop)
    else:
        with open_wordlist(config.wordlist) as stream:
            for line in islice(stream, start, stop):
                yield line.removesuffix("\n").encode("utf-8", errors="surrogateescape")


def _worker(index: int, target: str, algorithm: str, config: SearchConfig,
            length: int | None, start: int, stop: int, counts: Array[c_ulonglong],
            cancelled: Event, messages: ProcessQueue) -> None:
    # The parent handles Ctrl+C, including cleanup of every child process.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)
    next_snapshot = 0
    next_cancel_check = 0
    last_candidate = None
    try:
        for candidate in _candidates(config, length, start, stop):
            now = time.perf_counter()
            # Event.is_set() takes a shared process lock. Poll by elapsed time
            # so fast hashes do not serialize workers on every candidate.
            if now >= next_cancel_check:
                if cancelled.is_set():
                    break
                next_cancel_check = now + 0.01
            if now >= next_snapshot:
                messages.put(("candidate", index, candidate.decode("utf-8", errors="surrogateescape")))
                next_snapshot = now + 1
            matched = hash_password(candidate, algorithm, target) == target
            last_candidate = candidate
            counts[index] += 1
            if matched:
                messages.put(("found", index, candidate.decode("utf-8", errors="surrogateescape")))
                cancelled.set()
                break
    except Exception as exc:
        messages.put(("error", index, f"{type(exc).__name__}: {exc}"))
        cancelled.set()
    finally:
        if last_candidate is not None:
            messages.put(("candidate", index, last_candidate.decode("utf-8", errors="surrogateescape")))
        messages.put(("done", index, None))


def _cleanup(processes: list[BaseProcess], cancelled: Event,
             messages: ProcessQueue) -> None:
    # Further console interrupts must not abort child ownership and cleanup.
    handlers = []
    try:
        for name in ("SIGINT", "SIGBREAK"):
            if hasattr(signal, name):
                number = getattr(signal, name)
                handlers.append((number, signal.signal(number, signal.SIG_IGN)))
        cancelled.set()
        started = [process for process in processes if process.pid is not None]
        deadline = time.perf_counter() + 1
        for process in started:
            process.join(max(0, deadline - time.perf_counter()))
        for process in started:
            if process.is_alive():
                process.terminate()
                process.join()
        for process in processes:
            process.close()
        messages.close()
        messages.join_thread()
    finally:
        for number, handler in reversed(handlers):
            signal.signal(number, handler)


def _run_workers(target: str, algorithm: str, config: SearchConfig, length: int | None,
                 started: float, callback: Callable[[Progress], None],
                 attempts: int) -> SearchResult:
    context = mp.get_context("spawn")
    total = config.total
    stage_total = total if length is None else len(config.charset) ** length
    ranges = partition_ranges(stage_total, config.workers)
    counts = context.Array("Q", len(ranges), lock=False)
    cancelled = context.Event()
    messages = context.Queue()
    processes = []
    worker_candidates = [None] * len(ranges)
    completed = set()
    dead_since = {}
    status, password = "exhausted", None
    next_update = started + 1
    stop_deadline = None

    def report(now: float) -> None:
        callback(Progress(attempts + sum(counts), total, now - started,
                          length, tuple(worker_candidates)))

    try:
        for index, (start, stop) in enumerate(ranges):
            process = context.Process(
                target=_worker,
                args=(index, target, algorithm, config,
                      length, start, stop, counts, cancelled, messages),
                name=f"hashcat-worker-{index + 1}",
            )
            processes.append(process)
            process.start()
        if length is not None:
            report(time.perf_counter())
        while len(completed) < len(processes):
            now = time.perf_counter()
            if status == "exhausted" and config.timeout is not None and now - started >= config.timeout:
                status = "timeout"
                cancelled.set()
            if cancelled.is_set() and stop_deadline is None:
                stop_deadline = now + 1
            if stop_deadline is not None and now >= stop_deadline:
                break
            try:
                kind, index, payload = messages.get(timeout=0.05)
            except Empty:
                for index, process in enumerate(processes):
                    if index in completed or process.exitcode is None:
                        continue
                    first_seen = dead_since.setdefault(index, now)
                    if process.exitcode != 0 or now - first_seen > 0.25:
                        raise RuntimeError(
                            f"Worker {index + 1} exited without a result (code {process.exitcode})."
                        )
            else:
                if kind == "done":
                    completed.add(index)
                elif kind == "candidate":
                    worker_candidates[index] = payload
                elif kind == "found" and status == "exhausted":
                    status, password = "found", payload
                    cancelled.set()
                elif kind == "error":
                    raise RuntimeError(f"Worker {index + 1} failed: {payload}")
            now = time.perf_counter()
            if now >= next_update:
                report(now)
                next_update = now + 1
        if any(candidate is not None for candidate in worker_candidates):
            report(time.perf_counter())
    except KeyboardInterrupt:
        status = "interrupted"
    finally:
        _cleanup(processes, cancelled, messages)
    return SearchResult(status, password, attempts + sum(counts), total,
                        time.perf_counter() - started)


def search(target: str, config: SearchConfig,
           callback: Callable[[Progress], None],
           algorithm: str) -> SearchResult:
    total = config.total
    started = time.perf_counter()
    if total == 0:
        return SearchResult("exhausted", None, 0, 0, time.perf_counter() - started)
    attempts = 0
    status, password = "exhausted", None
    lengths = ((None,) if config.wordlist is not None else
               range(config.min_length, config.max_length + 1))
    try:
        callback(Progress(0, total, 0,
                          config.min_length if config.wordlist is None else None))
        for length in lengths:
            if config.timeout is not None and time.perf_counter() - started >= config.timeout:
                status = "timeout"
                break
            result = _run_workers(target, algorithm, config, length, started, callback, attempts)
            attempts = result.attempts
            status, password = result.status, result.password
            if status != "exhausted":
                break
    except KeyboardInterrupt:
        status = "interrupted"
    return SearchResult(status, password, attempts, total, time.perf_counter() - started)
