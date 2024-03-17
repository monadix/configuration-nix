{ config, pkgs, ... }: { 
  networking = {
    networkmanager.enable = true;
    dhcpcd = {
      wait = "background";
      extraConfig = "noarp";
    };
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

  services.xserver = {
    enable = true;
    displayManager.defaultSession = "xsession";   
    displayManager.session = [
      {
        manage = "desktop";
	      name = "xsession";
        start = "exec ~/.xsession";
      }
    ];

    xkb = {
      layout = "us,ru";
      variant = ",";
    };
  };

  services.printing.enable = true;

  sound.enable = true;
  hardware.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    jack.enable = true;
  };

  services.xserver.libinput.enable = true;

  services.mysql = {
    enable = true;
    package = pkgs.mysql80;
    settings = {
      mysqld = {
        local-infile = true;
      };
      mysql = {
        local-infile = true;
      };
    };
  };

  environment.interactiveShellInit = ''
    alias cl=clear
    export PATH="$PATH:$HOME/.pub-cache/bin"
  '';

  nix.settings.experimental-features = ["nix-command" "flakes"];

  users.users.chell = {
    isNormalUser = true;
    description = "chell";
    extraGroups = [ "networkmanager" "wheel" "docker" "plugdev" ];
    hashedPassword = "$y$j9T$dvuZmpawy1e63KSJpnLSE1$IVAAzcmcisaRsfNRMDikox36MOyH.e/DVOcJZG0cvAB";

    packages = with pkgs; [
    ];
  };

  nixpkgs.config = {
    allowUnfree = true;
    allowBroken = true;
    permittedInsecurePackages = [
      "electron-24.8.6"
      "electron-19.1.9"
    ];
  };

  environment.systemPackages = with pkgs; [
    android-tools
    cabal-install
    dart
    file
    firebase-tools
    flutter
    gcc
    ghc
    glances
    go
    haskell-language-server
    home-manager
    jdk
    neofetch
    openvpn
    parted
    pciutils
    rustup
    stack
    usbutils
    unzip
    wget
  ];

  virtualisation.docker.enable = true;

  programs = {
    neovim = 
    let 
      toLua = str: "lua << EOF\n${str}\nEOF\n";
      toLuaFile = file: toLua (builtins.readFile file);
    in {
      enable = true;
      defaultEditor = true;
      configure.customRC = toLuaFile ./nvim/options.lua;

      viAlias = true;
      vimAlias = true;
    };

    direnv = {
      enable = true;
    };
  };

  services.openssh = {
    enable = true;
  };

  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ 80 443 ];
    allowedUDPPortRanges = [
      { from = 28800; to = 28802; }
    ];
  };

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "23.11";
  # Did you read the comment?

}
