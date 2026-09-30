# Repository context

- Read the README and relevant files before editing. Preserve its role as a
  concise repository description and keep changes within the requested scope.
- `flake.nix` registers logical hosts and constructs `nixosConfigurations`.
  `common/` is imported for every host; `hosts/` holds machine-specific modules.
- Host names match `home-config.homeModules` in `monadix/home-manager-config`
  (`../home-manager`). `MDR024` maps to `hosts/madrigoal`; other host paths match
  their logical names. User environment changes belong in the Home Manager repo.
- `hosts/ugly-rod/` and `hosts/madrigoal/` include Disko configuration.
  `common/secrets.nix` integrates SOPS secrets from `secrets/secrets.yaml`.
- Validate affected hosts with `nix eval` on their
  `config.system.build.toplevel.drvPath`, and disk changes on
  `config.system.build.diskoScript.drvPath`. Existing CI checks are in
  `.github/workflows/evaluate-hosts.yml`.
