{ 
  config,
  pkgs,

  sops-nix,

  c3c,

  ... 
}: 
{ 
  networking = {
    networkmanager.enable = true;
    dhcpcd = {
      wait = "background";
      extraConfig = "noarp";
    };
  };
  
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

  users.users.chell = {
    isNormalUser = true;
    description = "chell";
    extraGroups = [ "networkmanager" "wheel" "docker" "plugdev" "dialout" "sys" "lp" "video" ];
    hashedPassword = "$y$j9T$dvuZmpawy1e63KSJpnLSE1$IVAAzcmcisaRsfNRMDikox36MOyH.e/DVOcJZG0cvAB";
    
    shell = pkgs.nushell;
  };

  nixpkgs.config = {
    allowUnfree = true;
    permittedInsecurePackages = [
      "electron-24.8.6"
      "electron-19.1.9"
    ];
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

    secrets.mdr-wg-private-key = {};
  };

  networking.wg-quick.interfaces = {
    mdr = {
      autostart = false;

      address = [ "192.168.78.25/32" ];
      listenPort = 51820;

      privateKeyFile = config.sops.secrets.mdr-wg-private-key.path;

      dns = [ "172.16.0.101" ];

      peers = [
        {
          publicKey = "4E0z2Zo4TvhtEPnC7gWcFlG6vpPR/aRJEKS8uFg2nFg=";

          allowedIPs = [ "172.16.16.0/20" "172.16.0.0/22" "172.16.32.0/22" ];

          endpoint = "213.138.72.10:13232";

          persistentKeepalive = 5;
        }
      ];
    };
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
