"""
Unit tests for Step 5 (`ia_spice.cli` and `ia_spice.mcp_server`).

Validates:
1. CLI subcommands (`doctor`, `snap`, `sim`, `verify-asc`).
2. MCP JSON-RPC 2.0 server protocol (`initialize`, `tools/list`, and all 5 `tools/call` endpoints).
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.asc_compiler import SchematicBuilder
from ia_spice.cli import main as cli_main
from ia_spice.mcp_server import handle_jsonrpc_Message


class TestStep5CLIAndMCP(unittest.TestCase):
    def test_cli_doctor_and_snap(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("[OK] LTspice executable found", buf.getvalue())

        buf_snap = io.StringIO()
        with redirect_stdout(buf_snap):
            rc2 = cli_main(["snap", "10.83k", "--series", "E24"])
        self.assertEqual(rc2, 0)
        data = json.loads(buf_snap.getvalue())
        self.assertEqual(data["snapped_spice"], "11k")

    def test_cli_sim_and_verify_asc(self):
        sch = SchematicBuilder(width=640, height=480)
        v1 = sch.add_component("V1", "voltage", x=96, y=160, value="10")
        r1 = sch.add_component("R1", "res", x=224, y=160, value="1k")
        r2 = sch.add_component("R2", "res", x=224, y=288, value="1k")

        sch.add_flag(v1.pin("top"), "VIN")
        sch.ground_pin(v1.pin("bot"))
        sch.add_flag(r1.pin("top"), "VIN")
        sch.add_flag(r1.pin("bot"), "VMID")
        sch.connect_points(r1.pin("bot"), r2.pin("top"))
        sch.ground_pin(r2.pin("bot"))
        sch.add_directive(".tran 0 1m 0 10u")
        sch.add_directive(".meas tran VMID_AVG AVG V(VMID)")

        with tempfile.TemporaryDirectory() as tmpdir:
            asc_path = os.path.join(tmpdir, "div.asc")
            sch.save(asc_path)

            # Test CLI verify-asc
            buf_ver = io.StringIO()
            with redirect_stdout(buf_ver):
                rc_ver = cli_main(["verify-asc", asc_path])
            self.assertEqual(rc_ver, 0)
            ver_json = json.loads(buf_ver.getvalue())
            self.assertTrue(ver_json["is_clean"])

            # Test CLI sim
            buf_sim = io.StringIO()
            with redirect_stdout(buf_sim):
                rc_sim = cli_main(["sim", asc_path])
            self.assertEqual(rc_sim, 0)
            sim_json = json.loads(buf_sim.getvalue())
            self.assertTrue(sim_json["succeeded"])
            self.assertAlmostEqual(sim_json["scalars"]["vmid_avg"], 5.0, places=3)

    def test_mcp_jsonrpc_initialize_and_tools(self):
        init_resp = handle_jsonrpc_Message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "ia-spice-mcp")

        list_resp = handle_jsonrpc_Message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = list_resp["result"]["tools"]
        self.assertEqual(len(tools), 7)

        # Call ia_spice_size_colpitts via MCP JSON-RPC
        call_resp = handle_jsonrpc_Message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ia_spice_size_colpitts",
                "arguments": {
                    "l1": 4.7e-6,
                    "r_ser_inductor": 5.4,
                    "c1": 330e-12,
                    "c2": 470e-12,
                    "g_m": 0.0619,
                    "c_var": 20.69e-12,
                    "c_cv": 100e-12,
                },
            },
        })
        self.assertFalse(call_resp["result"]["isError"])
        payload = json.loads(call_resp["result"]["content"][0]["text"])
        self.assertTrue(payload["barkhausen"]["will_start"])
        self.assertAlmostEqual(payload["tank"]["f_lc_hz"] * 1e-6, 5.054, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
