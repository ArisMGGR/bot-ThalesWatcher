"""ThalesWatcher tests: python3 -m unittest discover -s tests -t .

Importing the bot's modules needs credentials, so dummy ones are set here,
before any test module imports them (variables already in the environment win
over conf.env). Nothing in the tests contacts eClass, Telegram or Discord.
"""
import os

os.environ.update({
    "ECLASS_USER": "test-user",
    "ECLASS_PASS": "test-pass",
    "NOTIFY_PLATFORM": "telegram",
    "TG_TOKEN": "123456:test-token",
    "TG_CHAT": "123456",
    "SCAN_INTERVAL_HOURS": "6",
})
