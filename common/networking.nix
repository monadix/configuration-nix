{
  config,
  lib,
  pkgs,
  ...
}:
{
  nixpkgs.overlays = [
    (_final: prev: {
      strongswan = prev.strongswan.overrideAttrs (old: {
        # The mdr-l2tp VPN requires IKEv1, disabled by default since strongSwan 6.1.
        configureFlags = old.configureFlags ++ [ "--enable-ikev1" ];
      });
    })
  ];

  networking = {
    nftables.enable = true;

    networkmanager = {
      enable = true;

      plugins = with pkgs; [
        networkmanager-l2tp
        networkmanager-strongswan
      ];

      # VPN profiles do not support autoconnect; start on uplink activation.
      dispatcherScripts = lib.optionals (config.networking.hostName != "MDR024") [
        {
          source = pkgs.writeShellScript "mdr-l2tp-autoconnect" ''
            [ "$2" = up ] || exit 0
            export LC_ALL=C
            nmcli=${config.networking.networkmanager.package}/bin/nmcli
            [ "$("$nmcli" -g GENERAL.STATE connection show id mdr-l2tp)" = activated ] && exit 0
            case "$("$nmcli" -g connection.type connection show uuid "$CONNECTION_UUID")" in
              802-3-ethernet|802-11-wireless)
                ${pkgs.systemd}/bin/systemctl --no-block start mdr-l2tp-connect.service ;;
            esac
          '';
        }
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
            ipsec-forceencaps = "yes";
            ipsec-psk = "$MDR_L2TP_PSK";
            ipsec-gateway-id = "%any";

            password-flags = "0";
            mtu = "1280";
            mru = "1280";
            lcp-echo-interval = "10";
            lcp-echo-failure = "3";
          };

          vpn-secrets.password = "$MDR_L2TP_PASS";

          ipv4 = {
            method = "auto";
            never-default = "true";
            auto-route-ext-gw = 0;
            ignore-auto-routes = "true";
            ignore-auto-dns = "false";
            route4 = "172.16.0.0/16,,0";
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
          allowedIPs = [ "10.8.0.0/24" ];
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

  # The work VPN is the only PPP connection configured on these hosts.
  environment.etc."ppp/options".text = "ifname mdr-vpn\n";

  # PPP/NetworkManager own the work routes; avoid IPsec gateway routes bypassing TUN.
  environment.etc."strongswan.conf".text = ''
    charon {
      install_routes = no
    }
  '';
  # NetworkManager-L2TP owns charon; a standalone daemon conflicts with it.
  systemd.tmpfiles.rules = [
    "d /etc/ipsec.d 0700 root root -"
    "d /etc/ppp 0700 root root -"
  ];

  systemd.services.NetworkManager-dispatcher = lib.mkIf (config.networking.hostName != "MDR024") {
    requires = [ "NetworkManager-ensure-profiles.service" ];
    after = [ "NetworkManager-ensure-profiles.service" ];
  };

  systemd.services.mdr-l2tp-connect = lib.mkIf (config.networking.hostName != "MDR024") {
    after = [ "NetworkManager-ensure-profiles.service" "network-online.target" "mihomo.service" ];
    requires = [ "NetworkManager-ensure-profiles.service" "mihomo.service" ];
    wants = [ "network-online.target" ];
    path = [ config.networking.networkmanager.package ];
    environment.LC_ALL = "C";
    serviceConfig = {
      Type = "oneshot";
      TimeoutStartSec = "45min";
      LoadCredential = "office-bssids:${config.sops.secrets.mdr-office-bssids.path}";
      ExecStart = "${pkgs.nushell}/bin/nu --no-config-file ${../files/mihomo/connect-vpn.nu} /run/mihomo/controller.sock %d/office-bssids";
      # Also restore the hostname when systemd interrupts a connection attempt.
      ExecStopPost = "-${config.networking.networkmanager.package}/bin/nmcli connection modify --temporary id mdr-l2tp +vpn.data gateway=rnd.vpn.madrigal.ru";
    };
  };

  systemd.timers.mdr-l2tp-connect = lib.mkIf (config.networking.hostName != "MDR024") {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "1min";
      OnUnitInactiveSec = "1min";
    };
  };

  boot.kernel.sysctl = {
    "net.ipv4.ip_default_ttl" = 65;
    "net.ipv6.conf.all.hop_limit" = 65;
  };

  services.openssh.enable = true;
}
