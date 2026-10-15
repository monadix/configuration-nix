def main [credential: path, settings: path] {
    try {
        let subscription = http get --raw --max-time 30sec --headers {User-Agent: mihomo} (open --raw $credential | str trim) | from yaml
        let local = open $settings
        # Keep the subscription's DIRECT decisions unchanged.
        let rules = $subscription.rules | each {|rule|
            $rule | str replace --regex ',\s*Quattro VPN\s*$' ',FOREIGN-AUTO'
        }
        # Prefer subscription DNS; retain local fallback when it is absent.
        # Preserve provider filters, adding the work resolver and real-IP exception.
        let upstream_dns = ($subscription.dns? | default $local.dns)
        let work_policy = ($local.dns.nameserver-policy? | default {})
        let upstream_policy = ($upstream_dns.nameserver-policy? | default {})
        let dns_with_policy = $upstream_dns | upsert nameserver-policy ($upstream_policy | merge $work_policy)
        let filters = ($dns_with_policy.fake-ip-filter? | default [])
        let dns = match ($dns_with_policy.fake-ip-filter-mode? | default 'blacklist') {
            'rule' => (
                $dns_with_policy | upsert fake-ip-filter (
                    (['DOMAIN-SUFFIX,internal.madrigal.ru,real-ip'] ++ $filters) | uniq
                )
            )
            'blacklist' => (
                $dns_with_policy | upsert fake-ip-filter (
                    ($filters ++ ['+.internal.madrigal.ru']) | uniq
                )
            )
            _ => $dns_with_policy
        }
        let groups = $subscription.proxy-groups | each {|group|
            if $group.name == 'Quattro VPN' {
                {name: 'Quattro VPN', type: select, proxies: [FOREIGN-AUTO]}
            } else { $group }
        }
        $subscription | merge $local
        | upsert dns $dns
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
