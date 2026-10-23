"""Validate generated VM Tailscale installer without touching a real VM."""

import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from backend.openstack_service import _vm_user_data


config_text = base64.b64decode(_vm_user_data("student", "test-password-123")).decode()
config = json.loads(config_text.split("\n", 1)[1])
files = {item["path"]: item for item in config["write_files"]}
script = files["/usr/local/sbin/uccloud-install-tailscale"]["content"]
unit = files["/etc/systemd/system/uccloud-tailscale-install.service"]["content"]
assert "tailscale up" not in script
assert "Restart=on-failure" in unit and "StartLimitIntervalSec=0" in unit
assert config["runcmd"] == [
    ["systemctl", "daemon-reload"],
    ["systemctl", "enable", "uccloud-tailscale-install.service"],
    ["systemctl", "start", "--no-block", "uccloud-tailscale-install.service"],
]

with tempfile.TemporaryDirectory(prefix="portal-tailscale-config-") as directory:
    root = Path(directory)
    config_file = root / "cloud-config.yaml"
    script_file = root / "uccloud-install-tailscale"
    unit_file = root / "uccloud-tailscale-install.service"
    config_file.write_text(config_text)
    script_file.write_text(script)
    script_file.chmod(0o755)
    unit_file.write_text(unit.replace("ExecStart=/usr/local/sbin/uccloud-install-tailscale",
                                      f"ExecStart={script_file}"))
    if shutil.which("cloud-init"):
        subprocess.run(["cloud-init", "schema", "--config-file", str(config_file)], check=True)
    if shutil.which("sh"):
        subprocess.run(["sh", "-n", str(script_file)], check=True)
    if shutil.which("systemd-analyze"):
        subprocess.run(["systemd-analyze", "verify", str(unit_file)], check=True)

print("VM Tailscale installer cloud-init, shell, and systemd checks passed")
