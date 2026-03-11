import json
import tempfile
import unittest
from pathlib import Path

from src.app.baseline_codec import decode_baselines
from src.app.config_io import load_app_config


class ConfigIoTests(unittest.TestCase):
    def test_load_app_config_repairs_duplicate_baseline_quote_and_rewrites_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                "\n".join(
                    [
                        "{",
                        '  "slots": {"count": 2, "gap_pixels": 2, "padding": 3, "keybinds": []},',
                        '  "detection": {},',
                        '  "slot_baselines": [',
                        '    {"slot_index": 0, "shape": [2, 2], "data":""AQIDBA=="}',
                        "  ]",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            loaded = load_app_config(path)

            self.assertTrue(loaded.recovered)
            self.assertFalse(loaded.used_defaults)
            self.assertIn("normal", loaded.config.slot_baselines_by_form)
            self.assertEqual(
                loaded.config.slot_baselines_by_form["normal"][0]["data"],
                "AQIDBA==",
            )
            rewritten = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(rewritten["slot_baselines"][0]["data"], "AQIDBA==")

    def test_load_app_config_falls_back_to_defaults_when_json_is_unrecoverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text('{"slot_baselines":[', encoding="utf-8")

            loaded = load_app_config(path)

            self.assertTrue(loaded.used_defaults)
            self.assertEqual(loaded.config.slot_count, 10)
            backups = list(Path(tmpdir).glob("config.broken-*.json"))
            self.assertEqual(len(backups), 1)
            rewritten = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(rewritten["slots"]["count"], 10)

    def test_decode_baselines_skips_invalid_entries_and_keeps_valid_ones(self) -> None:
        decoded = decode_baselines(
            [
                {"slot_index": 0, "shape": [2, 2], "data": "AQIDBA=="},
                {"slot_index": 1, "shape": [2, 2], "data": "not-base64"},
            ]
        )

        self.assertIn(0, decoded)
        self.assertNotIn(1, decoded)
        self.assertEqual(decoded[0].shape, (2, 2))


if __name__ == "__main__":
    unittest.main()
