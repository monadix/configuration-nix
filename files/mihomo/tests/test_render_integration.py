from __future__ import annotations

import json
import os
import shutil
import signal
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

    def tearDown(self):
        self.temporary.cleanup()

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

    def systemctl(self) -> tuple[Path, Path]:
        log = self.state / "systemctl.log"
        stub = self.state / "systemctl"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, sys\n"
            f"pathlib.Path({str(log)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        )
        stub.chmod(0o755)
        return stub, log

    def render_apply(self, systemctl: Path) -> None:
        subprocess.run(
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
        self.assertEqual(systemctl_log.read_text(), "")

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
            "--no-block start mihomo-proxy-location-check.service", log
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
        try:
            deadline = time.time() + 10
            while not marker.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "scan did not start")
        finally:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.communicate(timeout=10)
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
            "--no-block start mihomo-proxy-location-check.service", log
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
