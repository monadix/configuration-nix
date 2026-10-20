from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import exit_verifier as ev  # noqa: E402

NU = os.environ.get("NU_TEST_BIN") or shutil.which("nu")
MIHOMO = os.environ.get("MIHOMO_TEST_BIN") or shutil.which("mihomo")
FLOCK = os.environ.get("FLOCK_TEST_BIN") or shutil.which("flock")
RENDER = MODULE_DIR / "render.nu"
CACHED_RENDER = MODULE_DIR / "cached-render.nu"
UPDATE = MODULE_DIR / "update.nu"
SCHEDULE = MODULE_DIR / "schedule-scan.nu"
RESET = MODULE_DIR / "reset-cache.nu"
VERIFIER = MODULE_DIR / "exit_verifier.py"
CONFIG = MODULE_DIR / "config.yaml"

SUBSCRIPTION = {
    "proxies": [
        {"name": "🇷🇺 RU One", "type": "socks5", "server": "192.0.2.10", "port": 1080},
        {"name": "🇩🇪 DE One", "type": "socks5", "server": "192.0.2.11", "port": 1080},
        {
            "name": "🇳🇱 NL LTE №91 - Amsterdam",
            "type": "socks5",
            "server": "192.0.2.12",
            "port": 1080,
        },
        {"name": "🇷🇺 RU Foreign", "type": "socks5", "server": "192.0.2.13", "port": 1080},
        {"name": "🇩🇪 DE RU", "type": "socks5", "server": "192.0.2.14", "port": 1080},
        {"name": "🇫🇷 FR Unverified", "type": "socks5", "server": "192.0.2.15", "port": 1080},
    ],
    "proxy-groups": [
        {"name": "Quattro VPN", "type": "select", "proxies": ["🇩🇪 DE One", "DIRECT"]},
        {
            "name": "RU-AUTO",
            "type": "url-test",
            "include-all-proxies": True,
            "filter": "^🇷🇺",
            "url": "https://www.gstatic.com/generate_204",
        },
        {
            "name": "FOREIGN-AUTO",
            "type": "url-test",
            "include-all-proxies": True,
            "filter": "^[🇦-🇿]{2}",
            "exclude-filter": "^🇷🇺|^🇳🇱.*LTE №(91|113) -",
            "url": "https://www.gstatic.com/generate_204",
        },
    ],
    "rules": [
        "DOMAIN-SUFFIX,vpn.example,Quattro VPN",
        "MATCH,DIRECT",
    ],
    "dns": {
        "enable": True,
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "nameserver": ["8.8.8.8", "1.1.1.1"],
    },
    "profile": {"store-selected": True},
}


def local_settings() -> dict:
    settings = yaml.safe_load(CONFIG.read_text())
    settings["proxies"] = [{"name": "WORK-VPN", "type": "direct"}]
    return settings


def probe(candidate: ev.Candidate, now: float) -> dict:
    if candidate.name == "🇫🇷 FR Unverified":
        responses = {
            ev.CLOUDFLARE_TRACE_URL: ev.CurlResult(0, "rate limited", "", 429),
            ev.IPINFO_URL: ev.CurlResult(0, '{"ip":"8.8.8.8","country":"DE"}', "", 200),
        }
    else:
        if candidate.name in {"🇷🇺 RU Foreign"}:
            country = "DE"
        elif candidate.name in {"🇩🇪 DE RU"}:
            country = "RU"
        elif candidate.original_country == "RU":
            country = "RU"
        else:
            country = "DE"
        responses = {
            ev.CLOUDFLARE_TRACE_URL: ev.CurlResult(
                0, f"ip=8.8.8.8\nloc={country}\n", "", 200
            ),
            ev.IPINFO_URL: ev.CurlResult(
                0, json.dumps({"ip": "8.8.8.8", "country": country}), "", 200
            ),
        }
    return ev.probe_node(
        candidate,
        "http://127.0.0.1:1",
        runner=lambda url, proxy_url, **kwargs: responses[url],
        clock=lambda: now,
    )


class NushellSyntaxTests(unittest.TestCase):
    def test_all_nushell_scripts_parse(self):
        if NU is None:
            self.skipTest("nushell is not available")
        for script in sorted(MODULE_DIR.glob("*.nu")):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    [NU, "--no-config-file", "--ide-check", "100", str(script)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for line in result.stdout.splitlines():
                    if not line.strip():
                        continue
                    diagnostic = json.loads(line)
                    self.assertNotEqual(
                        diagnostic.get("severity"),
                        "Error",
                        f"{script.name}: {diagnostic}",
                    )


class RenderIntegrationTests(unittest.TestCase):
    def setUp(self):
        if NU is None:
            self.skipTest("nushell is not available")
        if MIHOMO is None:
            self.skipTest("mihomo is not available (set MIHOMO_TEST_BIN)")
        self.temporary = tempfile.TemporaryDirectory()
        # Cleanup callbacks run in reverse order, so process groups registered
        # by individual tests are terminated before their state is removed.
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.subscription = self.state / "subscription.yaml"
        self.subscription.write_text(
            yaml.safe_dump(SUBSCRIPTION, allow_unicode=True, sort_keys=False)
        )
        self.settings = self.state / "settings.yaml"
        self.settings.write_text(
            yaml.safe_dump(local_settings(), allow_unicode=True, sort_keys=False)
        )
        self.cache = self.state / "location-cache.json"

    def render(self) -> dict:
        result = subprocess.run(
            [
                NU,
                "--no-config-file",
                str(RENDER),
                str(self.subscription),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                MIHOMO,
                sys.executable,
                str(self.state),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("candidate.yaml", result.stdout)
        return yaml.safe_load((self.state / "candidate.yaml").read_text())

    def systemctl(self, *, active: bool = False) -> tuple[Path, Path]:
        log = self.state / "systemctl.log"
        stub = self.state / "systemctl"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, sys\n"
            f"pathlib.Path({str(log)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:2] == ['is-active']:\n"
            f"    print({'active' if active else 'inactive'!r})\n"
        )
        stub.chmod(0o755)
        return stub, log

    def schedule_scan(self, systemctl: Path, *, changed: bool):
        command = [
            NU,
            "--no-config-file",
            str(SCHEDULE),
            str(systemctl),
            "mihomo-proxy-location-check.service",
        ]
        if changed:
            command.append("--changed")
        return subprocess.run(command, capture_output=True, text=True, check=True)

    def render_apply(self, systemctl: Path, *, check: bool = True):
        return subprocess.run(
            [
                NU,
                "--no-config-file",
                str(RENDER),
                str(self.subscription),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                MIHOMO,
                str(systemctl),
                str(self.state),
            ],
            capture_output=True,
            text=True,
            check=check,
        )

    def cached_render_apply(self, systemctl: Path):
        return subprocess.run(
            [
                FLOCK,
                "--timeout",
                "120",
                str(self.state / "render.lock"),
                NU,
                "--no-config-file",
                str(CACHED_RENDER),
                str(self.state),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                str(RENDER),
                MIHOMO,
                str(systemctl),
                NU,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def populate_cache(self):
        config = self.state / "cache-config.yaml"
        config.write_text(
            yaml.safe_dump(
                {"proxies": SUBSCRIPTION["proxies"] + local_settings()["proxies"]},
                allow_unicode=True,
            )
        )
        ev.refresh(
            str(config),
            str(self.cache),
            probe_fn=probe,
            clock=time.time,
        )

    def test_verified_groups_replace_hard_coded_filters(self):
        self.populate_cache()
        rendered = self.render()
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["🇷🇺 RU One"])
        self.assertEqual(
            groups["FOREIGN-AUTO"]["proxies"],
            ["🇩🇪 DE One", "🇳🇱 NL LTE №91 - Amsterdam"],
        )
        for name in ("RU-AUTO", "FOREIGN-AUTO"):
            self.assertNotIn("include-all-proxies", groups[name])
            self.assertNotIn("filter", groups[name])
            self.assertNotIn("exclude-filter", groups[name])
        self.assertNotIn("🇷🇺 RU Foreign", groups["RU-AUTO"]["proxies"])
        self.assertNotIn("🇩🇪 DE RU", groups["RU-AUTO"]["proxies"])
        self.assertNotIn("🇷🇺 RU Foreign", groups["FOREIGN-AUTO"]["proxies"])
        self.assertNotIn("🇩🇪 DE RU", groups["FOREIGN-AUTO"]["proxies"])
        self.assertNotIn("🇫🇷 FR Unverified", groups["FOREIGN-AUTO"]["proxies"])
        self.assertEqual(groups["Quattro VPN"]["proxies"], ["FOREIGN-AUTO"])
        self.assertEqual(groups["VPN-UPLINK"]["filter"], "^🇷🇺")
        self.assertTrue(groups["VPN-UPLINK"]["include-all-proxies"])
        self.assertIn("DOMAIN-SUFFIX,vpn.example,FOREIGN-AUTO", rendered["rules"])

    def test_empty_cache_renders_reject(self):
        rendered = self.render()
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])

    def test_update_persists_subscription_and_renders(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        credential = self.state / "credential"
        server_root = self.state / "server"
        server_root.mkdir()
        (server_root / "sub.yaml").write_text(
            yaml.safe_dump(SUBSCRIPTION, allow_unicode=True, sort_keys=False)
        )
        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server_root), **kwargs)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credential.write_text(
                f"http://127.0.0.1:{server.server_port}/sub.yaml\n"
            )
            systemctl_log = self.state / "systemctl.log"
            systemctl = self.state / "systemctl"
            systemctl.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                f"pathlib.Path({str(systemctl_log)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            )
            systemctl.chmod(0o755)
            subprocess.run(
                [
                    NU,
                    "--no-config-file",
                    str(UPDATE),
                    str(credential),
                    str(self.settings),
                    str(RENDER),
                    str(CACHED_RENDER),
                    sys.executable,
                    str(VERIFIER),
                    MIHOMO,
                    str(systemctl),
                    FLOCK,
                    NU,
                    str(self.state),
                    str(SCHEDULE),
                    "mihomo-proxy-location-check.service",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertTrue((self.state / "subscription.yaml").exists())
        self.assertFalse((self.state / "subscription.next.yaml").exists())
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertIn("try-restart mihomo.service", systemctl_log.read_text())

    def test_unchanged_render_does_not_request_main_lifecycle(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        self.assertIn("try-restart mihomo.service", systemctl_log.read_text())
        systemctl_log.write_text("")

        self.render_apply(systemctl)

        self.assertEqual(systemctl_log.read_text(), "")
        self.assertFalse((self.state / "pending-application").exists())

    def test_schedule_starts_idle_and_only_replaces_changed_active_scan(self):
        if NU is None:
            self.skipTest("nushell is not available")
        unit = "mihomo-proxy-location-check.service"
        for changed, active, expected in (
            (False, False, [f"--no-block start {unit}"]),
            (False, True, []),
            (True, False, [f"--no-block restart {unit}"]),
            (True, True, [f"--no-block restart {unit}"]),
        ):
            with self.subTest(changed=changed, active=active):
                systemctl, systemctl_log = self.systemctl(active=active)
                systemctl_log.write_text("")
                self.schedule_scan(systemctl, changed=changed)
                lines = systemctl_log.read_text().splitlines()
                self.assertEqual(lines[0], f"is-active {unit}")
                self.assertEqual(lines[1:], expected)

    def test_unchanged_update_while_scan_is_active_does_not_restart_scan(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        initial, _ = self.systemctl()
        self.render_apply(initial)
        server_root = self.state / "server"
        server_root.mkdir()
        (server_root / "sub.yaml").write_text(
            yaml.safe_dump(SUBSCRIPTION, allow_unicode=True, sort_keys=False)
        )

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server_root), **kwargs)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credential = self.state / "credential"
            credential.write_text(
                f"http://127.0.0.1:{server.server_port}/sub.yaml\n"
            )
            systemctl, systemctl_log = self.systemctl(active=True)
            systemctl_log.write_text("")
            result = self.run_update(credential, systemctl)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            systemctl_log.read_text().splitlines(),
            ["is-active mihomo-proxy-location-check.service"],
        )

    def test_rules_only_update_does_not_replace_active_scan(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        initial, _ = self.systemctl()
        self.render_apply(initial)
        changed_subscription = json.loads(json.dumps(SUBSCRIPTION))
        changed_subscription["rules"].insert(0, "DOMAIN-SUFFIX,new.example,DIRECT")
        server_root = self.state / "server"
        server_root.mkdir()
        (server_root / "sub.yaml").write_text(
            yaml.safe_dump(changed_subscription, allow_unicode=True, sort_keys=False)
        )

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server_root), **kwargs)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credential = self.state / "credential"
            credential.write_text(
                f"http://127.0.0.1:{server.server_port}/sub.yaml\n"
            )
            systemctl, systemctl_log = self.systemctl(active=True)
            systemctl_log.write_text("")
            result = self.run_update(credential, systemctl)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result.returncode, 0, result.stderr)
        log = systemctl_log.read_text().splitlines()
        self.assertIn("is-active mihomo-proxy-location-check.service", log)
        self.assertIn("--no-block try-restart mihomo.service", log)
        self.assertFalse(
            any(
                "mihomo-proxy-location-check.service" in line
                and "is-active" not in line
                for line in log
            ),
            log,
        )

    def test_node_identity_change_while_scan_is_active_replaces_scan(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        initial, _ = self.systemctl()
        self.render_apply(initial)
        changed_subscription = json.loads(json.dumps(SUBSCRIPTION))
        changed_subscription["proxies"].append(
            {"name": "🇸🇪 SE New", "type": "socks5", "server": "192.0.2.16", "port": 1080}
        )
        server_root = self.state / "server"
        server_root.mkdir()
        (server_root / "sub.yaml").write_text(
            yaml.safe_dump(changed_subscription, allow_unicode=True, sort_keys=False)
        )

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server_root), **kwargs)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credential = self.state / "credential"
            credential.write_text(
                f"http://127.0.0.1:{server.server_port}/sub.yaml\n"
            )
            systemctl, systemctl_log = self.systemctl(active=True)
            systemctl_log.write_text("")
            result = self.run_update(credential, systemctl)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "--no-block restart mihomo-proxy-location-check.service",
            systemctl_log.read_text().splitlines(),
        )

    def test_failed_update_while_scan_is_active_does_not_restart_scan(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        initial, _ = self.systemctl()
        self.render_apply(initial)
        credential = self.state / "credential"
        credential.write_text("http://127.0.0.1:1/sub.yaml\n")
        systemctl, systemctl_log = self.systemctl(active=True)
        systemctl_log.write_text("")
        result = self.run_update(credential, systemctl)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Subscription update failed", result.stderr)
        log = systemctl_log.read_text().splitlines()
        self.assertIn("--no-block start mihomo.service", log)
        self.assertIn("is-active mihomo-proxy-location-check.service", log)
        self.assertFalse(
            any(
                "mihomo-proxy-location-check.service" in line
                and "is-active" not in line
                for line in log
            ),
            log,
        )

    def wait_for_file(self, path: Path, description: str, timeout: float = 10):
        deadline = time.time() + timeout
        while not path.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(path.exists(), description)

    def terminate_group(self, process: subprocess.Popen):
        try:
            # Every caller starts a new session; its group ID remains the
            # original PID even if the leader exits before its children.
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=10)

    def test_interrupted_after_install_reapplies_on_unchanged_render(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        started = self.state / "dispatch-started"
        release = self.state / "dispatch-release"
        systemctl_log = self.state / "systemctl.log"
        blocking = self.state / "systemctl"
        blocking.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, sys, time\n"
            f"pathlib.Path({str(systemctl_log)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if 'try-restart' in sys.argv:\n"
            f"    pathlib.Path({str(started)!r}).write_text('')\n"
            f"    while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.02)\n"
        )
        blocking.chmod(0o755)
        process = subprocess.Popen(
            [
                NU,
                "--no-config-file",
                str(RENDER),
                str(self.subscription),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                MIHOMO,
                str(blocking),
                str(self.state),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self.terminate_group, process)
        self.wait_for_file(started, "renderer did not reach the restart request")
        pending = self.state / "pending-application"
        self.assertTrue((self.state / "config.yaml").exists())
        self.assertTrue(pending.exists())
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)
        self.terminate_group(process)
        self.assertTrue(pending.exists())

        success, recovery_log = self.systemctl()
        result = self.cached_render_apply(success)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("try-restart mihomo.service", recovery_log.read_text())
        self.assertFalse(pending.exists())

        recovery_log.write_text("")
        self.render_apply(success)
        self.assertEqual(recovery_log.read_text(), "")

    def test_interrupted_before_install_keeps_marker_and_recovers(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        wrapper_dir = self.state / "bin"
        wrapper_dir.mkdir()
        paused = self.state / "install-paused"
        release = self.state / "install-release"
        real_mv = shutil.which("mv")
        wrapper = wrapper_dir / "mv"
        wrapper.write_text(
            "#!/bin/sh\n"
            'last=""\n'
            'for arg in "$@"; do last="$arg"; done\n'
            'case "$last" in\n'
            "  */config.yaml)\n"
            '    : > "$MV_PAUSED"\n'
            '    while [ ! -e "$MV_RELEASE" ]; do sleep 0.05; done\n'
            "    ;;\n"
            "esac\n"
            f'exec {real_mv} "$@"\n'
        )
        wrapper.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{wrapper_dir}:{environment['PATH']}",
                "MV_PAUSED": str(paused),
                "MV_RELEASE": str(release),
            }
        )
        systemctl, systemctl_log = self.systemctl()
        process = subprocess.Popen(
            [
                NU,
                "--no-config-file",
                str(RENDER),
                str(self.subscription),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                MIHOMO,
                str(systemctl),
                str(self.state),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=environment,
        )
        self.addCleanup(self.terminate_group, process)
        self.wait_for_file(paused, "renderer did not pause before installation")
        pending = self.state / "pending-application"
        self.assertFalse((self.state / "config.yaml").exists())
        self.assertTrue(pending.exists())
        self.terminate_group(process)
        release.write_text("")
        self.assertTrue(pending.exists())
        self.assertFalse((self.state / "config.yaml").exists())

        result = self.render_apply(systemctl)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state / "config.yaml").exists())
        self.assertFalse(pending.exists())
        self.assertIn("try-restart mihomo.service", systemctl_log.read_text())

    def test_failed_application_request_retries_until_dispatched(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        failing = self.state / "systemctl-fail"
        failing.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "if 'try-restart' in sys.argv:\n"
            "    print('lifecycle unavailable', file=sys.stderr)\n"
            "    sys.exit(1)\n"
        )
        failing.chmod(0o755)
        failed = self.render_apply(failing, check=False)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertIn("Could not request mihomo restart", failed.stderr)
        pending = self.state / "pending-application"
        self.assertTrue((self.state / "config.yaml").exists())
        self.assertTrue(pending.exists())
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)

        success, success_log = self.systemctl()
        result = self.render_apply(success)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("try-restart mihomo.service", success_log.read_text())
        self.assertFalse(pending.exists())

        success_log.write_text("")
        self.render_apply(success)
        self.assertEqual(success_log.read_text(), "")

    def test_unchanged_update_refetches_without_main_lifecycle(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        server_root = self.state / "server"
        server_root.mkdir()
        (server_root / "sub.yaml").write_text(
            yaml.safe_dump(SUBSCRIPTION, allow_unicode=True, sort_keys=False)
        )
        request_log = self.state / "requests"

        class CountingHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server_root), **kwargs)

            def log_message(self, *args):
                pass

            def do_GET(self):
                with request_log.open("a") as log:
                    log.write("fetch\n")
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credential = self.state / "credential"
            credential.write_text(f"http://127.0.0.1:{server.server_port}/sub.yaml\n")
            systemctl, systemctl_log = self.systemctl()
            first = self.run_update(credential, systemctl)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("try-restart mihomo.service", systemctl_log.read_text())
            systemctl_log.write_text("")
            request_log.write_text("")

            second = self.run_update(credential, systemctl)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(request_log.read_text(), "fetch\n")
        self.assertEqual(
            systemctl_log.read_text().splitlines(),
            [
                "is-active mihomo-proxy-location-check.service",
                "--no-block start mihomo-proxy-location-check.service",
            ],
        )

    def test_reset_clears_cache_and_renders_reject(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["🇷🇺 RU One"])
        systemctl_log.write_text("")
        subprocess.run(
            [
                NU,
                "--no-config-file",
                str(RESET),
                str(self.state),
                str(self.cache),
                str(self.settings),
                sys.executable,
                str(VERIFIER),
                str(RENDER),
                str(CACHED_RENDER),
                MIHOMO,
                str(systemctl),
                FLOCK,
                NU,
                "mihomo-proxy-location-check.service",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        stored = json.loads(self.cache.read_text())
        self.assertEqual(stored["entries"], {})
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])
        log = systemctl_log.read_text()
        self.assertIn("stop mihomo-proxy-location-check.service", log)
        self.assertIn(
            "--no-block restart mihomo-proxy-location-check.service", log
        )

    def test_blocked_scan_does_not_keep_expired_approvals(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        cache = json.loads(self.cache.read_text())
        expired = time.time() - 2 * 24 * 60 * 60
        for entry in cache["entries"].values():
            entry["checked_at"] = expired
            entry["expires_at"] = expired
        self.cache.write_text(json.dumps(cache))
        self.cache.chmod(0o600)
        marker = self.state / "refresh-started"
        fake_python = self.state / "python"
        fake_python.write_text(
            f"#!{sys.executable}\n"
            "import os, sys, time, pathlib\n"
            "if len(sys.argv) > 2 and sys.argv[2] == 'refresh':\n"
            f"    pathlib.Path({str(marker)!r}).write_text('started')\n"
            "    time.sleep(60)\n"
            "    sys.exit(0)\n"
            "os.execv(sys.executable, [sys.executable] + sys.argv[1:])\n"
        )
        fake_python.chmod(0o755)
        process = subprocess.Popen(
            [
                NU,
                "--no-config-file",
                str(MODULE_DIR / "location-check.nu"),
                str(self.state),
                str(self.state / "config.yaml"),
                str(self.cache),
                str(self.settings),
                str(fake_python),
                str(VERIFIER),
                str(RENDER),
                str(CACHED_RENDER),
                MIHOMO,
                sys.executable,
                sys.executable,
                str(systemctl),
                FLOCK,
                NU,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self.terminate_group, process)
        try:
            deadline = time.time() + 10
            while not marker.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "scan did not start")
        finally:
            self.terminate_group(process)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])

    def test_update_failure_renders_stored_subscription(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        cache = json.loads(self.cache.read_text())
        expired = time.time() - 2 * 24 * 60 * 60
        for entry in cache["entries"].values():
            entry["checked_at"] = expired
            entry["expires_at"] = expired
        self.cache.write_text(json.dumps(cache))
        self.cache.chmod(0o600)
        credential = self.state / "credential"
        credential.write_text("http://127.0.0.1:1/sub.yaml\n")
        systemctl_log.write_text("")
        result = subprocess.run(
            [
                NU,
                "--no-config-file",
                str(UPDATE),
                str(credential),
                str(self.settings),
                str(RENDER),
                str(CACHED_RENDER),
                sys.executable,
                str(VERIFIER),
                MIHOMO,
                str(systemctl),
                FLOCK,
                NU,
                str(self.state),
                str(SCHEDULE),
                "mihomo-proxy-location-check.service",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Subscription update failed", result.stderr)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])
        self.assertIn("try-restart mihomo.service", systemctl_log.read_text())

    def test_reset_without_subscription_uses_applied_config(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        self.subscription.unlink()
        systemctl_log.write_text("")
        result = subprocess.run(
            [
                NU,
                "--no-config-file",
                str(RESET),
                str(self.state),
                str(self.cache),
                str(self.settings),
                sys.executable,
                str(VERIFIER),
                str(RENDER),
                str(CACHED_RENDER),
                MIHOMO,
                str(systemctl),
                FLOCK,
                NU,
                "mihomo-proxy-location-check.service",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        stored = json.loads(self.cache.read_text())
        self.assertEqual(stored["entries"], {})
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])
        log = systemctl_log.read_text()
        self.assertNotIn("start mihomo-subscription.service", log)
        self.assertIn("stop mihomo-proxy-location-check.service", log)
        self.assertIn(
            "--no-block restart mihomo-proxy-location-check.service", log
        )

    def test_reset_falls_back_to_applied_config_when_subscription_unreadable(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        self.subscription.write_text("[unterminated")
        systemctl_log.write_text("")
        result = subprocess.run(
            [
                NU,
                "--no-config-file",
                str(RESET),
                str(self.state),
                str(self.cache),
                str(self.settings),
                sys.executable,
                str(VERIFIER),
                str(RENDER),
                str(CACHED_RENDER),
                MIHOMO,
                str(systemctl),
                FLOCK,
                NU,
                "mihomo-proxy-location-check.service",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying from the applied config", result.stderr)
        stored = json.loads(self.cache.read_text())
        self.assertEqual(stored["entries"], {})
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])
        self.assertNotIn("start mihomo-subscription.service", systemctl_log.read_text())

    def test_malformed_cache_renders_reject(self):
        node = SUBSCRIPTION["proxies"][1]
        self.cache.write_text(
            json.dumps(
                {
                    "schema_version": ev.SCHEMA_VERSION,
                    "generation": 1,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "entries": {ev.canonical_node_key(node): {"verdict": []}},
                }
            )
        )
        self.cache.chmod(0o600)
        rendered = self.render()
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])

    def test_failed_scan_still_drops_expired_approvals(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"][0], "🇩🇪 DE One")
        cache = json.loads(self.cache.read_text())
        expired = time.time() - 2 * 24 * 60 * 60
        for entry in cache["entries"].values():
            entry["checked_at"] = expired
            entry["expires_at"] = expired
        self.cache.write_text(json.dumps(cache))
        self.cache.chmod(0o600)
        fake_python = self.state / "python"
        fake_python.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if len(sys.argv) > 2 and sys.argv[2] == 'refresh':\n"
            "    print('simulated scan failure', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "os.execv(sys.executable, [sys.executable] + sys.argv[1:])\n"
        )
        fake_python.chmod(0o755)
        systemctl_log.write_text("")
        result = subprocess.run(
            [
                NU,
                "--no-config-file",
                str(MODULE_DIR / "location-check.nu"),
                str(self.state),
                str(self.state / "config.yaml"),
                str(self.cache),
                str(self.settings),
                str(fake_python),
                str(VERIFIER),
                str(RENDER),
                str(CACHED_RENDER),
                MIHOMO,
                sys.executable,
                sys.executable,
                str(systemctl),
                FLOCK,
                NU,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])

    def expire_cache(self):
        cache = json.loads(self.cache.read_text())
        expired = time.time() - 2 * 24 * 60 * 60
        for entry in cache["entries"].values():
            entry["checked_at"] = expired
            entry["expires_at"] = expired
        self.cache.write_text(json.dumps(cache))
        self.cache.chmod(0o600)

    def run_update(self, credential: Path, systemctl: Path):
        return subprocess.run(
            [
                NU,
                "--no-config-file",
                str(UPDATE),
                str(credential),
                str(self.settings),
                str(RENDER),
                str(CACHED_RENDER),
                sys.executable,
                str(VERIFIER),
                MIHOMO,
                str(systemctl),
                FLOCK,
                NU,
                str(self.state),
                str(SCHEDULE),
                "mihomo-proxy-location-check.service",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_update_failure_with_unreadable_subscription_uses_applied_config(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, systemctl_log = self.systemctl()
        self.render_apply(systemctl)
        self.expire_cache()
        self.subscription.write_text(
            'url: "https://user:STORED-SECRET@example.invalid/sub"\nrules: [unterminated'
        )
        credential = self.state / "credential"
        credential.write_text(
            "http://user:CREDENTIAL-SECRET@127.0.0.1:1/sub.yaml\n"
        )
        systemctl_log.write_text("")
        result = self.run_update(credential, systemctl)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Subscription update failed", result.stderr)
        self.assertIn("retrying from the applied config", result.stderr)
        self.assertNotIn("STORED-SECRET", result.stderr)
        self.assertNotIn("CREDENTIAL-SECRET", result.stderr)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])
        self.assertIn("try-restart mihomo.service", systemctl_log.read_text())

    def test_update_invalid_fetched_subscription_is_not_promoted(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, _ = self.systemctl()
        self.render_apply(systemctl)
        original = self.subscription.read_text()
        self.expire_cache()
        server_root = self.state / "server"
        server_root.mkdir()
        (server_root / "sub.yaml").write_text("[unterminated")

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(server_root), **kwargs)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credential = self.state / "credential"
            credential.write_text(
                f"http://127.0.0.1:{server.server_port}/sub.yaml\n"
            )
            result = self.run_update(credential, systemctl)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Subscription update failed", result.stderr)
        self.assertEqual(self.subscription.read_text(), original)
        self.assertFalse((self.state / "subscription.next.yaml").exists())
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])

    def test_failed_scan_with_unreadable_subscription_uses_applied_config(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, _ = self.systemctl()
        self.render_apply(systemctl)
        self.expire_cache()
        self.subscription.write_text("[unterminated")
        fake_python = self.state / "python"
        fake_python.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if len(sys.argv) > 2 and sys.argv[2] == 'refresh':\n"
            "    print('simulated scan failure', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "os.execv(sys.executable, [sys.executable] + sys.argv[1:])\n"
        )
        fake_python.chmod(0o755)
        result = subprocess.run(
            [
                NU,
                "--no-config-file",
                str(MODULE_DIR / "location-check.nu"),
                str(self.state),
                str(self.state / "config.yaml"),
                str(self.cache),
                str(self.settings),
                str(fake_python),
                str(VERIFIER),
                str(RENDER),
                str(CACHED_RENDER),
                MIHOMO,
                sys.executable,
                sys.executable,
                str(systemctl),
                FLOCK,
                NU,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        rendered = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])

    def test_cached_render_timer_uses_applied_config_without_subscription(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.populate_cache()
        systemctl, _ = self.systemctl()
        self.render_apply(systemctl)
        before = yaml.safe_load((self.state / "config.yaml").read_text())
        self.expire_cache()
        self.subscription.unlink()
        result = subprocess.run(
            [
                FLOCK,
                "--timeout",
                "120",
                str(self.state / "render.lock"),
                NU,
                "--no-config-file",
                str(CACHED_RENDER),
                str(self.state),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                str(RENDER),
                MIHOMO,
                str(systemctl),
                NU,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.subscription.exists())
        after = yaml.safe_load((self.state / "config.yaml").read_text())
        groups = {group["name"]: group for group in after["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(after["proxies"], before["proxies"])
        self.assertEqual(after["rules"], before["rules"])
        self.assertTrue((self.state / "previous.yaml").exists())

    def test_cached_render_without_any_source_fails_clearly(self):
        if FLOCK is None:
            self.skipTest("flock is not available")
        self.subscription.unlink()
        systemctl, _ = self.systemctl()
        result = subprocess.run(
            [
                FLOCK,
                "--timeout",
                "120",
                str(self.state / "render.lock"),
                NU,
                "--no-config-file",
                str(CACHED_RENDER),
                str(self.state),
                str(self.settings),
                str(self.cache),
                sys.executable,
                str(VERIFIER),
                str(RENDER),
                MIHOMO,
                str(systemctl),
                NU,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "No stored subscription or applied configuration", result.stderr
        )

    def test_escaped_surrogate_cache_renders_reject(self):
        self.cache.write_text(
            '{"schema_version":1,"generation":1,"updated_at":"\\ud800","entries":{}}',
            encoding="ascii",
        )
        self.cache.chmod(0o600)
        rendered = self.render()
        groups = {group["name"]: group for group in rendered["proxy-groups"]}
        self.assertEqual(groups["RU-AUTO"]["proxies"], ["REJECT"])
        self.assertEqual(groups["FOREIGN-AUTO"]["proxies"], ["REJECT"])


if __name__ == "__main__":
    unittest.main()
