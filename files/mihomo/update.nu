def main [credential: path, settings: path] {
    try {
        let subscription = http get --raw --max-time 30sec --headers {User-Agent: mihomo} (open --raw $credential | str trim) | from yaml
        let local = open $settings
        let rules = $subscription.rules | each {|rule|
            $rule
            | str replace --regex ',\s*DIRECT\s*(,no-resolve)?$' ',RU-AUTO$1'
            | str replace --regex ',\s*Quattro VPN\s*$' ',FOREIGN-AUTO'
        }
        let groups = $subscription.proxy-groups | each {|group|
            if $group.name == 'Quattro VPN' {
                {name: 'Quattro VPN', type: select, proxies: [FOREIGN-AUTO]}
            } else { $group }
        }
        $subscription | merge $local
        | upsert proxy-groups ($groups ++ $local.proxy-groups)
        | upsert rules ($local.rules ++ $rules)
        | reject --optional external-controller external-controller-unix external-controller-tls external-ui external-ui-url secret authentication
        | to yaml | save --force candidate.yaml
        let check = do { ^mihomo -t -d . -f candidate.yaml } | complete
        if $check.exit_code != 0 { error make {msg: 'Invalid Mihomo configuration'} }
    } catch {
        rm --force candidate.yaml
        print --stderr 'Subscription update failed; keeping cached configuration.'
        if not ('config.yaml' | path exists) { exit 1 }
        return
    }
    if ('config.yaml' | path exists) and ((open --raw config.yaml) == (open --raw candidate.yaml)) {
        rm candidate.yaml
        return
    }
    mv --force candidate.yaml config.yaml
    ^systemctl --no-block restart mihomo.service
}
