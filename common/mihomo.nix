{ config, lib, pkgs, ... }:
let
  state = "/var/lib/mihomo-subscription";
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
    requires = [ "mihomo-subscription.service" ];
    after = [ "mihomo-subscription.service" ];
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
    path = [ config.services.mihomo.package pkgs.systemd ];
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "mihomo-subscription";
      StateDirectoryMode = "0700";
      UMask = "0077";
      WorkingDirectory = state;
      LoadCredential = "url:${config.sops.secrets.mihomo-subscription-url.path}";
      ExecStart = "${pkgs.nushell}/bin/nu --no-config-file ${../files/mihomo/update.nu} %d/url ${settings}";
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
