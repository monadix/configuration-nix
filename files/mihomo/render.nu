# Render RU-AUTO/FOREIGN-AUTO from the latest stored subscription and the
# cached location classifications. Callers hold the render lock so a scan can
# never apply an obsolete subscription snapshot. With --source-kind config the
# applied config is rewritten in place; reset uses that fallback when no
# subscription is stored.
#
# The two automatic groups use explicit verified members. An empty verified
# set becomes [REJECT]; include-all-proxies is removed so an unverified or
# mislabelled node can never leak into either group.

def verified-group [groups: list, name: string, members: list] {
    let members = if ($members | is-empty) { [REJECT] } else { $members }
    $groups | each {|group|
        if $group.name == $name {
            $group
            | reject --optional include-all-proxies filter exclude-filter
            | upsert proxies $members
        } else { $group }
    }
}

def main [
    subscription: path
    settings: path
    cache: path
    python: path
    verifier: path
    mihomo: path
    systemctl: path
    state: path
    --promote: path
    --source-kind: string = 'subscription'
    --dry-run
] {
    if $source_kind not-in ['subscription' 'config'] {
        error make {msg: 'Unsupported render source kind'}
    }
    let subscription_doc = (open --raw $subscription | from yaml)
    let local = (open --raw $settings | from yaml)
    let base_path = ($state | path join 'candidate-base.yaml')
    let candidate_path = ($state | path join 'candidate.yaml')
    let config_path = ($state | path join 'config.yaml')
    let previous_path = ($state | path join 'previous.yaml')

    # Keep the subscription's DIRECT decisions unchanged.
    let rules = ($subscription_doc.rules? | default [] | each {|rule|
        $rule | str replace --regex ',\s*Quattro VPN\s*$' ',FOREIGN-AUTO'
    })
    # Prefer subscription DNS; retain local fallback when it is absent.
    # Preserve provider filters, adding the work resolver and real-IP exception.
    let upstream_dns = ($subscription_doc.dns? | default $local.dns)
    let work_policy = ($local.dns.nameserver-policy? | default {})
    let upstream_policy = ($upstream_dns.nameserver-policy? | default {})
    let dns_with_policy = ($upstream_dns | upsert nameserver-policy ($upstream_policy | merge $work_policy))
    let filters = ($dns_with_policy.fake-ip-filter? | default [])
    let dns = match ($dns_with_policy.fake-ip-filter-mode? | default 'blacklist') {
        'rule' => (
            $dns_with_policy | upsert fake-ip-filter (
                (['DOMAIN-SUFFIX,internal.madrigal.ru,real-ip', 'DOMAIN,rnd.vpn.madrigal.ru,real-ip'] ++ $filters) | uniq
            )
        )
        'blacklist' => (
            $dns_with_policy | upsert fake-ip-filter (
                ($filters ++ ['+.internal.madrigal.ru', 'rnd.vpn.madrigal.ru']) | uniq
            )
        )
        _ => {
            print --stderr 'Unsupported fake-ip-filter-mode; gateway real-IP exceptions cannot be guaranteed.'
            error make {msg: 'Unsupported fake-ip-filter-mode'}
        }
    }
    # Subscription copies of the automatic groups are replaced by the local
    # definitions, so there is exactly one group of each name in the result.
    let subscription_groups = ($subscription_doc.proxy-groups? | default [])
        | where {|group| $group.name not-in ['RU-AUTO' 'FOREIGN-AUTO']}
        | each {|group|
            if $group.name == 'Quattro VPN' {
                {name: 'Quattro VPN', type: select, proxies: [FOREIGN-AUTO]}
            } else { $group }
        }
    let base = if $source_kind == 'config' {
        # Reset fallback: the applied config already contains local settings.
        # Only its automatic groups are regenerated from the cache.
        $subscription_doc
    } else {
        $subscription_doc
        | reject --optional external-controller external-controller-unix external-controller-tls external-ui external-ui-url secret authentication
        | merge $local
        | upsert profile (($subscription_doc.profile? | default {}) | merge $local.profile)
        | upsert proxies (($subscription_doc.proxies? | default []) ++ $local.proxies)
        | upsert dns $dns
        | upsert proxy-groups ($subscription_groups ++ $local.proxy-groups)
        | upsert rules ($local.rules ++ $rules)
    }

    try {
        $base | to yaml | save --force $base_path
        let classification = (do { ^$python $verifier classify --config $base_path --cache $cache --json } | complete)
        if $classification.exit_code != 0 {
            error make {msg: $'classification failed: ($classification.stderr | str trim)'}
        }
        if not ($classification.stderr | is-empty) {
            print --stderr $classification.stderr
        }
        let report = ($classification.stdout | from json)
        let groups_report = ($report.groups? | default {})
        let ru_members = ($groups_report | get -o 'RU-AUTO' | default [])
        let foreign_members = ($groups_report | get -o 'FOREIGN-AUTO' | default [])
        let with_ru = (verified-group $base.proxy-groups 'RU-AUTO' $ru_members)
        let groups = (verified-group $with_ru 'FOREIGN-AUTO' $foreign_members)
        let rendered = ($base | upsert proxy-groups $groups)
        $rendered | to yaml | save --force $candidate_path
        let check = (do { ^$mihomo -t -d $state -f $candidate_path } | complete)
        if $check.exit_code != 0 {
            # Do not echo mihomo output: it can contain configuration fragments.
            error make {msg: 'Invalid Mihomo configuration'}
        }
        if $dry_run {
            print $candidate_path
            return
        }
        if $promote != null {
            mv --force $subscription $promote
        }
        let changed = if ($config_path | path exists) {
            (open --raw $config_path) != (open --raw $candidate_path)
        } else { true }
        if not $changed {
            rm --force $candidate_path
            rm --force $base_path
            let started = (do { ^$systemctl --no-block start mihomo.service } | complete)
            if $started.exit_code != 0 {
                print --stderr $'Could not request mihomo start: ($started.stderr | str trim)'
            }
            return
        }
        # Preserve the previous parse-valid config for manual recovery.
        if ($config_path | path exists) { cp --force $config_path $previous_path }
        mv --force $candidate_path $config_path
        rm --force $base_path
        let restarted = (do { ^$systemctl --no-block try-restart mihomo.service } | complete)
        if $restarted.exit_code != 0 {
            print --stderr $'Could not request mihomo restart: ($restarted.stderr | str trim)'
        }
    } catch {|err|
        rm --force $candidate_path
        rm --force $base_path
        error make {msg: $err.msg}
    }
}
