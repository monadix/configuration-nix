{ config, lib, pkgs, ... }:
let
  state = "/var/lib/mihomo-subscription";
  prepareUnit = "mihomo-subscription-prepare.service";
  locationUnit = "mihomo-proxy-location-check.service";
  python = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
  verifier = ../files/mihomo/exit_verifier.py;
  updateScript = ../files/mihomo/update.nu;
  renderScript = ../files/mihomo/render.nu;
  cachedRenderScript = ../files/mihomo/cached-render.nu;
  scheduleScript = ../files/mihomo/schedule-scan.nu;
  locationScript = ../files/mihomo/location-check.nu;
  resetScript = ../files/mihomo/reset-cache.nu;
  nu = "${pkgs.nushell}/bin/nu";
  flock = "${pkgs.util-linux}/bin/flock";
  systemctl = "${pkgs.systemd}/bin/systemctl";
  mihomo = "${config.services.mihomo.package}/bin/mihomo";
  workProxy = { name = "WORK-VPN"; type = "direct"; }
    // lib.optionalAttrs (config.networking.hostName != "MDR024") {
      interface-name = "mdr-vpn";
    };
  settings = pkgs.writeText "mihomo-settings.yaml" (
    builtins.readFile ../files/mihomo/config.yaml
    + "\nproxies: ${builtins.toJSON [ workProxy ]}\n"
  );
in
{
  sops.secrets.mihomo-subscription-url = {
    restartUnits = [ "mihomo-subscription.service" ];
  };

  services.mihomo = {
    enable = true;
    tunMode = true;
    configFile = "${state}/config.yaml";
  };

  systemd.services.mihomo = {
    # Starting mihomo must not start the recurring updater: its ExecStartPost
    # schedules a location scan, and the scan's render can restart mihomo.
    requires = [ prepareUnit ];
    after = [ prepareUnit ];
    serviceConfig = {
      Restart = "on-failure";
      RestartSec = "5s";
      RuntimeDirectory = "mihomo";
      RuntimeDirectoryMode = "0700";
      RestrictAddressFamilies = lib.mkForce "AF_INET AF_INET6 AF_NETLINK AF_UNIX";
    };
  };

  systemd.services.mihomo-subscription = {
    description = "Update Mihomo subscription and preserve its routing rules";
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "mihomo-subscription";
      StateDirectoryMode = "0700";
      UMask = "0077";
      WorkingDirectory = state;
      LoadCredential = "url:${config.sops.secrets.mihomo-subscription-url.path}";
      ExecStart = lib.escapeShellArgs [
        nu "--no-config-file" updateScript
        "%d/url" settings renderScript cachedRenderScript
        "${python}/bin/python3" verifier
        mihomo systemctl flock nu state scheduleScript locationUnit
      ];
    };
  };

  # Cold boot without a cached config still needs one subscription fetch before
  # mihomo starts. When config.yaml exists this preparation is a no-op, so
  # restarting mihomo works offline and never refreshes the subscription.
  systemd.services.mihomo-subscription-prepare = {
    description = "Prepare the initial Mihomo subscription configuration when absent";
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    unitConfig.ConditionPathExists = "!${state}/config.yaml";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${systemctl} start mihomo-subscription.service";
    };
  };

  systemd.services.mihomo-subscription-apply = {
    description = "Apply changed Mihomo subscription settings";
    wantedBy = [ "multi-user.target" ];
    restartTriggers = [ settings updateScript renderScript cachedRenderScript scheduleScript verifier ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${systemctl} start mihomo-subscription.service";
    };
  };

  systemd.services.mihomo-proxy-location-check = {
    description = "Probe Mihomo exit locations and render verified RU/foreign groups";
    wants = [ "network-online.target" ];
    after = [ "network-online.target" "mihomo-subscription.service" ];
    unitConfig.ConditionPathExists = "${state}/config.yaml";
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "mihomo-subscription";
      StateDirectoryMode = "0700";
      UMask = "0077";
      WorkingDirectory = state;
      PrivateTmp = true;
      TimeoutStartSec = "2h";
      TimeoutStopSec = "30s";
      ExecStart = lib.escapeShellArgs [
        nu "--no-config-file" locationScript
        state "${state}/config.yaml" "${state}/location-cache.json"
        settings "${python}/bin/python3" verifier renderScript cachedRenderScript
        mihomo "${pkgs.curl}/bin/curl" "${pkgs.iproute2}/bin/ip"
        systemctl flock nu
      ];
    };
  };

  # A short cached render expires approvals even when a long scan is blocked
  # or terminated before its own post-scan render.
  systemd.services.mihomo-proxy-cache-render = {
    description = "Re-render cached Mihomo exit approvals without probing";
    after = [ "mihomo-subscription.service" ];
    unitConfig.ConditionPathExists = "${state}/config.yaml";
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "mihomo-subscription";
      StateDirectoryMode = "0700";
      UMask = "0077";
      WorkingDirectory = state;
      PrivateTmp = true;
      TimeoutStartSec = "2min";
      TimeoutStopSec = "30s";
      ExecStart = lib.escapeShellArgs [
        flock "--timeout" "90" "${state}/render.lock"
        nu "--no-config-file" cachedRenderScript
        state settings "${state}/location-cache.json"
        "${python}/bin/python3" verifier renderScript mihomo systemctl nu
      ];
    };
  };

  systemd.timers.mihomo-proxy-cache-render = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "15min";
      Unit = "mihomo-proxy-cache-render.service";
    };
  };

  systemd.services.mihomo-proxy-cache-reset = {
    description = "Clear cached Mihomo exit locations, render fail-closed groups, and rescan";
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "mihomo-subscription";
      StateDirectoryMode = "0700";
      UMask = "0077";
      WorkingDirectory = state;
      TimeoutStartSec = "5min";
      TimeoutStopSec = "60s";
      ExecStart = lib.escapeShellArgs [
        nu "--no-config-file" resetScript
        state "${state}/location-cache.json" settings
        "${python}/bin/python3" verifier renderScript cachedRenderScript mihomo
        systemctl flock nu locationUnit
      ];
    };
  };

  systemd.timers.mihomo-subscription = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnUnitActiveSec = "1h";
      Unit = "mihomo-subscription.service";
    };
  };
}
