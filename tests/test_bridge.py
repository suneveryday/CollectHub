from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    def test_bridge_emits_json_only_on_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "upstream"
            source = root / "source"
            module = source / "module"
            module.mkdir(parents=True)
            (module / "__init__.py").write_text("VERSION_MAJOR=2\nVERSION_MINOR=7\n")
            (source / "__init__.py").write_text(
                "from pathlib import Path\n"
                "class XHS:\n"
                " def __init__(self, **kwargs): self.kwargs=kwargs\n"
                " async def __aenter__(self): return self\n"
                " async def __aexit__(self,*args): return False\n"
                " async def extract(self,url,download=False):\n"
                "  print('upstream noise')\n"
                "  p=Path(self.kwargs['work_path'])/'payload'/'note_1.jpeg'\n"
                "  p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'image')\n"
                "  (p.parent/'ExploreData.db').write_bytes(b'database')\n"
                "  return [{'作品ID':'note','作品标题':'title','作品类型':'图文','下载地址':['u'],'动图地址':[None]}]\n"
            )
            staging = Path(directory) / "staging"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/xhs_downloader_bridge.py"),
                 "--upstream-root", str(root), "--url", "https://xhslink.com/demo",
                 "--staging", str(staging)],
                text=True, capture_output=True, check=False,
            )
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertIn("upstream noise", proc.stderr)
            self.assertEqual(payload["files"][0]["path"], "payload/note_1.jpeg")
            self.assertEqual(len(payload["files"]), 1)


if __name__ == "__main__":
    unittest.main()
