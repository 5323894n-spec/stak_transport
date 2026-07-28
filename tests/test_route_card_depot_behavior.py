# -*- coding: utf-8 -*-
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "route_card_depot_behavior.js"


@pytest.mark.parametrize(
    "scenario",
    [
        "route_navigation",
        "cached_direction",
        "invalid_row",
        "empty_rows",
        "valid_payload",
        "load_failure_retry",
        "save_route_race",
        "save_direction_race",
        "saving_locks_mutations",
        "main_runtime_validation",
        "main_runtime_direction_errors",
        "document_modal_keyboard",
    ],
)
def test_route_card_depot_behavior(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for executable route-card behavior tests"
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
