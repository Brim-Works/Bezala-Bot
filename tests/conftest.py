import os

# C44 — bakgrundskörningen vid uppstart (Gmail-läsning m.m.) körs inte i tester.
os.environ.setdefault("C44_STARTUP_ENABLED", "false")
# C60 — ingen omläsning av Bezala-utkast i bakgrunden och ingen paus i tester.
os.environ.setdefault("C60_VERIFY_SCHEDULE", "false")
os.environ.setdefault("C60_COUPLE_PAUSE_SECONDS", "0")
