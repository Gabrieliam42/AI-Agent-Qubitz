"""Native stdlib-only supervisor and checkpoint acceptance, without model execution."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.campaign_lifecycle import supervise
from evaluation.followup_campaign_control import process_identity


@unittest.skipUnless(os.name == "nt", "Native Windows identity acceptance")
class NativeSupervisionTests(unittest.TestCase):
    def test_exact_native_process_handle_is_actually_waited(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="native-supervision-", dir=root / ".cache") as tmp:
            output = Path(tmp) / "fixture"
            script = ("import json,pathlib,sys,time;p=pathlib.Path(sys.argv[1]);p.mkdir();"
                      "(p/'summary.json').write_text(json.dumps({'status':'complete'}));time.sleep(3);sys.exit(7)")
            code = supervise([sys.executable, "-u", "-B", "-c", script, str(output)], output, identity=process_identity)
            receipt = json.loads((Path(tmp) / "fixture_supervision/coordinator_exit.json").read_text())
            self.assertEqual(code, 7)
            self.assertTrue(receipt["actual_wait"])
            self.assertEqual(receipt["exit_code"], 7)
            self.assertIsNotNone(receipt["identity"])
            self.assertIsNone(process_identity(receipt["pid"]))
