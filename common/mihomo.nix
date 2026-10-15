{ config, pkgs, ... }:
let
  state = "/var/lib/mihomo-subscription";
in
{
  sops.secrets.mihomo-subscription-url = {
    sopsFile = ../secrets/mihomo.yaml;
    key = "subscription-url";
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
      ExecStart = "${pkgs.nushell}/bin/nu --no-config-file ${../files/mihomo/update.nu} %d/url ${../files/mihomo/config.yaml}";
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
