# Clear only the location cache, re-render fail-closed groups from the latest
# stored subscription (or the applied config when no subscription is stored),
# then start a fresh background scan. Stopping the scan unit first releases its
# process group; the cache generation guard also makes any still-running stale
# scan discard its commit.

def main [
    state: path
    cache: path
    settings: path
    python: path
    verifier: path
    render: path
    render_cached: path
    mihomo: path
    systemctl: path
    flock: path
    nu_bin: path
    location_unit: string
] {
    let stored = ($state | path join 'subscription.yaml')
    let config = ($state | path join 'config.yaml')
    if not ($stored | path exists) and not ($config | path exists) {
        # On an upgraded host the first subscription update may not have run
        # yet, so fetch once when there is no local source to render at all.
        let update = (do { ^$systemctl start mihomo-subscription.service } | complete)
        if $update.exit_code != 0 {
            print --stderr 'No stored or applied configuration and subscription update failed.'
        }
    }
    if not ($stored | path exists) and not ($config | path exists) {
        print --stderr 'No stored subscription or applied configuration; cannot render fail-closed groups.'
        exit 1
    }
    let stop = (do { ^$systemctl stop $location_unit } | complete)
    if $stop.exit_code != 0 {
        print --stderr $'Could not stop ($location_unit): ($stop.stderr | str trim)'
    }
    let reset = (do { ^$python $verifier reset --cache $cache --json } | complete)
    if $reset.exit_code != 0 {
        print --stderr $'Could not clear the location cache: ($reset.stderr | str trim)'
        exit 1
    }
    print $reset.stdout
    let lock = ($state | path join 'render.lock')
    let rendered = (do {
        ^$flock --timeout 120 $lock $nu_bin --no-config-file $render_cached $state $settings $cache $python $verifier $render $mihomo $systemctl $nu_bin
    } | complete)
    if not ($rendered.stderr | is-empty) { print --stderr $rendered.stderr }
    if $rendered.exit_code != 0 {
        print --stderr 'Cache cleared, but rendering the latest configuration failed.'
        exit 1
    }
    let started = (do { ^$systemctl --no-block start $location_unit } | complete)
    if $started.exit_code != 0 {
        print --stderr $'Could not schedule a fresh location scan: ($started.stderr | str trim)'
        exit 1
    }
}
