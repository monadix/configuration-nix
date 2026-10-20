#!/usr/bin/env python3
"""Cached exit-country verifier for Mihomo subscription nodes.

The verifier runs one temporary, TUN-less Mihomo instance.  Every node is
exposed through a loopback listener pinned to that node, so probes never pass
through the machine's active Mihomo instance.  The temporary instance binds
its outbound sockets and DNS queries to the physical uplink interface.

Only probe evidence and a canonical connection-config hash are cached.  Proxy
credentials are never written to the cache or printed.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    import yaml
except ImportError:  # pragma: no cover - packaging error, reported by CLI
    yaml = None

SCHEMA_VERSION = 1
SUCCESS_TTL_SECONDS = 24 * 60 * 60
FAILURE_TTL_SECONDS = 60 * 60
DEFAULT_WORKERS = 4
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_MAX_TIME_SECONDS = 10
DEFAULT_STARTUP_TIMEOUT_SECONDS = 10
MAX_CACHE_BYTES = 1024 * 1024
MAX_PROBE_BODY_BYTES = 64 * 1024

CLOUDFLARE_TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
IPINFO_URL = "https://ipinfo.io/json"
ENDPOINT_NAMES = ("cloudflare_trace", "ipinfo")

LOCAL_PROXY_NAMES = {"DIRECT", "REJECT", "WORK-VPN"}
NON_COUNTRY_CODES = {"XX", "T1", "A1", "A2", "O1"}
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
REGIONAL_INDICATOR_BASE = 0x1F1E6


class VerifierError(RuntimeError):
    """Operational or configuration error; the cache is left untouched."""

    exit_code = 2


class ScanBusyError(VerifierError):
    """Another refresh is already running."""

    exit_code = 3


class StaleScanError(VerifierError):
    """The cache or configuration changed while a scan was running."""

    exit_code = 3


@dataclass(frozen=True)
class Candidate:
    name: str
    key: str
    proxy: Mapping[str, Any]
    original_country: str
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class IgnoredNode:
    name: str
    reason: str


@dataclass(frozen=True)
class ConfigSnapshot:
    node_fingerprint: str
    candidates: Sequence[Candidate]
    ignored: Sequence[IgnoredNode]


@dataclass
class CacheState:
    generation: int
    entries: dict[str, dict[str, Any]]
    updated_at: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CurlResult:
    returncode: int
    stdout: str
    stderr: str
    status: int | None


def iso_time(epoch: float) -> str:
    return (
        datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _timestamp_is_representable(value: Any, *, extra: float = 0.0) -> bool:
    """Accept only timestamps that can be formatted and compared safely."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return False
    if not math.isfinite(seconds) or seconds < 0:
        return False
    try:
        datetime.fromtimestamp(seconds + extra, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def _ensure_parent_exists(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise VerifierError(f"cache directory does not exist: {parent}")
    return parent


def _lock_path(path: str, suffix: str) -> str:
    return os.path.abspath(path) + suffix


@contextmanager
def _locked(path: str, *, exclusive: bool, blocking: bool = True):
    parent = _ensure_parent_exists(path)
    lock_path = os.path.join(parent, os.path.basename(path))
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ScanBusyError("another refresh is already running") from exc
            raise
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _cache_lock(cache_path: str, *, exclusive: bool):
    with _locked(_lock_path(cache_path, ".lock"), exclusive=exclusive):
        yield


@contextmanager
def _scan_lock(cache_path: str):
    with _locked(_lock_path(cache_path, ".scan.lock"), exclusive=True, blocking=False):
        yield


def _owner_check(path: str, *, require_private: bool = False) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if st.st_uid != os.geteuid():
        raise VerifierError(f"cache file is not owned by the current user: {path}")
    if require_private and st.st_mode & 0o077:
        raise VerifierError(f"cache file permissions are not private: {path}")


def _valid_probe(probe: Any) -> bool:
    if not isinstance(probe, dict):
        return False
    if not isinstance(probe.get("ok"), bool):
        return False
    if probe.get("reason") is not None and not isinstance(probe.get("reason"), str):
        return False
    if not isinstance(probe.get("checked_at"), str):
        return False
    if probe["ok"]:
        if not isinstance(probe.get("ip"), str) or not isinstance(probe.get("country"), str):
            return False
        try:
            normalize_public_ip(probe["ip"])
            normalize_country(probe["country"])
        except ValueError:
            return False
    return True


def _valid_entry(key: Any, entry: Any) -> bool:
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        return False
    if not isinstance(entry, dict):
        return False
    verdict = entry.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"ru", "non_ru", "unverified"}:
        return False
    if not isinstance(entry.get("ok"), bool) or entry["ok"] != (verdict != "unverified"):
        return False
    checked_at = entry.get("checked_at")
    if not _timestamp_is_representable(checked_at, extra=SUCCESS_TTL_SECONDS):
        return False
    expires_at = entry.get("expires_at")
    if expires_at is not None and not _timestamp_is_representable(expires_at):
        return False
    ttl_seconds = entry.get("ttl_seconds")
    if ttl_seconds is not None:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(float(ttl_seconds))
            or float(ttl_seconds) < 0
        ):
            return False
    if not isinstance(entry.get("reason"), str):
        return False
    probes = entry.get("probes")
    if not isinstance(probes, dict):
        return False
    for name, probe in probes.items():
        if name not in ENDPOINT_NAMES or not _valid_probe(probe):
            return False
    if entry.get("ip_mismatch") is not None and not isinstance(entry["ip_mismatch"], bool):
        return False
    countries = entry.get("exit_countries")
    if countries is not None:
        if not isinstance(countries, list) or not all(
            isinstance(c, str) and COUNTRY_RE.fullmatch(c) for c in countries
        ):
            return False
    if verdict in {"ru", "non_ru"}:
        if set(probes) != set(ENDPOINT_NAMES) or not all(
            probe.get("ok") for probe in probes.values()
        ):
            return False
        observed = {probe["country"] for probe in probes.values()}
        if verdict == "ru" and observed != {"RU"}:
            return False
        if verdict == "non_ru" and "RU" in observed:
            return False
        if countries is not None and set(countries) != observed:
            return False
        if not isinstance(entry.get("ip_mismatch"), bool):
            return False
    return True


def _strings_are_utf8_encodable(document: Any) -> bool:
    """Reject string data that cannot be emitted to a UTF-8 pipe.

    json.loads accepts escaped lone surrogates such as \\ud800, which later
    fail when written with ensure_ascii=False. Check every parsed string,
    including keys and nested retained fields, instead of only known fields.
    """
    try:
        json.dumps(document, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return False
    return True


def _load_cache_file(path: str) -> CacheState:
    """Read and validate the cache without taking a lock."""
    warnings: list[str] = []
    if not os.path.lexists(path):
        return CacheState(0, {}, None, warnings)
    try:
        _owner_check(path, require_private=True)
    except VerifierError as exc:
        return CacheState(0, {}, None, [str(exc)])
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_CACHE_BYTES + 1)
    except OSError as exc:
        return CacheState(0, {}, None, [f"cannot read cache: {exc.strerror or exc}"])
    if len(raw) > MAX_CACHE_BYTES:
        return CacheState(0, {}, None, ["cache is larger than the supported size"])
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return CacheState(0, {}, None, ["cache is malformed JSON; treating it as empty"])
    if not isinstance(document, dict):
        return CacheState(0, {}, None, ["cache root is not an object; treating it as empty"])
    if not _strings_are_utf8_encodable(document):
        return CacheState(
            0,
            {},
            None,
            ["cache contains non-UTF-8 string data; treating it as empty"],
        )
    if document.get("schema_version") != SCHEMA_VERSION:
        return CacheState(
            0,
            {},
            None,
            [f"cache schema mismatch; treating it as empty (expected {SCHEMA_VERSION})"],
        )
    generation = document.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        return CacheState(0, {}, None, ["cache generation is invalid; treating it as empty"])
    updated_at = document.get("updated_at")
    if not isinstance(updated_at, str):
        return CacheState(0, {}, None, ["cache updated_at is invalid; treating it as empty"])
    entries = document.get("entries")
    if not isinstance(entries, dict):
        return CacheState(0, {}, None, ["cache entries are invalid; treating it as empty"])
    try:
        for key, entry in entries.items():
            if not _valid_entry(key, entry):
                return CacheState(
                    0,
                    {},
                    None,
                    ["cache contains an invalid entry; treating it as empty"],
                )
    except Exception:
        # Cache data is untrusted input; validation failures must fail closed.
        return CacheState(
            0,
            {},
            None,
            ["cache contains malformed entry data; treating it as empty"],
        )
    return CacheState(generation, entries, updated_at, warnings)


def read_cache(cache_path: str) -> CacheState:
    _ensure_parent_exists(cache_path)
    with _cache_lock(cache_path, exclusive=False):
        return _load_cache_file(cache_path)


def _atomic_write_json(path: str, payload: Mapping[str, Any]) -> None:
    parent = _ensure_parent_exists(path)
    data = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if len(data) > MAX_CACHE_BYTES:
        raise VerifierError("cache is larger than the supported size")
    fd, temporary = tempfile.mkstemp(prefix=".exit-cache.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_cache(cache_path: str, state: CacheState, now: float) -> None:
    _owner_check(cache_path)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generation": state.generation,
        "updated_at": iso_time(now),
        "entries": state.entries,
    }
    _atomic_write_json(cache_path, payload)


def reset_cache(cache_path: str, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    _ensure_parent_exists(cache_path)
    with _cache_lock(cache_path, exclusive=True):
        current = _load_cache_file(cache_path)
        generation = current.generation + 1
        now = clock()
        _write_cache(cache_path, CacheState(generation, {}, iso_time(now)), now)
    return {
        "schema_version": SCHEMA_VERSION,
        "generation": generation,
        "entries": 0,
        "warnings": current.warnings,
    }


def _country_from_name(name: str) -> str | None:
    if len(name) < 2:
        return None
    points = (ord(name[0]), ord(name[1]))
    if not all(REGIONAL_INDICATOR_BASE <= p <= REGIONAL_INDICATOR_BASE + 25 for p in points):
        return None
    code = "".join(chr(p - REGIONAL_INDICATOR_BASE + ord("A")) for p in points)
    return code if COUNTRY_RE.fullmatch(code) else None


def canonical_node_key(proxy: Mapping[str, Any]) -> str:
    """Hash the connection definition, excluding only its display name."""
    body = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _node_unsupported_reason(proxy: Mapping[str, Any]) -> str | None:
    if not isinstance(proxy.get("type"), str) or not proxy.get("type"):
        return "type_unsupported"
    if proxy.get("dialer-proxy"):
        return "dialer_proxy_unsupported"
    server = proxy.get("server")
    if not isinstance(server, str):
        return "server_unsupported"
    host = server.strip().lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain", "ip6-localhost", "0.0.0.0", "::"}:
        return "local_server_unsupported"
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if address.is_loopback or address.is_unspecified:
        return "local_server_unsupported"
    return None


def _node_fingerprint(candidates: Sequence[Candidate]) -> str:
    hasher = hashlib.sha256()
    for key, name in sorted((candidate.key, candidate.name) for candidate in candidates):
        hasher.update(key.encode("ascii"))
        hasher.update(b"\0")
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def load_config_snapshot(config_path: str) -> ConfigSnapshot:
    if yaml is None:
        raise VerifierError("PyYAML is required but is not available")
    try:
        raw = Path(config_path).read_bytes()
    except OSError as exc:
        raise VerifierError(f"cannot read configuration: {exc.strerror or exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except Exception:
        # PyYAML errors can embed configuration lines, so do not chain the cause.
        raise VerifierError("configuration is not valid YAML") from None
    if not isinstance(document, dict):
        raise VerifierError("configuration root is not an object")
    proxies = document.get("proxies") or []
    if not isinstance(proxies, list):
        raise VerifierError("configuration proxies is not a list")

    candidates: list[Candidate] = []
    ignored: list[IgnoredNode] = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            ignored.append(IgnoredNode("<invalid>", "proxy entry is not an object"))
            continue
        name = proxy.get("name")
        if not isinstance(name, str) or not name:
            ignored.append(IgnoredNode("<unnamed>", "proxy has no usable name"))
            continue
        proxy_type = proxy.get("type")
        if proxy_type in {"direct", "dns", "rematch"} or name in LOCAL_PROXY_NAMES:
            ignored.append(IgnoredNode(name, "local or non-node proxy"))
            continue
        country = _country_from_name(name)
        if country is None:
            ignored.append(IgnoredNode(name, "not country-labelled"))
            continue
        try:
            key = canonical_node_key(proxy)
        except (TypeError, ValueError):
            ignored.append(IgnoredNode(name, "proxy configuration is not serializable"))
            continue
        unsupported = _node_unsupported_reason(proxy)
        candidates.append(
            Candidate(
                name=name,
                key=key,
                proxy=proxy,
                original_country=country,
                unsupported_reason=unsupported,
            )
        )
    return ConfigSnapshot(_node_fingerprint(candidates), candidates, ignored)


def normalize_public_ip(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("IP is not a string")
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ValueError("IP is invalid") from exc
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("IP is not a public address")
    return str(address)


def normalize_country(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("country is not a string")
    country = value.strip().upper()
    if not COUNTRY_RE.fullmatch(country) or country in NON_COUNTRY_CODES:
        raise ValueError("country code is invalid")
    return country


def parse_cloudflare_trace(body: str) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in body.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    ip = values.get("ip")
    location = values.get("loc")
    if not ip or not location:
        raise ValueError("cloudflare trace is missing ip or loc")
    return normalize_public_ip(ip), normalize_country(location)


def parse_ipinfo_json(body: str) -> tuple[str, str]:
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("ipinfo response is not JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("ipinfo response is not an object")
    return normalize_public_ip(document.get("ip")), normalize_country(
        document.get("country")
    )


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def run_curl(
    url: str,
    proxy_url: str,
    *,
    curl_cmd: str = "curl",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    max_time: int = DEFAULT_MAX_TIME_SECONDS,
    cancel_event: threading.Event | None = None,
) -> CurlResult:
    command = [
        curl_cmd,
        "-q",  # Ignore ~/.curlrc so only the loopback proxy below is used.
        "--silent",
        "--show-error",
        "--noproxy",
        "",
        "--proxy",
        proxy_url,
        "--connect-timeout",
        str(connect_timeout),
        "--max-time",
        str(max_time),
        "--max-filesize",
        str(MAX_PROBE_BODY_BYTES),
        "--user-agent",
        "mihomo-exit-verifier/1",
        "--write-out",
        "\n%{http_code}",
        "--output",
        "-",
        url,
    ]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    }
    if cancel_event is not None and cancel_event.is_set():
        return CurlResult(130, "", "cancelled", None)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise VerifierError(f"curl executable not found: {curl_cmd}") from exc
    deadline = time.monotonic() + max_time + connect_timeout + 5
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    _terminate_process(process)
                    return CurlResult(130, "", "cancelled", None)
                if time.monotonic() >= deadline:
                    _terminate_process(process)
                    return CurlResult(124, "", "curl outer timeout", None)
    except BaseException:
        _terminate_process(process)
        raise
    body, separator, status_text = stdout.rpartition("\n")
    if not separator:
        body = ""
        status_text = stdout
    try:
        status = int(status_text.strip())
    except ValueError:
        status = None
    return CurlResult(process.returncode, body, stderr, status)


def _probe_endpoint(
    name: str,
    url: str,
    proxy_url: str,
    *,
    runner: Callable[..., CurlResult],
    clock: Callable[[], float],
    curl_cmd: str,
    connect_timeout: int,
    max_time: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    checked_at = clock()
    started = time.monotonic()
    evidence: dict[str, Any] = {
        "endpoint": name,
        "url": url,
        "checked_at": iso_time(checked_at),
        "duration_ms": None,
        "ok": False,
        "status": None,
        "ip": None,
        "country": None,
        "reason": None,
        "retry_after": None,
    }
    try:
        kwargs: dict[str, Any] = {
            "curl_cmd": curl_cmd,
            "connect_timeout": connect_timeout,
            "max_time": max_time,
        }
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        result = runner(url, proxy_url, **kwargs)
    except VerifierError:
        raise
    except Exception as exc:  # Keep one broken probe from aborting the scan.
        evidence["reason"] = f"probe_error:{type(exc).__name__}"
        evidence["duration_ms"] = int((time.monotonic() - started) * 1000)
        return evidence
    evidence["duration_ms"] = int((time.monotonic() - started) * 1000)
    evidence["status"] = result.status
    if result.returncode != 0:
        evidence["reason"] = (
            "timeout" if result.returncode in {28, 124} else f"curl_error_{result.returncode}"
        )
        return evidence
    if result.status is None:
        evidence["reason"] = "missing_http_status"
        return evidence
    if result.status == 429:
        evidence["reason"] = "rate_limited"
        return evidence
    if result.status != 200:
        evidence["reason"] = f"http_status_{result.status}"
        return evidence
    try:
        if name == "cloudflare_trace":
            ip, country = parse_cloudflare_trace(result.stdout)
        elif name == "ipinfo":
            ip, country = parse_ipinfo_json(result.stdout)
        else:  # pragma: no cover - callers use ENDPOINT_NAMES
            raise ValueError("unknown endpoint")
    except ValueError:
        evidence["reason"] = "invalid_response"
        return evidence
    evidence["ok"] = True
    evidence["ip"] = ip
    evidence["country"] = country
    return evidence


def _entry(
    verdict: str,
    reason: str,
    probes: Mapping[str, Any],
    checked_at: float,
    *,
    exit_countries: Sequence[str] = (),
    ip_mismatch: bool = False,
) -> dict[str, Any]:
    ok = verdict in {"ru", "non_ru"}
    ttl = SUCCESS_TTL_SECONDS if ok else FAILURE_TTL_SECONDS
    return {
        "verdict": verdict,
        "reason": reason,
        "ok": ok,
        "checked_at": float(checked_at),
        "expires_at": float(checked_at) + ttl,
        "ttl_seconds": ttl,
        "probes": dict(probes),
        "exit_countries": list(exit_countries),
        "ip_mismatch": bool(ip_mismatch),
    }


def probe_node(
    candidate: Candidate,
    proxy_url: str,
    *,
    runner: Callable[..., CurlResult] = run_curl,
    clock: Callable[[], float] = time.time,
    curl_cmd: str = "curl",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    max_time: int = DEFAULT_MAX_TIME_SECONDS,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    probes = {
        "cloudflare_trace": _probe_endpoint(
            "cloudflare_trace",
            CLOUDFLARE_TRACE_URL,
            proxy_url,
            runner=runner,
            clock=clock,
            curl_cmd=curl_cmd,
            connect_timeout=connect_timeout,
            max_time=max_time,
            cancel_event=cancel_event,
        ),
        "ipinfo": _probe_endpoint(
            "ipinfo",
            IPINFO_URL,
            proxy_url,
            runner=runner,
            clock=clock,
            curl_cmd=curl_cmd,
            connect_timeout=connect_timeout,
            max_time=max_time,
            cancel_event=cancel_event,
        ),
    }
    checked_at = clock()
    countries = sorted(
        {probe["country"] for probe in probes.values() if isinstance(probe.get("country"), str)}
    )
    if not all(probe["ok"] for probe in probes.values()):
        reasons = ",".join(
            f"{name}={probe['reason'] or 'ok'}" for name, probe in probes.items()
        )
        return _entry(
            "unverified",
            f"probe_failed:{reasons}",
            probes,
            checked_at,
            exit_countries=countries,
        )
    cloudflare = probes["cloudflare_trace"]
    ipinfo = probes["ipinfo"]
    ip_mismatch = cloudflare["ip"] != ipinfo["ip"]
    cloudflare_country = cloudflare["country"]
    ipinfo_country = ipinfo["country"]
    if cloudflare_country == "RU" and ipinfo_country == "RU":
        verdict = "ru"
        reason = "both_endpoints_ru"
    elif cloudflare_country != "RU" and ipinfo_country != "RU":
        verdict = "non_ru"
        reason = "both_endpoints_non_ru"
    else:
        verdict = "unverified"
        reason = "endpoint_country_disagreement"
    return _entry(
        verdict,
        reason,
        probes,
        checked_at,
        exit_countries=countries,
        ip_mismatch=ip_mismatch,
    )


def entry_expiry_reason(entry: Mapping[str, Any], now: float) -> str | None:
    try:
        checked_at = float(entry["checked_at"])
        ok = bool(entry["ok"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return "cache_entry_invalid"
    if not math.isfinite(checked_at) or checked_at > float(now) + 60:
        return "cache_timestamp_invalid"
    ttl = SUCCESS_TTL_SECONDS if ok else FAILURE_TTL_SECONDS
    if float(now) - checked_at >= ttl:
        return "cache_expired"
    return None


def _entry_is_fresh(entry: Mapping[str, Any], now: float) -> bool:
    return entry_expiry_reason(entry, now) is None


def is_physical_interface(device: str) -> bool:
    if not device or "/" in device:
        return False
    base = f"/sys/class/net/{device}"
    if not os.path.exists(os.path.join(base, "device")):
        return False
    try:
        with open(os.path.join(base, "type"), encoding="ascii") as handle:
            return handle.read().strip() == "1"  # ARPHRD_ETHER
    except OSError:
        return False


def _default_routes(ip_cmd: str, runner: Callable[[Sequence[str]], str] | None) -> list[dict]:
    command = [ip_cmd, "-json", "route", "show", "default"]
    if runner is not None:
        output = runner(command)
    else:
        try:
            output = subprocess.check_output(
                command, text=True, timeout=5, stderr=subprocess.DEVNULL
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerifierError(f"cannot inspect default routes: {exc}") from exc
    try:
        routes = json.loads(output)
    except json.JSONDecodeError as exc:
        raise VerifierError("cannot parse default routes") from exc
    if not isinstance(routes, list):
        raise VerifierError("default route output is not a list")
    return [route for route in routes if isinstance(route, dict)]


def detect_uplink(
    *,
    ip_cmd: str = "ip",
    runner: Callable[[Sequence[str]], str] | None = None,
    physical: Callable[[str], bool] = is_physical_interface,
) -> str:
    routes = _default_routes(ip_cmd, runner)
    candidates: list[tuple[int, str]] = []
    for route in routes:
        device = route.get("dev")
        if not isinstance(device, str) or not physical(device):
            continue
        try:
            metric = int(route.get("metric", 0))
        except (TypeError, ValueError):
            metric = 0
        candidates.append((metric, device))
    if not candidates:
        raise VerifierError(
            "no physical default-route interface found; refusing to probe through a tunnel"
        )
    candidates.sort()
    return candidates[0][1]


def allocate_loopback_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def build_probe_config(
    candidates: Sequence[Candidate], uplink: str, ports: Sequence[int]
) -> tuple[dict[str, Any], dict[str, str]]:
    if len(candidates) != len(ports):
        raise ValueError("candidate and port counts differ")
    proxies: list[dict[str, Any]] = []
    listeners: list[dict[str, Any]] = []
    proxy_urls: dict[str, str] = {}
    for index, (candidate, port) in enumerate(zip(candidates, ports)):
        proxy = dict(candidate.proxy)
        proxy["name"] = candidate.name
        proxy["interface-name"] = uplink
        proxy.pop("routing-mark", None)
        proxy.pop("dialer-proxy", None)
        proxies.append(proxy)
        listener_name = f"probe-{index}"
        listeners.append(
            {
                "name": listener_name,
                "type": "mixed",
                "listen": "127.0.0.1",
                "port": int(port),
                "proxy": candidate.name,
                "udp": False,
            }
        )
        proxy_urls[candidate.key] = f"http://127.0.0.1:{int(port)}"
    dns_servers = [f"8.8.8.8#{uplink}", f"1.1.1.1#{uplink}"]
    config = {
        "mixed-port": 0,
        "port": 0,
        "socks-port": 0,
        "redir-port": 0,
        "tproxy-port": 0,
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "direct",
        "log-level": "silent",
        "ipv6": True,
        "find-process-mode": "off",
        "interface-name": uplink,
        "tun": {"enable": False},
        "dns": {
            "enable": True,
            "ipv6": True,
            "enhanced-mode": "normal",
            "use-hosts": False,
            "respect-rules": False,
            "nameserver": list(dns_servers),
            "proxy-server-nameserver": list(dns_servers),
            "direct-nameserver": list(dns_servers),
        },
        "proxies": proxies,
        "listeners": listeners,
    }
    return config, proxy_urls


class ProbeSession:
    """A private TUN-less Mihomo process used only for this scan."""

    def __init__(
        self,
        mihomo_cmd: str,
        config: Mapping[str, Any],
        proxy_urls: Mapping[str, str],
        *,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        self.mihomo_cmd = mihomo_cmd
        self.config = dict(config)
        self.proxy_urls = dict(proxy_urls)
        self.startup_timeout = startup_timeout
        self.directory: str | None = None
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "ProbeSession":
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def start(self) -> None:
        if yaml is None:
            raise VerifierError("PyYAML is required but is not available")
        self.directory = tempfile.mkdtemp(prefix="mihomo-exit-verifier-")
        os.chmod(self.directory, 0o700)
        config_path = os.path.join(self.directory, "config.yaml")
        with open(config_path, "x", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            yaml.safe_dump(self.config, handle, sort_keys=False, allow_unicode=True)
        log_path = os.path.join(self.directory, "mihomo.log")
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            self.process = subprocess.Popen(
                [self.mihomo_cmd, "-d", self.directory, "-f", config_path],
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                cwd=self.directory,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise VerifierError(
                f"cannot start mihomo ({self.mihomo_cmd}): {exc.strerror or exc}"
            ) from exc
        finally:
            os.close(log_fd)
        self._wait_ready()

    def _wait_ready(self) -> None:
        if self.process is None:
            raise VerifierError("temporary mihomo was not started")
        ports: list[int] = []
        for listener in self.config.get("listeners", []):
            ports.append(int(listener["port"]))
        deadline = time.monotonic() + self.startup_timeout
        for port in ports:
            while True:
                if self.process.poll() is not None:
                    raise VerifierError(
                        f"temporary mihomo exited with code {self.process.returncode}"
                    )
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise VerifierError("temporary mihomo did not open its listeners")
                    time.sleep(0.05)

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None


def probe_nodes(
    candidates: Sequence[Candidate],
    uplink: str,
    *,
    workers: int = DEFAULT_WORKERS,
    mihomo_cmd: str = "mihomo",
    curl_cmd: str = "curl",
    clock: Callable[[], float] = time.time,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    max_time: int = DEFAULT_MAX_TIME_SECONDS,
) -> dict[str, dict[str, Any]]:
    unique: list[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.key not in seen:
            seen.add(candidate.key)
            unique.append(candidate)
    if not unique:
        return {}
    ports = allocate_loopback_ports(len(unique))
    config, proxy_urls = build_probe_config(unique, uplink, ports)
    results: dict[str, dict[str, Any]] = {}
    session = ProbeSession(mihomo_cmd, config, proxy_urls)
    executor = ThreadPoolExecutor(max_workers=min(workers, len(unique)))
    cancel_event = threading.Event()
    futures: dict[Any, Candidate] = {}
    cancelled = False
    try:
        session.start()
        futures = {
            executor.submit(
                probe_node,
                candidate,
                session.proxy_urls[candidate.key],
                clock=clock,
                curl_cmd=curl_cmd,
                connect_timeout=connect_timeout,
                max_time=max_time,
                cancel_event=cancel_event,
            ): candidate
            for candidate in unique
        }
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                results[candidate.key] = future.result()
            except VerifierError:
                raise
            except Exception as exc:
                results[candidate.key] = _entry(
                    "unverified",
                    f"internal_error:{type(exc).__name__}",
                    {},
                    clock(),
                )
        return results
    except BaseException:
        cancelled = True
        raise
    finally:
        if cancelled:
            cancel_event.set()
            for future in futures:
                future.cancel()
            session.close()
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)
            session.close()


def _derive_classification(
    candidate: Candidate, entry: Mapping[str, Any] | None, now: float
) -> tuple[str, str | None, str, bool]:
    if entry is None:
        return "unverified", None, "not_probed", True
    expiry = entry_expiry_reason(entry, now)
    if expiry is not None:
        return "unverified", None, expiry, True
    verdict = entry["verdict"]
    if verdict == "ru":
        if candidate.original_country == "RU":
            return "russian", "RU-AUTO", "both_endpoints_ru", False
        return "excluded", None, "foreign_labelled_ru_exit", False
    if verdict == "non_ru":
        if candidate.original_country == "RU":
            return "excluded", None, "ru_labelled_non_ru_exit", False
        return "foreign", "FOREIGN-AUTO", "both_endpoints_non_ru", False
    return "unverified", None, entry["reason"], False


def _entry_report_fields(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    if entry is None:
        return {
            "verdict": None,
            "checked_at": None,
            "expires_at": None,
            "evidence": None,
            "ip_mismatch": False,
            "exit_countries": [],
        }
    try:
        checked_at = float(entry["checked_at"])
        ttl = SUCCESS_TTL_SECONDS if entry["ok"] else FAILURE_TTL_SECONDS
        return {
            "verdict": entry["verdict"],
            "checked_at": iso_time(checked_at),
            "expires_at": iso_time(checked_at + ttl),
            "evidence": entry.get("probes", {}),
            "ip_mismatch": bool(entry.get("ip_mismatch", False)),
            "exit_countries": entry.get("exit_countries", []),
        }
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return {
            "verdict": None,
            "checked_at": None,
            "expires_at": None,
            "evidence": None,
            "ip_mismatch": False,
            "exit_countries": [],
        }


def _build_report(
    snapshot: ConfigSnapshot,
    cache: CacheState,
    now: float,
    *,
    scan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    groups: dict[str, list[str]] = {"RU-AUTO": [], "FOREIGN-AUTO": []}
    counts = {"russian": 0, "foreign": 0, "excluded": 0, "unverified": 0}
    for candidate in snapshot.candidates:
        entry = cache.entries.get(candidate.key)
        classification, group, reason, stale = _derive_classification(candidate, entry, now)
        counts[classification] += 1
        if group is not None:
            groups[group].append(candidate.name)
        node = {
            "name": candidate.name,
            "key": candidate.key,
            "original_country": candidate.original_country,
            "classification": classification,
            "eligible_group": group,
            "reason": reason,
            "stale": stale,
        }
        node.update(_entry_report_fields(entry))
        nodes.append(node)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_time(now),
        "cache_generation": cache.generation,
        "cache_updated_at": cache.updated_at,
        "cache_entries": len(cache.entries),
        "cache_warnings": list(cache.warnings),
        "counts": {
            **counts,
            "candidates": len(snapshot.candidates),
            "ignored": len(snapshot.ignored),
        },
        "groups": groups,
        "nodes": nodes,
        "ignored": [{"name": node.name, "reason": node.reason} for node in snapshot.ignored],
    }
    if scan is not None:
        report["scan"] = dict(scan)
    return report


def classify(
    config_path: str, cache_path: str, *, clock: Callable[[], float] = time.time
) -> dict[str, Any]:
    snapshot = load_config_snapshot(config_path)
    cache = read_cache(cache_path)
    return _build_report(snapshot, cache, clock())


def _unique_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    unique: list[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.key not in seen:
            seen.add(candidate.key)
            unique.append(candidate)
    return unique


def refresh(
    config_path: str,
    cache_path: str,
    *,
    force: bool = False,
    workers: int = DEFAULT_WORKERS,
    mihomo_cmd: str = "mihomo",
    curl_cmd: str = "curl",
    ip_cmd: str = "ip",
    probe_fn: Callable[[Candidate, float], Mapping[str, Any]] | None = None,
    uplink: str | None = None,
    clock: Callable[[], float] = time.time,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    max_time: int = DEFAULT_MAX_TIME_SECONDS,
) -> dict[str, Any]:
    if workers < 1 or workers > 16:
        raise VerifierError("workers must be between 1 and 16")
    snapshot = load_config_snapshot(config_path)
    started_at = clock()
    with _scan_lock(cache_path):
        start_cache = read_cache(cache_path)
        now = clock()
        fresh: dict[str, dict[str, Any]] = {}
        pending: list[Candidate] = []
        for candidate in snapshot.candidates:
            entry = start_cache.entries.get(candidate.key)
            if not force and entry is not None and _entry_is_fresh(entry, now):
                fresh[candidate.key] = entry
            else:
                pending.append(candidate)

        results: dict[str, dict[str, Any]] = {}
        uplink_used = uplink
        unsupported = [c for c in _unique_candidates(pending) if c.unsupported_reason]
        supported = [c for c in _unique_candidates(pending) if not c.unsupported_reason]
        for candidate in unsupported:
            results[candidate.key] = _entry(
                "unverified", candidate.unsupported_reason or "unsupported", {}, clock()
            )
        if supported:
            if probe_fn is not None:
                for candidate in supported:
                    results[candidate.key] = dict(probe_fn(candidate, clock()))
            else:
                if uplink_used is None:
                    uplink_used = detect_uplink(ip_cmd=ip_cmd)
                results.update(
                    probe_nodes(
                        supported,
                        uplink_used,
                        workers=workers,
                        mihomo_cmd=mihomo_cmd,
                        curl_cmd=curl_cmd,
                        clock=clock,
                        connect_timeout=connect_timeout,
                        max_time=max_time,
                    )
                )

        with _cache_lock(cache_path, exclusive=True):
            current_cache = _load_cache_file(cache_path)
            if current_cache.generation != start_cache.generation:
                raise StaleScanError(
                    "cache was reset or replaced while the scan was running; discarding results"
                )
            try:
                current_snapshot = load_config_snapshot(config_path)
            except VerifierError as exc:
                raise StaleScanError(
                    "configuration changed or became unreadable during the scan; "
                    "discarding results"
                ) from exc
            if current_snapshot.node_fingerprint != snapshot.node_fingerprint:
                raise StaleScanError(
                    "node set changed while the scan was running; discarding results"
                )
            entries: dict[str, dict[str, Any]] = {}
            for candidate in snapshot.candidates:
                if candidate.key in fresh:
                    entries[candidate.key] = fresh[candidate.key]
                elif candidate.key in results:
                    entries[candidate.key] = results[candidate.key]
            finished_at = clock()
            new_cache = CacheState(
                current_cache.generation,
                entries,
                iso_time(finished_at),
                start_cache.warnings + current_cache.warnings,
            )
            _write_cache(cache_path, new_cache, finished_at)

    scan = {
        "started_at": iso_time(started_at),
        "finished_at": iso_time(finished_at),
        "force": force,
        "workers": workers,
        "reused": len(fresh),
        "probed": len(supported),
        "unsupported": len(unsupported),
        "probe_failures": sum(1 for candidate in supported if not results[candidate.key]["ok"]),
        "pruned": len(set(start_cache.entries) - set(entries)),
        "uplink": uplink_used,
    }
    return _build_report(snapshot, new_cache, finished_at, scan=scan)


def _print_summary(report: Mapping[str, Any]) -> None:
    counts = report["counts"]
    print(
        "cache generation {generation}: {russian} russian, {foreign} foreign, "
        "{excluded} excluded, {unverified} unverified, {ignored} ignored".format(
            generation=report["cache_generation"], **counts
        )
    )
    scan = report.get("scan")
    if scan is not None:
        print(
            "scan: reused {reused}, probed {probed}, failures {probe_failures}, "
            "pruned {pruned}, uplink {uplink}".format(**scan)
        )
    for node in report["nodes"]:
        group = node["eligible_group"] or "-"
        print(
            f"{node['classification']:<10} {group:<12} {node['name']} "
            f"[{node['reason']}]"
        )


def _emit(report: Mapping[str, Any], as_json: bool) -> None:
    for warning in report.get("cache_warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mihomo-exit-verifier",
        description="Classify Mihomo nodes by their observed exit country.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    classify_command = commands.add_parser(
        "classify", help="render cached classifications without network access"
    )
    classify_command.add_argument("--config", required=True)
    classify_command.add_argument("--cache", required=True)
    classify_command.add_argument("--json", action="store_true")

    refresh_command = commands.add_parser(
        "refresh", help="probe stale nodes and commit the cache atomically"
    )
    refresh_command.add_argument("--config", required=True)
    refresh_command.add_argument("--cache", required=True)
    refresh_command.add_argument("--force", action="store_true")
    refresh_command.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    refresh_command.add_argument("--mihomo", default="mihomo")
    refresh_command.add_argument("--curl", default="curl")
    refresh_command.add_argument("--ip", default="ip")
    refresh_command.add_argument("--json", action="store_true")

    reset_command = commands.add_parser(
        "reset", help="atomically clear classifications and advance the generation"
    )
    reset_command.add_argument("--cache", required=True)
    reset_command.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        signal.signal(signal.SIGTERM, lambda signum, frame: raise_system_exit(signum))
        signal.signal(signal.SIGINT, lambda signum, frame: raise_system_exit(signum))
    except ValueError:  # not in the main thread (tests)
        pass
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "classify":
            report = classify(args.config, args.cache)
            _emit(report, bool(args.json))
        elif args.command == "refresh":
            report = refresh(
                args.config,
                args.cache,
                force=args.force,
                workers=args.workers,
                mihomo_cmd=args.mihomo,
                curl_cmd=args.curl,
                ip_cmd=args.ip,
            )
            _emit(report, bool(args.json))
        elif args.command == "reset":
            generation = reset_cache(args.cache)
            report = {
                "schema_version": SCHEMA_VERSION,
                "generation": generation["generation"],
                "cache_generation": generation["generation"],
                "cache_entries": 0,
                "cache_warnings": generation["warnings"],
            }
            if args.json:
                _emit(report, True)
            else:
                print(f"cache cleared; generation {generation['generation']}")
                for warning in generation["warnings"]:
                    print(f"warning: {warning}", file=sys.stderr)
        else:  # pragma: no cover - argparse enforces choices
            parser.error("unknown command")
            return 2
    except VerifierError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"error: operating system failure: {exc.strerror or exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


def raise_system_exit(signum: int) -> None:
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    sys.exit(main())
