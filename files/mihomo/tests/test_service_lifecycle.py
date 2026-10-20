"""Isolated systemd regression for the Mihomo update/scan scheduling.

The real units cannot be started from an unprivileged test process. This test
creates uniquely named linked user units with the same dependency topology,
counts the actions that must not recurse, and controls a scan so an update can
race an active scan and prove that a latest-config follow-up runs.
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
NU = os.environ.get("NU_TEST_BIN") or shutil.which("nu")
SCHEDULE = Path(__file__).resolve().parents[1] / "schedule-scan.nu"


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        if SYSTEMCTL is None:
            self.skipTest("systemctl is not available")
        if NU is None:
            self.skipTest("nushell is required for scan scheduling")
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
        self.version = self.state / "version"
        self.next_version = self.state / "next-version"
        self.block_scan = self.state / "block-scan"
        self.scan_release = self.state / "scan-release"
        self.reset_pause = self.state / "reset-pause"
        self.reset_release = self.state / "reset-release"
        self.fail_fetch = self.state / "fail-fetch"
        self.schedule_systemctl = self.state / "schedule-systemctl"
        self.unit_paths = {
            key: self.unit_dir / f"{self.prefix}-{key}.service"
            for key in ("main", "prepare", "updater", "scan", "reset")
        }
        self.units = {key: path.name for key, path in self.unit_paths.items()}
        self.addCleanup(self.cleanup)
        self.addCleanup(self.unblock_scan)

        self.config.write_text("cached configuration\n")
        self.version.write_text("cached\n")
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
            f"VERSION={self.version}",
            f"NEXT_VERSION={self.next_version}",
            f"BLOCK_SCAN={self.block_scan}",
            f"SCAN_RELEASE={self.scan_release}",
            f"RESET_PAUSE={self.reset_pause}",
            f"RESET_RELEASE={self.reset_release}",
            f"FAIL_FETCH={self.fail_fetch}",
            f"NU={NU}",
            f"SCHEDULE={SCHEDULE}",
            f"SCHEDULE_SYSTEMCTL={self.schedule_systemctl}",
            f"MAIN_UNIT={self.units['main']}",
            f"UPDATER_UNIT={self.units['updater']}",
            f"SCAN_UNIT={self.units['scan']}",
            f"SYSTEMCTL={SYSTEMCTL}",
            f"XDG_RUNTIME_DIR={self.runtime_dir}",
        ]

    def unblock_scan(self):
        """Release a controlled scan so cleanup never waits for its fixture."""
        try:
            self.block_scan.unlink(missing_ok=True)
            self.fail_fetch.unlink(missing_ok=True)
            self.scan_release.write_text("released\n")
            self.reset_release.write_text("released\n")
        except OSError:
            pass

    def unit_file(self, key: str, body: str):
        path = self.state / self.units[key]
        path.write_text(body.replace(
            "[Service]\n",
            "[Service]\nTimeoutStartSec=60s\nTimeoutStopSec=5s\n",
        ))
        self.systemctl("link", "--runtime", str(path))
        self.systemctl("daemon-reload")

    def install_units(self):
        scan = self.script(
            "scan",
            """\
scan_version=$(cat "$VERSION" 2>/dev/null || printf unknown)
trap 'printf "scan-cancelled %s\\n" "$scan_version" >> "$COUNTER"; exit 143' TERM INT
printf "scan-start %s\\n" "$scan_version" >> "$COUNTER"
if [ -e "$BLOCK_SCAN" ]; then
  printf "scan-blocked %s\\n" "$scan_version" >> "$COUNTER"
  while [ ! -e "$SCAN_RELEASE" ]; do sleep 0.05; done
fi
printf "scan-finish %s\\n" "$scan_version" >> "$COUNTER"
""",
        )
        updater = self.script(
            "updater",
            """\
printf "fetch\\n" >> "$COUNTER"
changed=0
if [ -e "$FAIL_FETCH" ]; then
  printf "fetch-failed\\n" >> "$COUNTER"
else
  if [ -e "$NEXT_VERSION" ]; then
    cat "$NEXT_VERSION" > "$VERSION"
    rm -f "$NEXT_VERSION"
    changed=1
  fi
  if [ ! -e "$CONFIG" ]; then
    printf "rendered configuration\\n" > "$CONFIG"
    "$SYSTEMCTL" --user --no-block try-restart "$MAIN_UNIT" >/dev/null 2>&1 || true
  fi
fi
if [ "$changed" = 1 ]; then
  "$NU" --no-config-file "$SCHEDULE" "$SCHEDULE_SYSTEMCTL" "$SCAN_UNIT" --changed
else
  "$NU" --no-config-file "$SCHEDULE" "$SCHEDULE_SYSTEMCTL" "$SCAN_UNIT"
fi
""",
        )
        reset = self.script(
            "reset",
            """\
printf "reset-start\\n" >> "$COUNTER"
"$SYSTEMCTL" --user stop "$SCAN_UNIT"
if [ -e "$RESET_PAUSE" ]; then
  printf "reset-paused\\n" >> "$COUNTER"
  while [ ! -e "$RESET_RELEASE" ]; do sleep 0.05; done
fi
printf "reset-done\\n" >> "$COUNTER"
"$SYSTEMCTL" --user --no-block restart "$SCAN_UNIT"
""",
        )
        prepare = self.script(
            "prepare",
            '"$SYSTEMCTL" --user start "$UPDATER_UNIT"\n',
        )
        main = self.script("main", 'printf "main\\n" >> "$COUNTER"\nexec sleep 3600\n')

        self.script("schedule-systemctl", 'exec "$SYSTEMCTL" --user "$@"\n')
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
            f"ExecStart={updater}\n",
        )
        self.unit_file(
            "reset",
            "[Unit]\n"
            "Description=Mihomo lifecycle test cache reset\n"
            "[Service]\n"
            f"Environment={environment}\n"
            "Type=oneshot\n"
            f"ExecStart={reset}\n",
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

    def events(self) -> list[str]:
        return self.counter.read_text().splitlines()

    def counts(self) -> dict[str, int]:
        lines = self.events()
        return {
            "fetch": lines.count("fetch"),
            "scan": sum(line.startswith("scan-start ") for line in lines),
            "main": lines.count("main"),
        }

    def scan_starts(self) -> list[str]:
        return [
            line.split(maxsplit=1)[1]
            for line in self.events()
            if line.startswith("scan-start ")
        ]

    def wait_until(self, predicate, description: str, timeout: float = 15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"{description}; events: {self.events()}")

    def wait_counts(self, **expected: int):
        self.wait_until(
            lambda: all(
                self.counts()[name] >= count for name, count in expected.items()
            ),
            f"counters did not reach {expected}: {self.counts()}",
            timeout=10,
        )

    def wait_scan_idle(self):
        self.wait_until(
            lambda: self.systemctl(
                "is-active", self.units["scan"], check=False
            ).stdout.strip()
            not in {"active", "activating", "reloading"},
            "previous scan did not finish",
        )

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
            self.wait_scan_idle()

        observed = self.counts()
        self.assertEqual(observed, {"fetch": 2, "scan": 2, "main": 0})

    def test_unchanged_and_failed_refreshes_do_not_cancel_long_scan(self):
        self.version.write_text("old\n")
        self.block_scan.write_text("")
        self.systemctl("--no-block", "start", self.units["scan"])
        self.wait_until(
            lambda: self.scan_starts() == ["old"],
            "initial scan did not start",
        )

        for count in (1, 2):
            self.systemctl("start", self.units["updater"])
            self.wait_counts(fetch=count)
            self.assertEqual(self.scan_starts(), ["old"])
            self.assertNotIn("scan-cancelled old", self.events())

        self.fail_fetch.write_text("")
        self.systemctl("start", self.units["updater"])
        self.wait_counts(fetch=3)
        self.assertIn("fetch-failed", self.events())
        self.assertEqual(self.scan_starts(), ["old"])
        self.assertNotIn("scan-cancelled old", self.events())
        self.assertIn(
            self.systemctl(
                "is-active", self.units["scan"], check=False
            ).stdout.strip(),
            {"active", "activating"},
        )

        self.unblock_scan()
        self.wait_until(
            lambda: "scan-finish old" in self.events(),
            "unchanged refresh cancelled or lost the valid scan",
        )
        self.assertNotIn("scan-cancelled old", self.events())

    def test_cold_boot_without_config_prepares_without_deadlock(self):
        self.systemctl("stop", self.units["main"])
        self.config.unlink()
        self.counter.write_text("")

        self.systemctl("start", self.units["main"])
        self.wait_active("main")
        self.wait_counts(fetch=1, scan=1, main=1)

        self.assertTrue(self.config.exists())

    def test_update_while_scan_is_active_cancels_it_and_scans_latest(self):
        self.version.write_text("old\n")
        self.block_scan.write_text("")
        self.systemctl("--no-block", "start", self.units["scan"])
        self.wait_until(
            lambda: self.scan_starts() == ["old"],
            "initial scan did not start with the old configuration",
        )

        self.next_version.write_text("new\n")
        self.systemctl("start", self.units["updater"])
        self.wait_until(
            lambda: self.scan_starts() == ["old", "new"],
            "refresh did not replace the active scan with a latest-config scan",
        )
        self.wait_counts(fetch=1)
        self.assertIn("scan-cancelled old", self.events())

        self.unblock_scan()
        self.wait_until(
            lambda: "scan-finish new" in self.events(),
            "replacement scan did not finish after release",
        )
        self.assertEqual(self.scan_starts(), ["old", "new"])

    def test_simultaneous_update_and_scan_start_converge_on_latest(self):
        self.version.write_text("old\n")
        self.next_version.write_text("new\n")
        self.block_scan.write_text("")
        self.systemctl("--no-block", "start", self.units["updater"])
        self.systemctl("--no-block", "start", self.units["scan"])
        self.wait_counts(fetch=1)
        self.wait_until(
            lambda: bool(self.scan_starts())
            and self.scan_starts()[-1] == "new",
            "no scan observed the refreshed configuration",
        )

        self.unblock_scan()
        self.wait_until(
            lambda: "scan-finish new" in self.events(),
            "latest scan did not finish",
        )
        starts = self.scan_starts()
        self.assertEqual(starts[-1], "new")
        if "old" in starts:
            self.assertIn("scan-cancelled old", self.events())
        self.assertEqual(self.counts()["fetch"], 1)

    def test_reset_during_refresh_keeps_its_follow_up_scan(self):
        self.version.write_text("old\n")
        self.block_scan.write_text("")
        self.systemctl("--no-block", "start", self.units["scan"])
        self.wait_until(
            lambda: self.scan_starts() == ["old"],
            "initial scan did not start with the old configuration",
        )

        self.reset_pause.write_text("")
        self.systemctl("--no-block", "start", self.units["reset"])
        self.wait_until(
            lambda: "reset-paused" in self.events(),
            "reset did not stop the active scan before pausing",
        )
        self.next_version.write_text("new\n")
        self.systemctl("start", self.units["updater"])
        self.wait_until(
            lambda: self.scan_starts() == ["old", "new"],
            "refresh did not start a scan while the reset was paused",
        )

        self.reset_release.write_text("")
        self.wait_until(
            lambda: self.scan_starts() == ["old", "new", "new"],
            "reset follow-up scan was lost behind the refresh scan",
        )
        self.unblock_scan()
        self.wait_until(
            lambda: "scan-finish new" in self.events(),
            "reset follow-up scan did not finish",
        )
        self.assertIn("scan-cancelled new", self.events())
        self.assertIn("reset-done", self.events())

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
