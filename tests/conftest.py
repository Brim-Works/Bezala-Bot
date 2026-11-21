import os

# C44 — bakgrundskörningen vid uppstart (Gmail-läsning m.m.) körs inte i tester.
os.environ.setdefault("C44_STARTUP_ENABLED", "false")
