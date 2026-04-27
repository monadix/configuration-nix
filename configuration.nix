{ 
  config,
  pkgs,

  sops-nix,

  c3c,

  ... 
}: 
{ 
  networking = {
    firewall.checkReversePath = "loose";

    nftables.enable = true;

    networkmanager = {
      enable = true;

      plugins = with pkgs; [
        networkmanager-l2tp
        networkmanager-strongswan
      ];
    };

    dhcpcd = {
      wait = "background";
      extraConfig = "noarp";
    };
  };

  environment.etc."strongswan.conf" = {
    text = '''';
  };
  services.strongswan.enable = true;

  boot.kernel.sysctl = {
    "net.ipv4.ip_default_ttl" = 65;
    "net.ipv6.conf.all.hop_limit" = 65;
  };

  boot.loader.grub = {
    splashImage = ./wallpapers/nixos-nord-dark.png;
  };

  time.timeZone = "Europe/Moscow";

  i18n.defaultLocale = "en_US.UTF-8";

  i18n.extraLocaleSettings = {
    LC_ADDRESS = "ru_RU.UTF-8";
    LC_IDENTIFICATION = "ru_RU.UTF-8";
    LC_MEASUREMENT = "ru_RU.UTF-8";
    LC_MONETARY = "ru_RU.UTF-8";
    LC_NAME = "ru_RU.UTF-8";
    LC_NUMERIC = "ru_RU.UTF-8";
    LC_PAPER = "ru_RU.UTF-8";
    LC_TELEPHONE = "ru_RU.UTF-8";
    LC_TIME = "ru_RU.UTF-8";
  };

  boot.supportedFilesystems = {
    btrfs = true;
    exfat = true;
    ext = true;
    ntfs = true;
    vfat = true;
    xfs = true;
  };

  services.xserver = {
    enable = true;
    displayManager = {
      session = [{
        manage = "desktop";
        name = "xsession";
        start = "exec ~/.xsession";
      }];

      lightdm = {
        enable = true;
        background = ./wallpapers/nixos-nord-dark.png;
        greeters.gtk = {
          enable = true;

          theme = {
            name = "Nordic";
            package = pkgs.nordic;
          };
          iconTheme = {
            name = "Nordzy";
            package = pkgs.nordzy-icon-theme;
          };
          cursorTheme = {
            package = pkgs.nordzy-cursor-theme;
            name = "Nordzy-cursors";
            size = 32;
          };
        };
      };
    };

    xkb = {
      layout = "us,ru";
      variant = ",";
    };
  };

  services.displayManager = {
    defaultSession = "xsession";   
  };

  services.printing.enable = true;

  security.rtkit.enable = true;
  security.pam.services.xscreensaver.enable = true;

  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    jack.enable = true;

    extraConfig.pipewire = {
      "99-silent-bell" = {
        "context.properties" = {
          "module.x11.bell" = false;
        };
      };
    };
  };

  services.libinput.enable = true;

  environment.interactiveShellInit = ''
    alias cl=clear
    export PATH="$PATH:$HOME/.pub-cache/bin"
  '';

  nix = {
    package = pkgs.nixVersions.latest;

    settings = {
      experimental-features = ["nix-command" "flakes"];
      
      substituters = [
        "https://mirror.yandex.ru/nixos"
        "https://cache.nixos.org/"
      ];

      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      ];
    };
  };

  sops.secrets.chell-password = {
    neededForUsers = true;
  };

  users.users.chell = {
    isNormalUser = true;
    description = "chell";
    extraGroups = [ "networkmanager" "wheel" "docker" "plugdev" "dialout" "sys" "lp" "video" ];
    hashedPasswordFile = config.sops.secrets.chell-password.path;
    
    shell = pkgs.nushell;
  };

  nixpkgs = {
    config = {
      allowUnfree = true;
      permittedInsecurePackages = [
        "electron-24.8.6"
        "electron-19.1.9"
      ];
    };

    #overlays = [
    #  (final: prev: {
    #    strongswan = prev.strongswan.override { enableNetworkManager = true; };
    #  })
    #];
  };

  environment.systemPackages = with pkgs; [
    acpi
    android-tools
    c3c
    cabal-install
    dart
    dive
    dmidecode
    docker-compose
    file
    flutter
    gcc
    ghc
    glances
    go
    haskell-language-server
    home-manager
    inxi
    jdk
    lshw
    fastfetch
    openvpn
    parted
    pciutils
    podman-tui
    rustup
    screentest
    stack
    steam-run
    upower
    usbutils
    unzip
    wget
  ];

  sops = {
    defaultSopsFile = ./secrets/secrets.yaml;

    age = {
      sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];

      keyFile = "/var/lib/sops-nix/keys.txt";
      generateKey = true;
    };
  };

  sops = {
    secrets = {
      mdr-l2tp-psk = {
        group = "networkmanager";
        mode = "0440";
      };
      mdr-l2tp-user = {
        group = "networkmanager";
        mode = "0440";
      };
      mdr-l2tp-pass = {
        group = "networkmanager";
        mode = "0440";
      };
    };

    templates.mdr-l2tp-env = {
      group = "networkmanager";
      mode = "0440";
      content = ''
        MDR_L2TP_PSK=${config.sops.placeholder.mdr-l2tp-psk}
        MDR_L2TP_USER=${config.sops.placeholder.mdr-l2tp-user}
        MDR_L2TP_PASS=${config.sops.placeholder.mdr-l2tp-pass}
      '';
    };
  };

  networking.networkmanager.ensureProfiles = {
    environmentFiles = [ config.sops.templates.mdr-l2tp-env.path ];
    profiles = {
      mdr-l2tp = {
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

        vpn-secrets = {
          password = "$MDR_L2TP_PASS";
        };

        ipv4 = {
          method = "auto";

          never-default = "true";

          ignore-auto-routes = "true";
          ignore-auto-dns = "false";

          route1 = "172.16.0.100/32,,0";
          route2 = "172.16.0.101/32,,0";
          route3 = "172.16.20.2/32,,0";

          dns = "172.16.0.101";
          dns-search = "internal.madrigal.ru";
        };
        
        ipv6.method = "disabled";
      };
    };
  };

  programs.clash-verge = {
    enable = true;
    serviceMode = true;
    tunMode = true;
  };

  networking.firewall = {
    trustedInterfaces = [ "Mihomo" ];
    extraReversePathFilterRules = ''
      iifname { "Mihomo" } accept comment "trusted interface"
    '';
  };

  virtualisation.containers.enable = true;

  virtualisation.docker.enable = true;

  virtualisation.podman = {
    enable = true;

    defaultNetwork.settings.dns_enabled = true;
  };

  programs = {
    dconf.enable = true;

    neovim = { 
      enable = true;
      defaultEditor = true;

      viAlias = true;
      vimAlias = true;
    };

    direnv = {
      enable = true;
    };
  };

  xdg.portal = {
    enable = true;
    extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
    config = {
      common.default = "*";
    };
  };

  services.zapret = {
    enable = true;
    configureFirewall = false;

    whitelist = [
      # hehe
      "internal.madrigal.ru"

      "rutracker.org"
      "rutracker.cc"

      ## YouTube
      "youtube.com"
      "googlevideo.com"
      "ytimg.com"
      "youtu.be"

      ## Discord
      # Core Discord Domains
      "discord.com"
      "discordapp.com"
      "discord.gg"
      "dis.gd"
      "discord.co"
      "discordapp.net"
      # CDN Domains
      "cdn.discordapp.com"
      "media.discordapp.net"
      # Major CDN Providers
      "cloudflare.com"
      "amazonaws.com"
      "cloudfront.net"
      # Voice and Media
      "discord.media"
      # API Related (Likely)
      "*.discord.com"
      "*.discordapp.com"
      # Other Official Domains
      "discord.new"
      "discord.gift"
      "discord.gifts"
      "discordstatus.com"
      "discord.design"
      "discord.dev"
      "discord.store"
      "discord.tools"
      "discordpartygames.com"
      "discord-activities.com"
      "discordactivities.com"
      "discordsays.com"
      "discordmerch.com"
      "discordsez.com"
      "*.discord.fr"
      # Other CDN Providers (Consider)
      "*.akamai.com"
      "*.fastly.com"
      "*.googleusercontent.com"
    ];

    params = [
      "--dpi-desync=multidisorder"
    ];
  };

  services.openssh = {
    enable = true;
  };

  services.tor = {
    enable = true;
    openFirewall = true;
    client.enable = true;
    torsocks.enable = true;

    settings = {
      UseBridges = true;
      ClientTransportPlugin = "obfs4 exec ${pkgs.obfs4}/bin/lyrebird";
      Bridge = [
        "obfs4 141.94.213.29:34975 3CCF6211A6115BA32224E939C179AC2F8269E186 cert=xFW3DRM458PUIuWgi74qRg8IG7JElyFJIYgy9+V+flBQuvfKKonuPJb373QooLLR+eIqMg iat-mode=0"
        "obfs4 57.128.35.251:17275 3411026D454DF9F94BB263BCED2CFBECEBBAAF4C cert=nDA6sKSsjz28b1XZVZwZcx2dBbRSdYFZR5yvenhtFKQA7zYY6N2otTaa562gZViSNSW9Kg iat-mode=0"
      ];
    };
  };

  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ 80 443 25565 ];
    allowedUDPPorts = [ 51820 ];
    allowedUDPPortRanges = [
      { from = 28800; to = 28802; }
      { from = 25565; to = 25566; }
    ];
  };

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It's perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "23.11";
  # Did you read the comment?

}
