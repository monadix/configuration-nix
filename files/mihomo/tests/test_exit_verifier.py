from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import exit_verifier as ev  # noqa: E402


def proxy(name: str, *, password: str = "secret-value", port: int = 1080, **extra):
    value = {
        "name": name,
        "type": "socks5",
        "server": "proxy.example",
        "port": port,
        "username": "user",
        "password": password,
    }
    value.update(extra)
    return value


def write_config(path: Path, proxies, **extra) -> None:
    payload = {"proxies": proxies}
    payload.update(extra)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def write_cache(path: Path, generation: int, entries: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": ev.SCHEMA_VERSION,
                "generation": generation,
                "updated_at": "2026-01-01T00:00:00Z",
                "entries": entries,
            },
            ensure_ascii=False,
        )
    )
    path.chmod(0o600)


def probe_evidence(endpoint: str, url: str, country: str, ip: str, checked_at: float):
    return {
        "endpoint": endpoint,
        "url": url,
        "checked_at": ev.iso_time(checked_at),
        "duration_ms": 1,
        "ok": True,
        "status": 200,
        "ip": ip,
        "country": country,
        "reason": None,
        "retry_after": None,
    }


def entry(
    verdict: str,
    checked_at: float = 1_000.0,
    *,
    countries=("DE",),
    reason: str | None = None,
    ip_mismatch: bool = False,
):
    if reason is None:
        reason = {
            "ru": "both_endpoints_ru",
            "non_ru": "both_endpoints_non_ru",
            "unverified": "probe_failed:cloudflare_trace=rate_limited,ipinfo=ok",
        }[verdict]
    probes = {}
    if verdict in {"ru", "non_ru"}:
        cloudflare_country = countries[0]
        ipinfo_country = countries[1] if len(countries) > 1 else countries[0]
        probes = {
            "cloudflare_trace": probe_evidence(
                "cloudflare_trace",
                ev.CLOUDFLARE_TRACE_URL,
                cloudflare_country,
                "8.8.8.8",
                checked_at,
            ),
            "ipinfo": probe_evidence(
                "ipinfo",
                ev.IPINFO_URL,
                ipinfo_country,
                "1.1.1.1" if ip_mismatch else "8.8.8.8",
                checked_at,
            ),
        }
    return ev._entry(
        verdict,
        reason,
        probes,
        checked_at,
        exit_countries=countries,
        ip_mismatch=ip_mismatch,
    )


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, proxy_url, **kwargs):
        self.calls.append((url, proxy_url, kwargs))
        return self.responses[url]


class LabelAndKeyTests(unittest.TestCase):
    def test_country_label(self):
        self.assertEqual(ev._country_from_name("🇷🇺 Moscow"), "RU")
        self.assertEqual(ev._country_from_name("🇳🇱 Amsterdam"), "NL")
        self.assertIsNone(ev._country_from_name("Amsterdam"))
        self.assertIsNone(ev._country_from_name("🇷 Moscow"))
        self.assertIsNone(ev._country_from_name(""))

    def test_key_ignores_only_display_name(self):
        first = proxy("🇳🇱 Amsterdam", password="one")
        renamed = dict(first, name="🇩🇪 Berlin")
        changed = dict(first, password="two")
        self.assertEqual(
            ev.canonical_node_key(first), ev.canonical_node_key(dict(reversed(list(first.items()))))
        )
        self.assertEqual(ev.canonical_node_key(first), ev.canonical_node_key(renamed))
        self.assertNotEqual(ev.canonical_node_key(first), ev.canonical_node_key(changed))

    def test_config_snapshot_ignores_local_work_and_unlabelled_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            write_config(
                path,
                [
                    proxy("🇷🇺 RU node"),
                    proxy("🇳🇱 NL node"),
                    proxy("DIRECT", type="direct", server="127.0.0.1"),
                    proxy("WORK-VPN", type="direct", server="127.0.0.1"),
                    proxy("plain node"),
                    {"name": "missing-name", "type": "socks5"},
                ],
            )
            snapshot = ev.load_config_snapshot(str(path))
            self.assertEqual([c.name for c in snapshot.candidates], ["🇷🇺 RU node", "🇳🇱 NL node"])
            reasons = {node.name: node.reason for node in snapshot.ignored}
            self.assertEqual(reasons["DIRECT"], "local or non-node proxy")
            self.assertEqual(reasons["WORK-VPN"], "local or non-node proxy")
            self.assertEqual(reasons["plain node"], "not country-labelled")

    def test_dialer_proxy_and_local_server_are_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            write_config(
                path,
                [
                    proxy("🇷🇺 RU node", **{"dialer-proxy": "WORK-VPN"}),
                    proxy("🇩🇪 DE node", server="127.0.0.1"),
                    proxy("🇫🇷 FR node", server="localhost"),
                    {"name": "🇺🇸 US node", "server": "proxy.example", "port": 1},
                ],
            )
            snapshot = ev.load_config_snapshot(str(path))
            self.assertEqual(
                snapshot.candidates[0].unsupported_reason, "dialer_proxy_unsupported"
            )
            self.assertEqual(
                snapshot.candidates[1].unsupported_reason, "local_server_unsupported"
            )
            self.assertEqual(
                snapshot.candidates[2].unsupported_reason, "local_server_unsupported"
            )
            self.assertEqual(
                snapshot.candidates[3].unsupported_reason, "type_unsupported"
            )


class AddressAndResponseTests(unittest.TestCase):
    def test_public_ip_validation(self):
        self.assertEqual(ev.normalize_public_ip("8.8.8.8"), "8.8.8.8")
        self.assertEqual(ev.normalize_public_ip("2606:4700:4700::1111"), "2606:4700:4700::1111")
        for value in ("10.0.0.1", "127.0.0.1", "100.64.0.1", "192.0.2.1", "::1", "not-an-ip"):
            with self.assertRaises(ValueError, msg=value):
                ev.normalize_public_ip(value)

    def test_cloudflare_trace_parsing(self):
        self.assertEqual(
            ev.parse_cloudflare_trace("fl=1\nip=8.8.8.8\nloc=DE\nts=1\n"),
            ("8.8.8.8", "DE"),
        )
        with self.assertRaises(ValueError):
            ev.parse_cloudflare_trace("ip=8.8.8.8\n")
        with self.assertRaises(ValueError):
            ev.parse_cloudflare_trace("ip=10.0.0.1\nloc=DE\n")
        with self.assertRaises(ValueError):
            ev.parse_cloudflare_trace("ip=8.8.8.8\nloc=XX\n")

    def test_ipinfo_parsing(self):
        self.assertEqual(
            ev.parse_ipinfo_json('{"ip":"8.8.8.8","country":"NL"}'),
            ("8.8.8.8", "NL"),
        )
        with self.assertRaises(ValueError):
            ev.parse_ipinfo_json('{"ip":"8.8.8.8"}')
        with self.assertRaises(ValueError):
            ev.parse_ipinfo_json("not json")


class ProbeNodeTests(unittest.TestCase):
    def response(self, body: str, status: int = 200, returncode: int = 0) -> ev.CurlResult:
        return ev.CurlResult(returncode, body, "", status)

    def test_both_ru_is_ru(self):
        runner = FakeRunner(
            {
                ev.CLOUDFLARE_TRACE_URL: self.response("ip=8.8.8.8\nloc=RU\n"),
                ev.IPINFO_URL: self.response('{"ip":"8.8.8.8","country":"RU"}'),
            }
        )
        result = ev.probe_node(
            ev.Candidate("🇷🇺 RU", "k" * 64, proxy("🇷🇺 RU"), "RU"),
            "http://127.0.0.1:1",
            runner=runner,
            clock=lambda: 2_000.0,
        )
        self.assertEqual(result["verdict"], "ru")
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_countries"], ["RU"])
        self.assertFalse(result["ip_mismatch"])
        self.assertEqual(runner.calls[0][1], "http://127.0.0.1:1")
        self.assertEqual(runner.calls[0][2]["max_time"], ev.DEFAULT_MAX_TIME_SECONDS)

    def test_foreign_country_difference_is_foreign_and_recorded(self):
        runner = FakeRunner(
            {
                ev.CLOUDFLARE_TRACE_URL: self.response("ip=8.8.8.8\nloc=DE\n"),
                ev.IPINFO_URL: self.response('{"ip":"1.1.1.1","country":"NL"}'),
            }
        )
        result = ev.probe_node(
            ev.Candidate("🇳🇱 NL", "k" * 64, proxy("🇳🇱 NL"), "NL"),
            "http://127.0.0.1:2",
            runner=runner,
            clock=lambda: 2_000.0,
        )
        self.assertEqual(result["verdict"], "non_ru")
        self.assertEqual(result["exit_countries"], ["DE", "NL"])
        self.assertTrue(result["ip_mismatch"])
        self.assertEqual(result["reason"], "both_endpoints_non_ru")
        self.assertEqual(result["probes"]["cloudflare_trace"]["country"], "DE")
        self.assertEqual(result["probes"]["ipinfo"]["country"], "NL")

    def test_endpoint_country_disagreement_is_unverified(self):
        runner = FakeRunner(
            {
                ev.CLOUDFLARE_TRACE_URL: self.response("ip=8.8.8.8\nloc=RU\n"),
                ev.IPINFO_URL: self.response('{"ip":"8.8.8.8","country":"DE"}'),
            }
        )
        result = ev.probe_node(
            ev.Candidate("🇷🇺 RU", "k" * 64, proxy("🇷🇺 RU"), "RU"),
            "http://127.0.0.1:3",
            runner=runner,
            clock=lambda: 2_000.0,
        )
        self.assertEqual(result["verdict"], "unverified")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "endpoint_country_disagreement")

    def test_rate_limit_is_failure_and_not_retried_in_scan(self):
        runner = FakeRunner(
            {
                ev.CLOUDFLARE_TRACE_URL: self.response("rate limited", status=429),
                ev.IPINFO_URL: self.response('{"ip":"8.8.8.8","country":"DE"}'),
            }
        )
        result = ev.probe_node(
            ev.Candidate("🇩🇪 DE", "k" * 64, proxy("🇩🇪 DE"), "DE"),
            "http://127.0.0.1:4",
            runner=runner,
            clock=lambda: 2_000.0,
        )
        self.assertEqual(result["verdict"], "unverified")
        self.assertFalse(result["ok"])
        self.assertIn("rate_limited", result["reason"])
        self.assertEqual(len(runner.calls), 2)

    def test_private_exit_ip_fails_closed(self):
        runner = FakeRunner(
            {
                ev.CLOUDFLARE_TRACE_URL: self.response("ip=10.0.0.1\nloc=DE\n"),
                ev.IPINFO_URL: self.response('{"ip":"8.8.8.8","country":"DE"}'),
            }
        )
        result = ev.probe_node(
            ev.Candidate("🇩🇪 DE", "k" * 64, proxy("🇩🇪 DE"), "DE"),
            "http://127.0.0.1:5",
            runner=runner,
            clock=lambda: 2_000.0,
        )
        self.assertEqual(result["verdict"], "unverified")
        self.assertEqual(result["probes"]["cloudflare_trace"]["reason"], "invalid_response")

    def test_curl_error_is_recorded(self):
        runner = FakeRunner(
            {
                ev.CLOUDFLARE_TRACE_URL: self.response("", status=None, returncode=28),
                ev.IPINFO_URL: self.response('{"ip":"8.8.8.8","country":"DE"}'),
            }
        )
        result = ev.probe_node(
            ev.Candidate("🇩🇪 DE", "k" * 64, proxy("🇩🇪 DE"), "DE"),
            "http://127.0.0.1:6",
            runner=runner,
            clock=lambda: 2_000.0,
        )
        self.assertEqual(result["verdict"], "unverified")
        self.assertEqual(result["probes"]["cloudflare_trace"]["reason"], "timeout")

    def test_run_curl_uses_loopback_proxy_and_no_environment_proxy(self):
        process = mock.Mock(returncode=0)
        process.communicate.return_value = ("ip=8.8.8.8\nloc=DE\n\n200", "")
        with mock.patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://main-proxy:7897", "NO_PROXY": "*"},
            clear=False,
        ):
            with mock.patch.object(
                ev.subprocess, "Popen", return_value=process
            ) as popen:
                result = ev.run_curl(
                    ev.CLOUDFLARE_TRACE_URL,
                    "http://127.0.0.1:34567",
                    curl_cmd="/usr/bin/curl",
                    connect_timeout=5,
                    max_time=10,
                )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.stdout, "ip=8.8.8.8\nloc=DE\n")
        command = popen.call_args.args[0]
        self.assertEqual(command[1], "-q")
        self.assertIn("--proxy", command)
        self.assertEqual(command[command.index("--proxy") + 1], "http://127.0.0.1:34567")
        self.assertEqual(command[command.index("--noproxy") + 1], "")
        self.assertEqual(command[command.index("--max-time") + 1], "10")
        self.assertEqual(command[command.index("--connect-timeout") + 1], "5")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        environment = popen.call_args.kwargs["env"]
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("NO_PROXY", environment)

    def test_run_curl_terminates_active_process_on_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "slow-curl"
            script.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import time
                    time.sleep(30)
                    """
                )
            )
            script.chmod(0o755)
            cancel_event = threading.Event()
            result = {}

            def probe():
                result["value"] = ev.run_curl(
                    ev.CLOUDFLARE_TRACE_URL,
                    "http://127.0.0.1:1",
                    curl_cmd=str(script),
                    cancel_event=cancel_event,
                )

            thread = threading.Thread(target=probe)
            thread.start()
            time.sleep(0.3)
            cancel_event.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["value"].returncode, 130)


class TtlAndClassificationTests(unittest.TestCase):
    def test_success_and_failure_ttls(self):
        success = entry("non_ru", 1_000.0)
        failure = entry("unverified", 1_000.0)
        self.assertIsNone(ev.entry_expiry_reason(success, 1_000.0 + 86_399))
        self.assertEqual(
            ev.entry_expiry_reason(success, 1_000.0 + 86_400), "cache_expired"
        )
        self.assertIsNone(ev.entry_expiry_reason(failure, 1_000.0 + 3_599))
        self.assertEqual(
            ev.entry_expiry_reason(failure, 1_000.0 + 3_600), "cache_expired"
        )
        self.assertEqual(
            ev.entry_expiry_reason(success, 900.0), "cache_timestamp_invalid"
        )

    def test_classify_uses_cached_entries_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            ru = proxy("🇷🇺 RU 1", password="ru-secret")
            nl = proxy("🇳🇱 NL 1", password="nl-secret")
            de_ru = proxy("🇩🇪 DE RU exit", password="de-secret")
            ru_de = proxy("🇷🇺 RU foreign exit", password="ru2-secret")
            fr = proxy("🇫🇷 FR unprobed", password="fr-secret")
            write_config(
                config_path,
                [
                    ru,
                    nl,
                    de_ru,
                    ru_de,
                    fr,
                    proxy("DIRECT", type="direct", server="127.0.0.1"),
                    proxy("WORK-VPN", type="direct", server="127.0.0.1"),
                    proxy("plain"),
                ],
            )
            entries = {
                ev.canonical_node_key(ru): entry("ru", countries=("RU",)),
                ev.canonical_node_key(nl): entry("non_ru", countries=("DE", "NL")),
                ev.canonical_node_key(de_ru): entry("ru", countries=("RU",)),
                ev.canonical_node_key(ru_de): entry("non_ru", countries=("DE",)),
            }
            write_cache(cache_path, 7, entries)
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertEqual(report["cache_generation"], 7)
            self.assertEqual(report["groups"]["RU-AUTO"], ["🇷🇺 RU 1"])
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], ["🇳🇱 NL 1"])
            by_name = {node["name"]: node for node in report["nodes"]}
            self.assertEqual(by_name["🇩🇪 DE RU exit"]["classification"], "excluded")
            self.assertEqual(by_name["🇩🇪 DE RU exit"]["reason"], "foreign_labelled_ru_exit")
            self.assertEqual(by_name["🇷🇺 RU foreign exit"]["classification"], "excluded")
            self.assertEqual(by_name["🇷🇺 RU foreign exit"]["reason"], "ru_labelled_non_ru_exit")
            self.assertEqual(by_name["🇫🇷 FR unprobed"]["classification"], "unverified")
            self.assertEqual(by_name["🇫🇷 FR unprobed"]["reason"], "not_probed")
            self.assertEqual(by_name["🇳🇱 NL 1"]["exit_countries"], ["DE", "NL"])
            self.assertEqual(report["counts"]["russian"], 1)
            self.assertEqual(report["counts"]["foreign"], 1)
            self.assertEqual(report["counts"]["excluded"], 2)
            self.assertEqual(report["counts"]["unverified"], 1)
            self.assertEqual(report["counts"]["ignored"], 3)

    def test_expired_entry_revokes_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇳🇱 NL")
            write_config(config_path, [node])
            write_cache(
                cache_path,
                1,
                {ev.canonical_node_key(node): entry("non_ru", 1_000.0)},
            )
            report = ev.classify(
                str(config_path), str(cache_path), clock=lambda: 1_000.0 + 86_400
            )
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], [])
            self.assertEqual(report["nodes"][0]["classification"], "unverified")
            self.assertEqual(report["nodes"][0]["reason"], "cache_expired")
            self.assertTrue(report["nodes"][0]["stale"])

    def test_rename_reuses_entry_and_detail_change_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            original = proxy("🇳🇱 NL old")
            renamed = dict(original, name="🇳🇱 NL renamed")
            changed = dict(original, name="🇳🇱 NL changed", password="different")
            write_config(config_path, [renamed])
            write_cache(
                cache_path,
                2,
                {ev.canonical_node_key(original): entry("non_ru", 1_000.0)},
            )
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], ["🇳🇱 NL renamed"])
            write_config(config_path, [changed])
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], [])
            self.assertEqual(report["nodes"][0]["reason"], "not_probed")


class CacheTests(unittest.TestCase):
    def test_corrupt_and_schema_mismatch_are_empty_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            for payload in ("{not json", json.dumps({"schema_version": 99, "entries": {}})):
                cache_path.write_text(payload)
                cache_path.chmod(0o600)
                report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
                self.assertTrue(report["cache_warnings"])
                self.assertEqual(report["cache_generation"], 0)
                self.assertEqual(report["nodes"][0]["classification"], "unverified")

    def test_invalid_entry_is_empty_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            write_cache(cache_path, 1, {"bad-key": entry("non_ru")})
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertTrue(report["cache_warnings"])
            self.assertEqual(report["cache_generation"], 0)

    def test_wrong_type_verdict_is_empty_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            key = ev.canonical_node_key(node)
            for verdict in ([], {}):
                bad = entry("non_ru")
                bad["verdict"] = verdict
                write_cache(cache_path, 1, {key: bad})
                output = StringIO()
                errors = StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = ev.main(
                        [
                            "classify",
                            "--config",
                            str(config_path),
                            "--cache",
                            str(cache_path),
                            "--json",
                        ]
                    )
                self.assertEqual(code, 0)
                document = json.loads(output.getvalue())
                self.assertEqual(document["groups"]["FOREIGN-AUTO"], [])
                self.assertIn("warning:", errors.getvalue())

    def test_unrepresentable_timestamp_is_empty_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            bad = entry("non_ru")
            bad["checked_at"] = 1e300
            write_cache(cache_path, 1, {ev.canonical_node_key(node): bad})
            output = StringIO()
            errors = StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = ev.main(
                    [
                        "classify",
                        "--config",
                        str(config_path),
                        "--cache",
                        str(cache_path),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            document = json.loads(output.getvalue())
            self.assertEqual(document["groups"]["FOREIGN-AUTO"], [])
            self.assertIn("warning:", errors.getvalue())

    def test_escaped_surrogates_are_empty_cache_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            bad_entry = entry("non_ru")
            bad_entry["reason"] = "\ud800"
            payloads = (
                '{"schema_version":1,"generation":1,"updated_at":"\\ud800","entries":{}}',
                json.dumps(
                    {
                        "schema_version": ev.SCHEMA_VERSION,
                        "generation": 1,
                        "updated_at": "2026-01-01T00:00:00Z",
                        "entries": {ev.canonical_node_key(node): bad_entry},
                    },
                    ensure_ascii=True,
                ),
            )
            for payload in payloads:
                with self.subTest(payload=payload[:40]):
                    cache_path.write_text(payload, encoding="ascii")
                    cache_path.chmod(0o600)
                    output = StringIO()
                    errors = StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        code = ev.main(
                            [
                                "classify",
                                "--config",
                                str(config_path),
                                "--cache",
                                str(cache_path),
                                "--json",
                            ]
                        )
                    self.assertEqual(code, 0)
                    output.getvalue().encode("utf-8")
                    document = json.loads(output.getvalue())
                    self.assertEqual(document["cache_generation"], 0)
                    self.assertEqual(document["groups"]["RU-AUTO"], [])
                    self.assertEqual(document["groups"]["FOREIGN-AUTO"], [])
                    self.assertEqual(
                        document["nodes"][0]["classification"], "unverified"
                    )
                    self.assertIn("non-UTF-8", errors.getvalue())

    def test_cache_size_budget_matches_reader_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            state = ev.CacheState(1, {}, "2026-01-01T00:00:00Z")
            payload = {
                "schema_version": ev.SCHEMA_VERSION,
                "generation": 1,
                "updated_at": ev.iso_time(1_000.0),
                "entries": {},
            }
            size = len(
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                    + "\n"
                ).encode("utf-8")
            )
            with mock.patch.object(ev, "MAX_CACHE_BYTES", size):
                ev._write_cache(str(cache_path), state, 1_000.0)
                self.assertEqual(ev.read_cache(str(cache_path)).generation, 1)
            before = cache_path.read_bytes()
            with mock.patch.object(ev, "MAX_CACHE_BYTES", size - 1):
                with self.assertRaises(ev.VerifierError):
                    ev._write_cache(str(cache_path), state, 2_000.0)
            self.assertEqual(cache_path.read_bytes(), before)
            self.assertEqual(list(Path(tmp).glob(".exit-cache.*")), [])

    def test_success_entry_without_evidence_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            write_cache(
                cache_path,
                1,
                {
                    ev.canonical_node_key(node): {
                        "verdict": "non_ru",
                        "reason": "both_endpoints_non_ru",
                        "ok": True,
                        "checked_at": 1_000.0,
                        "expires_at": 87_400.0,
                        "ttl_seconds": 86_400,
                        "probes": {},
                        "exit_countries": [],
                        "ip_mismatch": False,
                    }
                },
            )
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertTrue(report["cache_warnings"])
            self.assertEqual(report["nodes"][0]["classification"], "unverified")

    def test_permissive_cache_permissions_are_rejected_and_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            write_cache(
                cache_path,
                1,
                {ev.canonical_node_key(node): entry("non_ru", 1_000.0)},
            )
            cache_path.chmod(0o644)
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertTrue(report["cache_warnings"])
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], [])
            ev.refresh(
                str(config_path),
                str(cache_path),
                probe_fn=lambda candidate, now: entry("non_ru", now),
                clock=lambda: 1_000.0,
            )
            self.assertEqual(stat.S_IMODE(cache_path.stat().st_mode), 0o600)

    def test_empty_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇷🇺 RU")
            write_config(config_path, [node])
            write_cache(cache_path, 1, {})
            report = ev.classify(str(config_path), str(cache_path), clock=lambda: 1_000.0)
            self.assertEqual(report["groups"], {"RU-AUTO": [], "FOREIGN-AUTO": []})
            self.assertEqual(report["nodes"][0]["eligible_group"], None)

    def test_atomic_write_mode_and_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            ev.reset_cache(str(cache_path), clock=lambda: 1_000.0)
            mode = stat.S_IMODE(cache_path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertEqual(list(Path(tmp).glob(".exit-cache.*")), [])
            self.assertEqual(json.loads(cache_path.read_text())["generation"], 1)

    def test_failed_atomic_write_preserves_previous_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            ev.reset_cache(str(cache_path), clock=lambda: 1_000.0)
            before = cache_path.read_bytes()
            with mock.patch.object(ev.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    ev._write_cache(
                        str(cache_path),
                        ev.CacheState(1, {}, "2026-01-01T00:00:00Z"),
                        2_000.0,
                    )
            self.assertEqual(cache_path.read_bytes(), before)
            self.assertEqual(list(Path(tmp).glob(".exit-cache.*")), [])

    def test_cache_never_contains_display_name_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE", password="do-not-store-this")
            write_config(config_path, [node])
            ev.refresh(
                str(config_path),
                str(cache_path),
                probe_fn=lambda candidate, now: entry("non_ru", now),
                clock=lambda: 1_000.0,
            )
            text = cache_path.read_text()
            self.assertNotIn("do-not-store-this", text)
            self.assertNotIn("🇩🇪 DE", text)


class RefreshTests(unittest.TestCase):
    def test_fresh_entry_is_reused_and_removed_node_is_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            keep = proxy("🇩🇪 DE keep", password="keep-secret")
            removed = proxy("🇫🇷 FR removed", password="removed-secret")
            write_config(config_path, [keep])
            write_cache(
                cache_path,
                3,
                {
                    ev.canonical_node_key(keep): entry("non_ru", 1_000.0),
                    ev.canonical_node_key(removed): entry("non_ru", 1_000.0),
                },
            )

            def must_not_probe(candidate, now):
                raise AssertionError("fresh cache was not reused")

            report = ev.refresh(
                str(config_path),
                str(cache_path),
                probe_fn=must_not_probe,
                clock=lambda: 1_000.0 + 10,
            )
            self.assertEqual(report["scan"]["reused"], 1)
            self.assertEqual(report["scan"]["probed"], 0)
            self.assertEqual(report["scan"]["pruned"], 1)
            stored = json.loads(cache_path.read_text())
            self.assertEqual(set(stored["entries"]), {ev.canonical_node_key(keep)})

    def test_failed_entry_is_reprobed_after_one_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            write_cache(
                cache_path,
                1,
                {ev.canonical_node_key(node): entry("unverified", 1_000.0)},
            )
            calls = []

            def probe(candidate, now):
                calls.append((candidate.name, now))
                return entry("non_ru", now)

            report = ev.refresh(
                str(config_path),
                str(cache_path),
                probe_fn=probe,
                clock=lambda: 1_000.0 + 3_600,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(report["scan"]["probed"], 1)
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], ["🇩🇪 DE"])

    def test_force_reprobes_fresh_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            write_cache(
                cache_path,
                1,
                {ev.canonical_node_key(node): entry("non_ru", 1_000.0)},
            )
            calls = []

            def probe(candidate, now):
                calls.append(candidate.name)
                return entry("ru", now, countries=("RU",))

            report = ev.refresh(
                str(config_path),
                str(cache_path),
                force=True,
                probe_fn=probe,
                clock=lambda: 1_000.0 + 10,
            )
            self.assertEqual(calls, ["🇩🇪 DE"])
            self.assertEqual(report["nodes"][0]["classification"], "excluded")

    def test_unsupported_node_is_unverified_without_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE", **{"dialer-proxy": "WORK-VPN"})
            write_config(config_path, [node])
            report = ev.refresh(
                str(config_path),
                str(cache_path),
                probe_fn=lambda candidate, now: self.fail("unsupported node was probed"),
                clock=lambda: 1_000.0,
            )
            self.assertEqual(report["nodes"][0]["classification"], "unverified")
            self.assertEqual(report["nodes"][0]["reason"], "dialer_proxy_unsupported")

    def test_reset_during_scan_cannot_restore_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])

            def reset_then_return(candidate, now):
                ev.reset_cache(str(cache_path), clock=lambda: now)
                return entry("non_ru", now)

            with self.assertRaises(ev.StaleScanError):
                ev.refresh(
                    str(config_path),
                    str(cache_path),
                    probe_fn=reset_then_return,
                    clock=lambda: 1_000.0,
                )
            stored = json.loads(cache_path.read_text())
            self.assertEqual(stored["generation"], 1)
            self.assertEqual(stored["entries"], {})

    def test_config_change_during_scan_cannot_be_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            write_config(config_path, [proxy("🇩🇪 DE")], rules=["MATCH,DIRECT"])

            def mutate_config(candidate, now):
                write_config(config_path, [proxy("🇩🇪 DE changed", password="new")])
                return entry("non_ru", now)

            with self.assertRaises(ev.StaleScanError):
                ev.refresh(
                    str(config_path),
                    str(cache_path),
                    probe_fn=mutate_config,
                    clock=lambda: 1_000.0,
                )
            self.assertFalse(cache_path.exists())

    def test_oversized_refresh_keeps_previous_cache_and_can_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            nodes = [proxy(f"🇩🇪 DE {index}", port=1_000 + index) for index in range(12)]
            write_config(config_path, nodes)
            with mock.patch.object(ev, "MAX_CACHE_BYTES", 2_048):
                ev.reset_cache(str(cache_path), clock=lambda: 1_000.0)
                before = cache_path.read_bytes()
                with self.assertRaises(ev.VerifierError):
                    ev.refresh(
                        str(config_path),
                        str(cache_path),
                        probe_fn=lambda candidate, now: entry("non_ru", now),
                        clock=lambda: 1_000.0,
                    )
                self.assertEqual(cache_path.read_bytes(), before)
                self.assertEqual(list(Path(tmp).glob(".exit-cache.*")), [])
                report = ev.classify(
                    str(config_path), str(cache_path), clock=lambda: 1_000.0
                )
                self.assertEqual(report["groups"]["FOREIGN-AUTO"], [])
            with mock.patch.object(ev, "MAX_CACHE_BYTES", 1024 * 1024):
                report = ev.refresh(
                    str(config_path),
                    str(cache_path),
                    probe_fn=lambda candidate, now: entry("non_ru", now),
                    clock=lambda: 1_000.0,
                )
                self.assertGreater(len(report["groups"]["FOREIGN-AUTO"]), 0)
                again = ev.classify(
                    str(config_path), str(cache_path), clock=lambda: 1_000.0
                )
                self.assertEqual(
                    again["groups"]["FOREIGN-AUTO"],
                    report["groups"]["FOREIGN-AUTO"],
                )

    def test_unrelated_rule_change_does_not_discard_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            write_config(config_path, [proxy("🇩🇪 DE")], rules=["MATCH,DIRECT"])

            def mutate_rules(candidate, now):
                write_config(config_path, [proxy("🇩🇪 DE")], rules=["MATCH,REJECT"])
                return entry("non_ru", now)

            report = ev.refresh(
                str(config_path),
                str(cache_path),
                probe_fn=mutate_rules,
                clock=lambda: 1_000.0,
            )
            self.assertEqual(report["groups"]["FOREIGN-AUTO"], ["🇩🇪 DE"])

    def test_overlapping_scan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            write_config(config_path, [proxy("🇩🇪 DE")])
            with ev._scan_lock(str(cache_path)):
                with self.assertRaises(ev.ScanBusyError):
                    ev.refresh(
                        str(config_path),
                        str(cache_path),
                        probe_fn=lambda candidate, now: entry("non_ru", now),
                        clock=lambda: 1_000.0,
                    )


class ProbeInfrastructureTests(unittest.TestCase):
    def test_build_probe_config_is_tun_less_and_pinned(self):
        candidate = ev.Candidate(
            "🇩🇪 DE",
            "k" * 64,
            proxy(
                "🇩🇪 DE",
                **{"dialer-proxy": "WORK-VPN", "routing-mark": 123, "interface-name": "old0"}
            ),
            "DE",
        )
        config, proxy_urls = ev.build_probe_config([candidate], "wlan0", [34567])
        self.assertEqual(config["tun"], {"enable": False})
        self.assertEqual(config["mixed-port"], 0)
        self.assertEqual(config["interface-name"], "wlan0")
        self.assertEqual(config["listeners"][0]["proxy"], "🇩🇪 DE")
        self.assertEqual(config["listeners"][0]["listen"], "127.0.0.1")
        self.assertFalse(config["listeners"][0]["udp"])
        self.assertEqual(config["proxies"][0]["interface-name"], "wlan0")
        self.assertNotIn("dialer-proxy", config["proxies"][0])
        self.assertNotIn("routing-mark", config["proxies"][0])
        self.assertTrue(all(s.endswith("#wlan0") for s in config["dns"]["nameserver"]))
        self.assertEqual(
            config["dns"]["proxy-server-nameserver"], config["dns"]["nameserver"]
        )
        self.assertEqual(proxy_urls[candidate.key], "http://127.0.0.1:34567")
        self.assertNotIn("external-controller", config)

    def test_detect_uplink_skips_tunnel_and_fails_closed(self):
        routes = json.dumps(
            [
                {"dev": "Mihomo", "metric": 10},
                {"dev": "mdr-vpn", "metric": 20},
                {"dev": "wlan0", "metric": 600},
            ]
        )
        self.assertEqual(
            ev.detect_uplink(
                runner=lambda command: routes, physical=lambda device: device == "wlan0"
            ),
            "wlan0",
        )
        with self.assertRaises(ev.VerifierError):
            ev.detect_uplink(
                runner=lambda command: routes, physical=lambda device: False
            )

    def test_detect_uplink_picks_lowest_physical_metric(self):
        routes = json.dumps(
            [
                {"dev": "wlan0", "metric": 600},
                {"dev": "enp0s31f6", "metric": 100},
            ]
        )
        self.assertEqual(
            ev.detect_uplink(runner=lambda command: routes, physical=lambda device: True),
            "enp0s31f6",
        )

    def test_probe_session_cleans_up_process_and_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            candidate = proxy("🇩🇪 DE")
            write_config(config_path, [candidate])
            snapshot = ev.load_config_snapshot(str(config_path))
            ports = ev.allocate_loopback_ports(1)
            config, proxy_urls = ev.build_probe_config(snapshot.candidates, "wlan0", ports)
            script = Path(tmp) / "fake-mihomo"
            script.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import socket, time
                    listener = socket.socket()
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind(("127.0.0.1", {ports[0]}))
                    listener.listen()
                    while True:
                        connection, _ = listener.accept()
                        connection.close()
                    """
                )
            )
            script.chmod(0o755)
            session = ev.ProbeSession(str(script), config, proxy_urls)
            process = None
            with session:
                process = session.process
                self.assertIsNotNone(session.directory)
                self.assertTrue(os.path.isdir(session.directory))
                self.assertIsNone(session.process.poll())
                self.assertEqual(
                    stat.S_IMODE(Path(session.directory).stat().st_mode), 0o700
                )
                self.assertEqual(
                    stat.S_IMODE(
                        Path(session.directory, "config.yaml").stat().st_mode
                    ),
                    0o600,
                )
            self.assertIsNone(session.directory)
            self.assertIsNotNone(process)
            self.assertIsNotNone(process.poll())

    def test_cancellation_cancels_queued_probes_before_session_close(self):
        candidates = [
            ev.Candidate(
                f"🇩🇪 DE {index}",
                f"{index:064x}",
                proxy(f"🇩🇪 DE {index}", port=2_000 + index),
                "DE",
            )
            for index in range(6)
        ]
        events = []
        captured = {}

        class FakeSession:
            def __init__(self, mihomo_cmd, config, proxy_urls):
                self.proxy_urls = dict(proxy_urls)

            def start(self):
                events.append("start")

            def close(self):
                events.append("close")

        class Cancelled(KeyboardInterrupt):
            pass

        def cancel_probe(candidate, proxy_url, **kwargs):
            captured.setdefault("cancel_event", kwargs.get("cancel_event"))
            events.append(("probe", candidate.name))
            raise Cancelled()

        with mock.patch.object(ev, "ProbeSession", FakeSession), mock.patch.object(
            ev, "probe_node", cancel_probe
        ):
            with self.assertRaises(Cancelled):
                ev.probe_nodes(candidates, "wlan0", workers=1)
        self.assertEqual(len([event for event in events if isinstance(event, tuple)]), 1)
        self.assertEqual(events[-1], "close")
        self.assertTrue(captured["cancel_event"].is_set())

    def test_probe_session_cleans_up_after_start_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake-mihomo"
            script.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(7)\n")
            script.chmod(0o755)
            config = {
                "listeners": [{"port": ev.allocate_loopback_ports(1)[0]}],
            }
            session = ev.ProbeSession(str(script), config, {}, startup_timeout=0.5)
            with self.assertRaises(ev.VerifierError):
                with session:
                    pass
            self.assertIsNone(session.directory)


class CliTests(unittest.TestCase):
    def test_classify_json_and_reset_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            cache_path = Path(tmp) / "cache.json"
            node = proxy("🇩🇪 DE")
            write_config(config_path, [node])
            write_cache(
                cache_path,
                1,
                {ev.canonical_node_key(node): entry("non_ru", time.time())},
            )
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(StringIO()):
                code = ev.main(
                    [
                        "classify",
                        "--config",
                        str(config_path),
                        "--cache",
                        str(cache_path),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            document = json.loads(output.getvalue())
            self.assertEqual(document["groups"]["FOREIGN-AUTO"], ["🇩🇪 DE"])
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(StringIO()):
                code = ev.main(["reset", "--cache", str(cache_path)])
            self.assertEqual(code, 0)
            self.assertIn("generation 2", output.getvalue())


if __name__ == "__main__":
    unittest.main()
