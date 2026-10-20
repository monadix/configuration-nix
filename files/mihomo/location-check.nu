# Background location check. Cached approvals are rendered before the scan so
# expiry applies even if the scan is terminated, then the scan writes the
# location cache and the latest stored subscription or applied config is
# re-rendered under the shared render lock, so a slow scan can never apply an
# obsolete snapshot.

def main [
    state: path
    config: path
    cache: path
    settings: path
    python: path
    verifier: path
    render: path
    render_cached: path
    mihomo: path
    curl: path
    ip: path
    systemctl: path
    flock: path
    nu_bin: path
] {
    let lock = ($state | path join 'render.lock')
    # Enforce cache expiry before the long scan. A terminated scan then cannot
    # leave approvals applied past their TTL; the separate cached-render timer
    # covers entries that expire while the scan is running.
    let before = (do {
        ^$flock --timeout 120 $lock $nu_bin --no-config-file $render_cached $state $settings $cache $python $verifier $render $mihomo $systemctl $nu_bin
    } | complete)
    if $before.exit_code != 0 {
        print --stderr 'Could not render cached approvals before the scan; continuing.'
    }
    let result = (do {
        ^$python $verifier refresh --config $config --cache $cache --mihomo $mihomo --curl $curl --ip $ip --workers 4
    } | complete)
    if not ($result.stdout | is-empty) { print $result.stdout }
    if not ($result.stderr | is-empty) { print --stderr $result.stderr }
    let scan_failed = ($result.exit_code not-in [0 3])
    if $scan_failed {
        print --stderr $'Location scan failed with exit code ($result.exit_code); keeping the previous cache and re-rendering its unexpired entries.'
    }
    if $result.exit_code == 3 {
        print --stderr 'Location scan was busy or discarded; rendering the latest configuration with the existing cache.'
    }
    if not ($config | path exists) {
        print --stderr 'No applied configuration; nothing to render.'
        if $scan_failed { exit 1 }
        return
    }
    let rendered = (do {
        ^$flock --timeout 120 $lock $nu_bin --no-config-file $render_cached $state $settings $cache $python $verifier $render $mihomo $systemctl $nu_bin
    } | complete)
    if $rendered.exit_code != 0 {
        print --stderr 'Location scan finished, but rendering the latest configuration failed.'
        exit 1
    }
    if $scan_failed { exit 1 }
}
