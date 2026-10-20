"""Isolated systemd regression for the Mihomo update/scan feedback loop.

The real units cannot be started from an unprivileged test process. This test
creates uniquely named linked user units with the same dependency topology and
counts the actions that must not recurse: subscription fetches and scan starts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

SYSTEMCTL = shutil.which("systemctl")


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        if SYSTEMCTL is None:
            self.skipTest("systemctl is not available")
        self.runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not self.runtime_dir:
            self.skipTest("no user systemd manager (XDG_RUNTIME_DIR is unset)")
        probe = subprocess.run(
            [SYSTEMCTL, "--user", "is-system-running"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if probe.returncode not in (0, 1) or probe.stdout.strip() not in {
            "running",
            "degraded",
        }:
            self.skipTest("no running user systemd manager")

        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name)
        self.unit_dir = Path(self.runtime_dir) / "systemd" / "user"
        self.prefix = f"mihomo-lifecycle-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.counter = self.state / "counters"
        self.config = self.state / "config.yaml"
        self.unit_paths = {
            key: self.unit_dir / f"{self.prefix}-{key}.service"
            for key in ("main", "prepare", "updater", "scan")
        }
        self.units = {key: path.name for key, path in self.unit_paths.items()}
        self.addCleanup(self.cleanup)

        self.config.write_text("cached configuration\n")
        self.counter.write_text("")
        self.install_units()
        self.systemctl("start", self.units["main"])
        self.wait_active("main")
        self.wait_counts(main=1)
        # Install and the initial cached boot performed no refresh; start every
        # test from a clean action count with a running main process.
        self.counter.write_text("")

    def cleanup(self):
        # Stop requests cancel pending startup jobs before waiting for cleanup.
        units = list(self.units.values())
        try:
            self.systemctl("--no-block", "stop", *units, check=False)
            try:
                self.systemctl("stop", *units, check=False)
            except subprocess.TimeoutExpired:
                self.systemctl("kill", "--signal=KILL", *units, check=False)
            self.systemctl("reset-failed", *units, check=False)
        finally:
            for path in self.unit_dir.glob(f"{self.prefix}-*.service"):
                path.unlink(missing_ok=True)
            try:
                self.systemctl("daemon-reload", check=False)
            finally:
                self.temporary.cleanup()

    def systemctl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            [SYSTEMCTL, "--user", *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=20,
        )

    def script(self, name: str, body: str) -> Path:
        path = self.state / name
        path.write_text(f"#!/bin/sh\nset -eu\n{body}")
        path.chmod(0o755)
        return path

    def environment(self) -> list[str]:
        return [
            f"COUNTER={self.counter}",
            f"CONFIG={self.config}",
            f"MAIN_UNIT={self.units['main']}",
            f"UPDATER_UNIT={self.units['updater']}",
            f"SYSTEMCTL={SYSTEMCTL}",
            f"XDG_RUNTIME_DIR={self.runtime_dir}",
        ]

    def unit_file(self, key: str, body: str):
        path = self.state / self.units[key]
        path.write_text(body.replace(
            "[Service]\n",
            "[Service]\nTimeoutStartSec=15s\nTimeoutStopSec=5s\n",
        ))
        self.systemctl("link", "--runtime", str(path))
        self.systemctl("daemon-reload")

    def install_units(self):
        scan = self.script("scan", 'printf "scan\\n" >> "$COUNTER"\n')
        updater = self.script(
            "updater",
            'printf "fetch\\n" >> "$COUNTER"\n'
            'if [ ! -e "$CONFIG" ]; then\n'
            '  printf "rendered configuration\\n" > "$CONFIG"\n'
            '  "$SYSTEMCTL" --user --no-block try-restart "$MAIN_UNIT" >/dev/null 2>&1 || true\n'
            "fi\n",
        )
        prepare = self.script(
            "prepare",
            '"$SYSTEMCTL" --user start "$UPDATER_UNIT"\n',
        )
        main = self.script("main", 'printf "main\\n" >> "$COUNTER"\nexec sleep 3600\n')

        self.unit_dir.mkdir(parents=True, exist_ok=True)
        environment = " ".join(self.environment())
        self.unit_file(
            "scan",
            "[Unit]\n"
            "Description=Mihomo lifecycle test scan\n"
            f"After={self.units['updater']}\n"
            "[Service]\n"
            f"Environment={environment}\n"
            "Type=oneshot\n"
            f"ExecStart={scan}\n",
        )
        self.unit_file(
            "updater",
            "[Unit]\n"
            "Description=Mihomo lifecycle test subscription updater\n"
            "[Service]\n"
            f"Environment={environment}\n"
            "Type=oneshot\n"
            f"ExecStart={updater}\n"
            f"ExecStartPost={SYSTEMCTL} --user --no-block start {self.units['scan']}\n",
        )
        self.unit_file(
            "prepare",
            "[Unit]\n"
            "Description=Mihomo lifecycle test preparation\n"
            f"ConditionPathExists=!{self.config}\n"
            "[Service]\n"
            f"Environment={environment}\n"
            "Type=oneshot\n"
            f"ExecStart={prepare}\n",
        )
        self.unit_file(
            "main",
            "[Unit]\n"
            "Description=Mihomo lifecycle test main service\n"
            f"Requires={self.units['prepare']}\n"
            f"After={self.units['prepare']}\n"
            "[Service]\n"
            f"Environment={environment}\n"
            "Type=simple\n"
            f"ExecStart={main}\n",
        )

    def wait_active(self, key: str):
        deadline = time.time() + 15
        while time.time() < deadline:
            result = self.systemctl("is-active", self.units[key], check=False)
            if result.stdout.strip() == "active":
                return
            time.sleep(0.05)
        self.fail(f"{self.units[key]} did not become active")

    def counts(self) -> dict[str, int]:
        lines = self.counter.read_text().splitlines()
        return {name: lines.count(name) for name in ("fetch", "scan", "main")}

    def wait_counts(self, **expected: int):
        deadline = time.time() + 10
        while time.time() < deadline:
            observed = self.counts()
            if all(observed[name] >= count for name, count in expected.items()):
                return
            time.sleep(0.05)
        self.fail(f"counters did not reach {expected}: {self.counts()}")

    def test_repeated_main_restarts_do_not_fetch_or_scan(self):
        for count in range(1, 4):
            self.systemctl("restart", self.units["main"])
            self.wait_active("main")
            self.wait_counts(main=count)

        observed = self.counts()
        self.assertEqual(observed["main"], 3)
        self.assertEqual(observed["fetch"], 0)
        self.assertEqual(observed["scan"], 0)

    def test_explicit_updater_run_fetches_and_triggers_scan(self):
        for count in range(1, 3):
            self.systemctl("start", self.units["updater"])
            self.wait_counts(fetch=count, scan=count)

        observed = self.counts()
        self.assertEqual(observed, {"fetch": 2, "scan": 2, "main": 0})

    def test_cold_boot_without_config_prepares_without_deadlock(self):
        self.systemctl("stop", self.units["main"])
        self.config.unlink()
        self.counter.write_text("")

        self.systemctl("start", self.units["main"])
        self.wait_active("main")
        self.wait_counts(fetch=1, scan=1, main=1)

        self.assertTrue(self.config.exists())

    def test_cached_boot_starts_main_without_fetching(self):
        self.systemctl("stop", self.units["main"])
        self.counter.write_text("")

        self.systemctl("start", self.units["main"])
        self.wait_active("main")
        self.wait_counts(main=1)

        observed = self.counts()
        self.assertEqual(observed["fetch"], 0)
        self.assertEqual(observed["scan"], 0)


if __name__ == "__main__":
    unittest.main()
