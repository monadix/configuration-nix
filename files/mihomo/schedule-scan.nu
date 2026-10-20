# Schedule the background location check after a subscription refresh.
#
# An unchanged refresh must not cancel a valid long scan: only start when the
# unit is idle. A changed node identity replaces the active scan so the new
# subscription is verified reliably. Reset keeps its own unconditional restart.

def main [
    systemctl: path
    location_unit: string
    --changed
] {
    let state = (do { ^$systemctl is-active $location_unit } | complete)
    let running = ($state.stdout | str trim) in ['active' 'activating' 'reloading']
    if $changed {
        let request = (do { ^$systemctl --no-block restart $location_unit } | complete)
        if $request.exit_code != 0 {
            print --stderr $'Could not replace the location scan: ($request.stderr | str trim)'
        }
    } else if not $running {
        let request = (do { ^$systemctl --no-block start $location_unit } | complete)
        if $request.exit_code != 0 {
            print --stderr $'Could not start the location scan: ($request.stderr | str trim)'
        }
    }
}
