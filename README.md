# configuration-nix

NixOS system configurations for the machines represented here.

`common/` contains configuration applied to every host. `hosts/` contains each
machine's hardware and system-specific settings. The host registry in
`flake.nix` is the source of the logical host names.

Each NixOS host imports the Home Manager module with the same name from
[`home-manager-config`](https://github.com/monadix/home-manager-config). This
keeps the system and user-environment halves of a machine joined by one name.
