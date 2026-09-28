{
  config,
  pkgs,
  pkgsStable,
  ...
}:
{
  networking = {
    nftables.enable = true;

    networkmanager = {
      enable = true;

      plugins = with pkgs; [
        networkmanager-l2tp
        networkmanager-strongswan
      ];

      ensureProfiles = {
        environmentFiles = [ config.sops.templates.mdr-l2tp-env.path ];

        profiles.mdr-l2tp = {
          connection = {
            id = "mdr-l2tp";
            type = "vpn";
            autoconnect = false;
          };

          vpn = {
            service-type = "org.freedesktop.NetworkManager.l2tp";
            gateway = "rnd.vpn.madrigal.ru";
            user = "$MDR_L2TP_USER";

            ipsec-enabled = "yes";
            ipsec-psk = "$MDR_L2TP_PSK";
            ipsec-gateway-id = "%any";

            password-flags = "0";
          };

          vpn-secrets.password = "$MDR_L2TP_PASS";

          ipv4 = {
            method = "auto";
            never-default = "true";
            ignore-auto-routes = "true";
            ignore-auto-dns = "false";
            route1 = "172.16.0.100/32,,0";
            route2 = "172.16.0.101/32,,0";
            route3 = "172.16.20.0/24,,0";
            dns = "172.16.0.101";
            dns-search = "internal.madrigal.ru";
          };

          ipv6.method = "disabled";
        };
      };
    };

    dhcpcd = {
      wait = "background";
      extraConfig = "noarp";
    };

    wg-quick.interfaces.lena727 = {
      autostart = false;
      address = [ "10.8.0.17/24" ];
      dns = [ "8.8.8.8" ];
      privateKeyFile = config.sops.secrets.lena727-wg-private-key.path;

      peers = [
        {
          publicKey = "NjJfKekR5QmqlC8AYioRXvgBIeNlYGFElJjRql7WvT0=";
          allowedIPs = [ "0.0.0.0/0" "::/0" ];
          endpoint = "ams.server.lena727.ru:51820";
          persistentKeepalive = 0;
          presharedKeyFile = config.sops.secrets.lena727-wg-preshared-key.path;
        }
      ];
    };

    firewall = {
      enable = true;
      checkReversePath = "loose";
      trustedInterfaces = [ "Mihomo" ];

      extraReversePathFilterRules = ''
        iifname { "Mihomo" } accept comment "trusted interface"
      '';

      allowedTCPPorts = [ 80 443 25565 ];
      allowedUDPPorts = [ 51820 ];
      allowedUDPPortRanges = [
        { from = 28800; to = 28802; }
        { from = 25565; to = 25566; }
      ];
    };
  };

  environment.etc."strongswan.conf".text = '''';
  services.strongswan.enable = true;

  boot.kernel.sysctl = {
    "net.ipv4.ip_default_ttl" = 65;
    "net.ipv6.conf.all.hop_limit" = 65;
  };

  programs.clash-verge = {
    enable = true;
    package = pkgsStable.clash-verge-rev;
    serviceMode = true;
    tunMode = true;
  };

  services.openssh.enable = true;
}
