# Fetch the subscription, then render and apply it under the shared render lock.
# The download is deliberately outside the lock so a background location scan
# never waits for a slow subscription endpoint.

def main [
    credential: path
    settings: path
    render: path
    render_cached: path
    python: path
    verifier: path
    mihomo: path
    systemctl: path
    flock: path
    nu_bin: path
    state: path
] {
    let lock = ($state | path join 'render.lock')
    let next = ($state | path join 'subscription.next.yaml')
    let stored = ($state | path join 'subscription.yaml')
    let cache = ($state | path join 'location-cache.json')
    let config = ($state | path join 'config.yaml')
    try {
        let subscription = http get --raw --max-time 30sec --headers {User-Agent: mihomo} (open --raw $credential | str trim) | from yaml
        # Render from the new snapshot while holding the lock. render.nu
        # promotes it to subscription.yaml before applying config.yaml, so a
        # checker render cannot pick up an older subscription afterwards.
        $subscription | to yaml | save --force $next
        let rendered = (do {
            ^$flock --timeout 120 $lock $nu_bin --no-config-file $render $next $settings $cache $python $verifier $mihomo $systemctl $state --promote $stored
        } | complete)
        if $rendered.exit_code != 0 {
            error make {msg: 'Configuration render failed'}
        }
    } catch {
        rm --force $next
        # The broad catch covers the credential URL and YAML parsing too, so
        # log generically instead of echoing error text with secret fragments.
        print --stderr 'Subscription update failed; keeping the cached configuration.'
        # Expiration still applies while the subscription endpoint is down:
        # render the stored subscription or fall back to the applied config.
        let rendered = (do {
            ^$flock --timeout 120 $lock $nu_bin --no-config-file $render_cached $state $settings $cache $python $verifier $render $mihomo $systemctl $nu_bin
        } | complete)
        if not ($rendered.stderr | is-empty) { print --stderr $rendered.stderr }
        if $rendered.exit_code != 0 {
            print --stderr 'Cached configuration could not be rendered fail-closed.'
        }
        if not ($config | path exists) {
            print --stderr 'No stored subscription or applied configuration; cannot render fail-closed groups.'
            exit 1
        }
        let started = (do { ^$systemctl --no-block start mihomo.service } | complete)
        if $started.exit_code != 0 {
            print --stderr $'Could not request mihomo start: ($started.stderr | str trim)'
        }
        return
    }
}
