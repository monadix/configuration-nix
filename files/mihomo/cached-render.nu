# Render the cached classifications under the caller's render lock. The stored
# subscription is preferred; when it is absent or cannot render/validate, the
# applied config is rendered with --source-kind config so expiry still applies
# without a manual reset. Do not echo render stderr: stored subscription errors
# can contain configuration fragments.

def render-source [
    source: path
    source_kind: string
    settings: path
    cache: path
    python: path
    verifier: path
    render: path
    mihomo: path
    systemctl: path
    state: path
    nu_bin: path
] {
    do {
        ^$nu_bin --no-config-file $render $source $settings $cache $python $verifier $mihomo $systemctl $state --source-kind $source_kind
    } | complete
}

def main [
    state: path
    settings: path
    cache: path
    python: path
    verifier: path
    render: path
    mihomo: path
    systemctl: path
    nu_bin: path
] {
    let stored = ($state | path join 'subscription.yaml')
    let config = ($state | path join 'config.yaml')
    if ($stored | path exists) {
        let rendered = (render-source $stored 'subscription' $settings $cache $python $verifier $render $mihomo $systemctl $state $nu_bin)
        if $rendered.exit_code == 0 { return }
        print --stderr 'Stored subscription could not be rendered; retrying from the applied config.'
    }
    if not ($config | path exists) {
        error make {msg: 'No stored subscription or applied configuration; cannot render fail-closed groups'}
    }
    let fallback = (render-source $config 'config' $settings $cache $python $verifier $render $mihomo $systemctl $state $nu_bin)
    if $fallback.exit_code != 0 {
        error make {msg: 'Applied configuration could not be rendered fail-closed'}
    }
}
