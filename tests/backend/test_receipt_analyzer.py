"""Tester för app.services.receipt_analyzer — isolerade hjälpare."""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("APP_PASSWORD", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "")


class ExtractJsonTest(unittest.TestCase):
    """_extract_json strippar markdown-codefences som Claude ibland packar
    sin JSON i. Bug-rapport: Arlanda-mail 19993fc7c9464d90 fick
    'Kunde inte parsa Claude-JSON' eftersom tidigare implementationen
    krävde att fence-innehållet började med '{' — men '```json\n{...}'
    har 'json\\n' som prefix efter split."""

    def _parse(self, raw: str) -> dict:
        """Kör pipelinen: strip → json.loads — samma som callsiten i
        analyze() gör efter fixen."""
        from app.services.receipt_analyzer import _extract_json
        return json.loads(_extract_json(raw))

    def test_raw_json_no_fences(self):
        out = self._parse('{"a": 1, "b": "hej"}')
        self.assertEqual(out, {"a": 1, "b": "hej"})

    def test_raw_json_with_surrounding_whitespace(self):
        """User-krav: '  {"a": 1}  ' ska parsa direkt utan fence-fall."""
        out = self._parse('  \n  {"a": 1}  \n  ')
        self.assertEqual(out, {"a": 1})

    def test_fenced_with_json_language_tag(self):
        """Mest vanliga Claude-formatet: ```json\\n{...}\\n```"""
        raw = '```json\n{"is_receipt": true, "confidence": 72}\n```'
        out = self._parse(raw)
        self.assertEqual(out, {"is_receipt": True, "confidence": 72})

    def test_fenced_with_uppercase_language_tag(self):
        raw = '```JSON\n{"a": 1}\n```'
        out = self._parse(raw)
        self.assertEqual(out, {"a": 1})

    def test_fenced_without_language_tag(self):
        raw = '```\n{"a": 1}\n```'
        out = self._parse(raw)
        self.assertEqual(out, {"a": 1})

    def test_fenced_with_whitespace_around(self):
        raw = '  \n```json\n{"a": 1, "nested": {"b": 2}}\n```  \n'
        out = self._parse(raw)
        self.assertEqual(out, {"a": 1, "nested": {"b": 2}})

    def test_missing_closing_fence_still_parses(self):
        """Graceful: öppnande fence finns men stängande saknas."""
        raw = '```json\n{"a": 1}'
        out = self._parse(raw)
        self.assertEqual(out, {"a": 1})

    def test_missing_opening_fence_still_parses(self):
        """Graceful: bara stängande fence."""
        raw = '{"a": 1}\n```'
        out = self._parse(raw)
        self.assertEqual(out, {"a": 1})

    def test_arlanda_regression(self):
        """Exakt payload från error-rad 442 (modifierad för att inte
        inkludera alla fält — testar bara strip-mekaniken)."""
        raw = (
            '```json\n'
            '{\n'
            '  "is_receipt": true,\n'
            '  "confidence": 72,\n'
            '  "vendor": "Arlanda Express",\n'
            '  "amount": null,\n'
            '  "currency": "SEK",\n'
            '  "date": null,\n'
            '  "category": "Annat",\n'
            '  "summary": "Biljett"\n'
            '}\n'
            '```'
        )
        out = self._parse(raw)
        self.assertEqual(out["vendor"], "Arlanda Express")
        self.assertEqual(out["confidence"], 72)
        self.assertTrue(out["is_receipt"])

    def test_empty_raises_json_error(self):
        """Tom sträng kommer igenom som tom — json.loads kastar
        JSONDecodeError som callsiten förpackar till AnalyzerError."""
        with self.assertRaises(json.JSONDecodeError):
            self._parse("")

    def test_invalid_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            self._parse('```json\nnot valid json\n```')


if __name__ == "__main__":
    unittest.main()
