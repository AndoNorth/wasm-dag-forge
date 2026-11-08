"""Stand-in noop guest: reads /in/input.json and writes one digest block to /out/results.xml.

The argument `hold_mib` makes it write that many MiB into its linear memory first, to probe the room
the guest has under the container cap after wasmtime has compiled it.
"""

import hashlib
import json
import struct
import sys
import xml.etree.ElementTree as ET

from wit_world import exports


class Run(exports.Run):
    def run(self) -> None:
        with open("/in/input.json") as f:
            params = json.load(f)
        if params["workload"] != "noop":
            sys.exit(1)
        held = b"\x01" * (params["args"].get("hold_mib", 0) * 1048576)
        root = ET.Element(
            "results", schema="wdf.sim.v1", seed=str(params["seed"]), held=str(len(held))
        )
        ET.SubElement(root, "block", id="noop", kind="digest")
        digest = hashlib.sha256(struct.pack(">Q", params["seed"])).hexdigest()
        root[0].append(ET.Element("row", name="seed", value=digest))
        ET.ElementTree(root).write("/out/results.xml", encoding="utf-8", xml_declaration=True)
