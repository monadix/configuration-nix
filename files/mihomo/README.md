# Mihomo subscription and verified exit groups

`mihomo-subscription.service` downloads the subscription, stores it as
`/var/lib/mihomo-subscription/subscription.yaml`, and renders
`config.yaml` from that stored snapshot. Rendering uses only the cached exit
classification, so subscription startup never waits for network probes.

`mihomo.service` depends on `mihomo-subscription-prepare.service`, not on the
recurring updater. The prepare unit fetches only when `config.yaml` is absent,
so a cold boot still gets an initial configuration while a cached boot starts
mihomo offline. Restarting mihomo therefore cannot fetch the subscription or
schedule a location scan. The hourly updater timer, the deployment apply unit,
and explicit updater starts are the only refresh triggers. An unchanged render
leaves the running process untouched; only a changed configuration requests a
`try-restart`. The prepare unit runs the updater only while mihomo waits for
it, and changed rendering uses `try-restart`, so bootstrap cannot restart
mihomo recursively.

`mihomo-proxy-location-check.service` has no independent timer. Each
subscription refresh schedules it after the refresh completes, so verification
can never start against a subscription that the refresh is still replacing. The
former `mihomo-proxy-location-check.timer` is no longer generated; the NixOS
switch stops and removes the running timer unit, so no manual `disable` is
needed. A refresh starts the scan when the unit is idle. An unchanged,
rules-only, cached-fallback, or failed-download refresh leaves an active scan
running, so a scan that lasts longer than the hourly refresh still commits.
Only a changed node identity replaces the active scan with one that reads the
latest subscription; its stale predecessor's commit is rejected by the cache
generation and node fingerprint guards. The service renders the cached
classifications before probing so expired approvals are revoked even if the
scan is later terminated, probes stale nodes into
`/var/lib/mihomo-subscription/location-cache.json`, then re-renders the latest
stored subscription under the shared render lock and validates it with
`mihomo -t` before atomically replacing `config.yaml`. A scan that becomes
obsolete while running exits without committing; the next refresh retries it.

`mihomo-proxy-cache-render.service` still runs every 15 minutes to expire
cached approvals during long or blocked scans. All automatic cached renders use
the stored subscription when it is readable and fall back to rewriting the
applied `config.yaml` when it is absent or cannot render, so expiry does not
require a manual reset. A failed subscription download uses the same path
instead of only starting the old config, and an invalid downloaded subscription
is never promoted.

`RU-AUTO` and `FOREIGN-AUTO` contain explicit verified members only:

- `RU-AUTO`: RU-labelled nodes whose two independent probes both report RU.
- `FOREIGN-AUTO`: non-RU-labelled nodes whose probes both report non-RU.
- Mislabelled or expired nodes are excluded.
- An empty verified set renders `proxies: [REJECT]`. There is no
  `include-all-proxies` fallback and no hard-coded LTE exception.

`VPN-UPLINK` uses UDP-capable Russian proxies only; VPN reconnection tries
non-LTE nodes before LTE nodes and never falls back to direct transport. Its
`REJECT` member provides a fail-closed default when no proxy is selected.

`Quattro VPN`, manual proxy selections, rules, and the backup
`previous.yaml` behaviour are unchanged. A render writes a private
`pending-application` marker before replacing `config.yaml` and removes it only
after `try-restart mihomo.service` is accepted. An unchanged render retries an
outstanding application request, while a render with no marker and no byte
change remains a no-op. The state directory is `0700`; the cache and marker are
written `0600` and must stay root-only.

## Manual operations

```sh
# Recheck stale nodes now. Restart also cancels an active scan so the request
# is not lost while the oneshot is still running.
run0 systemctl restart mihomo-proxy-location-check.service

# Clear all cached approvals, re-render fail-closed groups, then rescan.
run0 systemctl start mihomo-proxy-cache-reset.service
```

The reset unit stops an active scan first. The cache generation guard makes any
scan that was already running discard its commit, so it cannot restore old
approvals after the reset. It uses the stored subscription when readable, or
otherwise rewrites the automatic groups in the applied `config.yaml`, without
fetching a new subscription unnecessarily. A fresh scan is scheduled
immediately afterwards with a restart, so a refresh that started a scan while
the reset was running cannot cause the reset's scan to be dropped; the cache
generation guard rejects that scan's pre-reset results.

Service output, including per-node classification reasons, is in
`journalctl -u mihomo-proxy-location-check.service`.

## Tests

Offline helper and renderer tests:

```sh
PYTHONPATH="$(nix build --no-link --print-out-paths nixpkgs#python3Packages.pyyaml)/lib/python3.14/site-packages" \
  python3 -m unittest discover -s files/mihomo/tests -v
```

The renderer integration tests execute Nushell against offline fixtures and
run `mihomo -t`. They skip when `nu` or `mihomo` is unavailable; set
`NU_TEST_BIN`, `MIHOMO_TEST_BIN`, and `FLOCK_TEST_BIN` to run them from a
development shell.

The service-lifecycle regression runs uniquely named runtime units linked into
the user systemd manager and removes them afterwards. It verifies that repeated
mihomo restarts do not fetch or scan, explicit updater runs do both, and
cold/cached boots complete without a startup dependency deadlock. It also
blocks a controlled scan across several unchanged and failed-fallback refresh
events and checks that it is not cancelled and eventually finishes; a changed
refresh must replace an active scan with one that observes the refreshed
subscription, and a simultaneous refresh and scan start must converge on the
latest configuration. It skips when no user systemd manager or Nushell is
available.
