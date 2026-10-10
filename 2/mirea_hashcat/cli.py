"""English console interface."""

import argparse
import shutil
import signal
import sys
import textwrap

from colorama import just_fix_windows_console

from .charsets import CHARSETS, resolve_charset
from .hashes import ALGORITHMS, detect_algorithm
from .search import Progress, SearchConfig, SearchResult, search


EXIT_CODES = {"found": 0, "exhausted": 1, "timeout": 124, "interrupted": 130}


def build_parser() -> argparse.ArgumentParser:
    presets = "\n".join(f"  {name}: {characters!r}" for name, characters in CHARSETS.items())
    parser = argparse.ArgumentParser(
        description="Hashcat MIREA: CPU brute force and wordlist search for MD5, SHA-1, bcrypt, and Argon2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Named character sets (names are case-sensitive):\n" + presets
                + "\n\nUnknown charset names are treated as literal characters."
                  "\nDuplicate characters are removed; order is preserved."
                  "\nQuote hashes and literal charsets in your shell."
                  "\n\nExamples:"
                  "\n  python main.py 'HASH' --charset digits --min-length 6 --max-length 6"
                  "\n  python main.py 'HASH' --charset abc123 --workers 4 --timeout 60"
                  "\n  python main.py 'HASH' --wordlist passwords.txt --workers 4"),
    )
    parser.add_argument("hash", metavar="HASH", help="Hash to recover; quote it in your shell.")
    parser.add_argument("--algorithm", default="auto",
                        help=f"Hash algorithm: {', '.join(ALGORITHMS)} (default: auto).")
    parser.add_argument("--wordlist", metavar="FILE",
                        help="Read password candidates from a file, preserving non-UTF-8 bytes; ignores charset and length options.")
    parser.add_argument("--charset", default="alnum", metavar="NAME_OR_CHARACTERS",
                        help="Preset name or literal characters (default: alnum).")
    parser.add_argument("--min-length", type=int, default=1, metavar="N",
                        help="Minimum password length; 0 includes the empty password (default: 1).")
    parser.add_argument("--max-length", type=int, default=6, metavar="N",
                        help="Maximum password length (default: 6).")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help="Number of CPU processes (default: 1).")
    parser.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                        help="Time limit in seconds (default: unlimited).")
    return parser


def _summary(result: SearchResult) -> None:
    messages = {
        "found": f"Password found: {result.password!r}",
        "exhausted": "Search space exhausted. Password not found.",
        "timeout": "Time limit reached. Password not found.",
    }
    print(messages.get(result.status, "Search interrupted."))
    print(f"Checked: {result.attempts:,} / {result.total:,}")
    print(f"Elapsed: {result.elapsed:.3f} s")
    speed = result.attempts / result.elapsed if result.elapsed else 0
    print(f"Speed: {speed:,.0f} candidates/s")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    previous_break_handler = None
    if hasattr(signal, "SIGBREAK"):
        previous_break_handler = signal.signal(signal.SIGBREAK, signal.default_int_handler)
    just_fix_windows_console()
    progress_rows = 0

    def show_progress(progress: Progress) -> None:
        nonlocal progress_rows
        length_line = (f"Searching passwords of length: {progress.current_length}"
                       if progress.current_length is not None else None)
        percentage = progress.attempts / progress.total * 100
        worker_lines = [f"Worker {index}: {candidate!r}"
                        for index, candidate in enumerate(progress.worker_candidates, 1)
                        if candidate is not None]
        line = (f"Progress: {progress.attempts:,}/{progress.total:,} ({percentage:.2f}%)"
                f" | Speed: {progress.speed:,.0f} candidates/s"
                f" | Elapsed: {progress.elapsed:.2f} s")
        width = max(1, shutil.get_terminal_size().columns - 1)
        lines = ([length_line[:width]] if length_line is not None else [])
        lines += textwrap.wrap(line, width=width)
        lines += [worker_line if len(worker_line) <= width else worker_line[:max(0, width - 3)] + "..."
                  for worker_line in worker_lines]
        prefix = f"\x1b[{progress_rows}A\r\x1b[J" if progress_rows else ""
        print(prefix + "\n".join(lines), flush=True)
        progress_rows = len(lines)

    try:
        arguments = build_parser().parse_args()
        target = arguments.hash.strip()
        algorithm = detect_algorithm(target) if arguments.algorithm == "auto" else arguments.algorithm
        if algorithm in ("md5", "sha1"):
            target = target.lower()
        config = SearchConfig(resolve_charset(arguments.charset), arguments.min_length,
                              arguments.max_length, arguments.workers, arguments.timeout, arguments.wordlist)
        print("Hashcat MIREA")
        print(f"Algorithm: {algorithm}")
        if config.wordlist is None:
            print("Mode: brute force")
            print(f"Charset: {config.charset!r}")
            print(f"Length: {config.min_length}..{config.max_length}")
        else:
            print("Mode: wordlist")
            print(f"Wordlist: {config.wordlist}")
        print(f"Workers: {min(config.workers, config.total)}")
        print(f"Search space: {config.total:,} candidates", flush=True)
        result = search(target, config, show_progress, algorithm=algorithm)
        _summary(result)
        return EXIT_CODES[result.status]
    except KeyboardInterrupt:
        print("Search interrupted.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if previous_break_handler is not None:
            signal.signal(signal.SIGBREAK, previous_break_handler)
