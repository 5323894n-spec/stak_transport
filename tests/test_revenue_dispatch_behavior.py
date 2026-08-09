# -*- coding: utf-8 -*-
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

HARNESSES = [
    "revenue_recalc_behavior.js",
    "revenue_render_behavior.js",
    "dispatch_deviation_behavior.js",
    "dispatch_render_behavior.js",
]


@pytest.mark.parametrize("harness", HARNESSES)
def test_js_behavior(harness):
    node = shutil.which("node")
    assert node, "Node.js is required for executable behavior tests"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "js" / harness)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
