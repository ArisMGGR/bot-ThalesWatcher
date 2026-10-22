"""Reading and writing conf.env: one set of rules for the bot and auto_setup.py.

connection_const.py (the bot) and auto_setup.py both read conf.env through
this module, so they always see the same values. Standard library only, so
auto_setup.py can use it before the bot's packages are installed.

  - KEY=VALUE lines; blank lines and lines starting with # are skipped
  - an optional `export ` prefix is allowed
  - whitespace around the key and around the value is ignored
  - one pair of matching quotes around the value is removed ('x' or "x");
    nothing inside them is unescaped, so any value can be written as "value"
  - if a key appears twice, the later line wins (as in a shell)
  - a UTF-8 byte order mark, which some Windows editors add, is ignored
"""

QUOTES = "\"'"


def parse(text):
    """{key: value} for the KEY=VALUE lines in `text`."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
            value = value[1:-1]
        values[key] = value
    return values


def read(path):
    """parse() of the file at `path`, or {} if there is none."""
    if not path.is_file():
        return {}
    return parse(path.read_text(encoding="utf-8-sig"))


def quote(value):
    """`value` as written to conf.env, so that parse() gives it back unchanged."""
    if value and (value != value.strip() or value[0] in QUOTES or value[-1] in QUOTES):
        return f'"{value}"'
    return value
