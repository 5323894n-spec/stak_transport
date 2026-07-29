# -*- coding: utf-8 -*-
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launcher")
def test_start_bat_falls_back_to_python_launcher_with_installed_runtime(tmp_path):
    required = "import fastapi, uvicorn, openpyxl, multipart"
    runtime_dir = Path(sys.executable).resolve().parent
    base_path = os.pathsep.join(
        entry for entry in os.environ["PATH"].split(os.pathsep)
        if Path(entry or ".").resolve() != runtime_dir
    )
    probe_env = os.environ.copy()
    probe_env["PATH"] = base_path
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    python_probe = subprocess.run(
        [comspec, "/d", "/c", "python", "-c", required],
        env=probe_env,
        check=False,
    )
    py_probe = subprocess.run(
        [comspec, "/d", "/c", "py", "-c", required],
        env=probe_env,
        check=False,
    )
    if python_probe.returncode == 0 or py_probe.returncode != 0:
        pytest.skip("requires broken python command and working py launcher")

    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        test_port = port_socket.getsockname()[1]
    launcher = (ROOT / "start.bat").read_text(encoding="utf-8")
    launcher = launcher.replace(
        'set "PORT=8001"', f'set "PORT={test_port}"'
    )
    (tmp_path / "start.bat").write_text(launcher, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "selected-python.txt"

    (tmp_path / "run.py").write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['START_TEST_MARKER']).write_text(sys.executable)\n",
        encoding="utf-8",
    )
    shutil.copy2(
        Path(os.environ["SystemRoot"]) / "System32" / "where.exe",
        fake_bin / "powershell.exe",
    )

    env = probe_env.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + base_path
    env["START_TEST_MARKER"] = str(marker)
    completed = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", "start.bat"],
        cwd=tmp_path,
        env=env,
        input="\n",
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected = subprocess.check_output(
        ["py", "-c", "import sys; print(sys.executable)"], text=True
    ).strip()
    assert marker.read_text(encoding="utf-8").strip() == expected
