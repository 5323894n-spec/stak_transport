# -*- coding: utf-8 -*-
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "route_geometry_editor_behavior.js"


@pytest.mark.parametrize(
    "scenario",
    [
        "draft_lifecycle",
        "anchors_locked",
        "insert_delete",
        "sparse",
        "nearest",
        "cloning",
        "invalid",
        "labels",
    ],
)
def test_route_geometry_editor_behavior(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for executable route geometry tests"
    result = subprocess.run(
        [node, str(HARNESS), scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
