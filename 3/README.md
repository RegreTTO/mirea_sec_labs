# Hashcat MIREA

Подбор паролей по хешу MD5, SHA-1, bcrypt или Argon2: перебор символов или поиск по словарю на CPU.

## Запуск

Из папки `3` установите зависимости и откройте справку:

```sh
python -m pip install -r requirements.txt
python main.py --help
```

## Help

```text
usage: main.py [-h] [--algorithm ALGORITHM] [--wordlist FILE]
               [--charset NAME_OR_CHARACTERS] [--min-length N]
               [--max-length N] [--workers N] [--timeout SECONDS]
               HASH

Hashcat MIREA: CPU brute force and wordlist search for MD5, SHA-1, bcrypt, and Argon2.

positional arguments:
  HASH                  Hash to recover; quote it in your shell.

options:
  -h, --help            show this help message and exit
  --algorithm ALGORITHM
                        Hash algorithm: auto, md5, sha1, bcrypt, argon2
                        (default: auto).
  --wordlist FILE       Read password candidates from a file, preserving non-
                        UTF-8 bytes; ignores charset and length options.
  --charset NAME_OR_CHARACTERS
                        Preset name or literal characters (default: alnum).
  --min-length N        Minimum password length; 0 includes the empty password
                        (default: 1).
  --max-length N        Maximum password length (default: 6).
  --workers N           Number of CPU processes (default: 1).
  --timeout SECONDS     Time limit in seconds (default: unlimited).

Named character sets (names are case-sensitive):
  alnum: '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
  digits: '0123456789'
  ascii_letters: 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
  ascii_lowercase: 'abcdefghijklmnopqrstuvwxyz'
  ascii_uppercase: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  hexdigits: '0123456789abcdefABCDEF'
  octdigits: '01234567'
  punctuation: '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'
  whitespace: ' \t\n\r\x0b\x0c'
  printable: '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ \t\n\r\x0b\x0c'

Unknown charset names are treated as literal characters.
Duplicate characters are removed; order is preserved.
Quote hashes and literal charsets in your shell.

Examples:
  python main.py 'HASH' --charset digits --min-length 6 --max-length 6
  python main.py 'HASH' --charset abc123 --workers 4 --timeout 60
  python main.py 'HASH' --wordlist passwords.txt --workers 4
```

## Использование

Перебор четырёхзначного пароля из цифр (пример найдёт `1234`):

```sh
python main.py '81dc9bdb52d04dc20036dbd8313ed055' --charset digits --min-length 4 --max-length 4
```

Свой набор символов, четыре процесса и ограничение в 60 секунд:

```sh
python main.py 'HASH' --charset 'abc123' --min-length 1 --max-length 6 --workers 4 --timeout 60
```

Поиск по словарю — один пароль на строку в `passwords.txt`:

```sh
python main.py 'HASH' --wordlist passwords.txt --workers 4
```

Замените `HASH` своим хешем, сохранив одинарные кавычки. Алгоритм определяется автоматически; при необходимости задайте `--algorithm md5`, `sha1`, `bcrypt` или `argon2`. В режиме словаря набор символов и длина игнорируются. Остановить поиск можно через `Ctrl+C`.
