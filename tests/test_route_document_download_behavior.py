# -*- coding: utf-8 -*-
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "route_card_document_download_behavior.js"


def test_route_document_download_behavior():
    node = shutil.which("node")
    assert node, "Node.js is required for executable route-card behavior tests"
    result = subprocess.run(
        [node, str(HARNESS)], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
