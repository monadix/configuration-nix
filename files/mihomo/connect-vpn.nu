def disconnect [] {
    do { ^nmcli --wait 10 connection down id mdr-l2tp } | complete | ignore
    # NetworkManager can finish deactivation before IPsec and PPP exit.
    let deadline = (date now) + 10sec
    while (['/run/charon.pid', '/run/starter.charon.pid', '/sys/class/net/mdr-vpn'] | any {|p| $p | path exists }) {
        if (date now) > $deadline { error make {msg: 'Previous VPN did not stop'} }
        sleep 200ms
    }
}

def main [socket: path] {
    if (^nmcli -g GENERAL.STATE connection show id mdr-l2tp | str trim) == 'activated' { return }
    # A running Mihomo process can still be initializing its control socket.
    let deadline = (date now) + 30sec
    while not ($socket | path exists) {
        if (date now) > $deadline { error make {msg: 'Mihomo control socket did not become ready'} }
        sleep 200ms
    }
    let api = 'http://localhost'
    let proxies = http get --max-time 5sec --unix-socket $socket $'($api)/proxies' | get proxies
    let eligible = $proxies.VPN-UPLINK.all | where {|name| $name not-in ['DIRECT', 'REJECT'] and ($proxies | get $name | get udp) }
    let candidates = ($eligible | where $it !~ '(?i)LTE') ++ ($eligible | where $it =~ '(?i)LTE')
    let connected = try {
        mut success = false
        disconnect
        for candidate in $candidates {
            http put --max-time 5sec --unix-socket $socket --content-type application/json $'($api)/proxies/VPN-UPLINK' {name: $candidate} | ignore
            # Discard only this transport's UDP associations between attempts.
            let sessions = http get --max-time 5sec --unix-socket $socket $'($api)/connections' | get connections
            for session in ($sessions | where {|s| 'VPN-UPLINK' in $s.chains }) {
                http delete --max-time 5sec --unix-socket $socket $'($api)/connections/($session.id)' | ignore
            }
            print $'Trying work VPN using ($candidate).'
            let attempt = do { ^nmcli --wait 35 connection up id mdr-l2tp } | complete
            if $attempt.exit_code == 0 {
                print $'Work VPN connected using ($candidate).'
                $success = true
                break
            }
            print --stderr $'VPN attempt using ($candidate) failed with nmcli exit code ($attempt.exit_code).'
            disconnect
        }
        $success
    } catch {|err| print --stderr $err.msg; false }
    if not $connected {
        disconnect
        http put --max-time 5sec --unix-socket $socket --content-type application/json $'($api)/proxies/VPN-UPLINK' {name: REJECT} | ignore
        error make {msg: 'No Russian proxy path established the work VPN'}
    }
}
