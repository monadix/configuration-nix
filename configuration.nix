# Edit this configuration file to define what should be installed on
# your system.  Help is available in the configuration.nix(5) man page
# and in the NixOS manual (accessible by running ‘nixos-help’).

{ config, pkgs, ... }: { 
  imports =
    [ # Include the results of the hardware scan.
      ./hardware-configuration.nix
    ];

  # Bootloader.
  #boot.loader.systemd-boot.enable = true;
  #boot.loader.efi.canTouchEfiVariables = true;

  boot = {
    loader = {
      systemd-boot.enable = false;
      grub = {
        enable = true;
        device = "nodev";
        useOSProber = false;
        efiSupport = true;
        efiInstallAsRemovable = true;
        extraGrubInstallArgs = [ "--disable-shim-lock" ];
      };
      efi = {
        canTouchEfiVariables = false;
        efiSysMountPoint = "/boot";
      };
    };
    extraModulePackages = with config.boot.kernelPackages; [
      rtl8821au
      v4l2loopback
    ];
    kernelModules = [
      "rtl8821au"
      "v4l2loopback"
    ];
  };
    
  networking = {
    hostName = "chell-nixos";
    networkmanager.enable = true;
    dhcpcd = {
      wait = "background";
      extraConfig = "noarp";
    };
    #wireless.enable = true; 
  };
  # Enables wireless support via wpa_supplicant.

  # Configure network proxy if necessary
  # networking.proxy.default = "http://user:password@proxy:port/";
  # networking.proxy.noProxy = "127.0.0.1,localhost,internal.domain";

  # Set your time zone.
  time.timeZone = "Europe/Moscow";

  # Select internationalisation properties.
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

  # Enable the X11 windowing system.
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

  # Enable the GNOME Desktop Environment.
#  services.xserver.displayManager.gdm.enable = true;
#  services.xserver.desktopManager.gnome.enable = true;

  # Enable CUPS to print documents.
  services.printing.enable = true;

  # Enable sound with pipewire.
  sound.enable = true;
  hardware.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    # If you want to use JACK applications, uncomment this
    #jack.enable = true;

    # use the example session manager (no others are packaged yet so this is enabled by default,
    # no need to redefine it in your config for now)
    #media-session.enable = true;
  };

  # Enable touchpad support (enabled default in most desktopManager).
  # services.xserver.libinput.enable = true;

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

  # Defin1e a user account. Don't forget to set a password with ‘passwd’.
  users.users.chell = {
    isNormalUser = true;
    description = "chell";
    extraGroups = [ "networkmanager" "wheel" "docker" "plugdev" ];
    hashedPassword = "$y$j9T$dvuZmpawy1e63KSJpnLSE1$IVAAzcmcisaRsfNRMDikox36MOyH.e/DVOcJZG0cvAB";
    initialHashedPassword = "$y$j9T$dvuZmpawy1e63KSJpnLSE1$IVAAzcmcisaRsfNRMDikox36MOyH.e/DVOcJZG0cvAB";   

    packages = with pkgs; [
    ];
  };

  # Allow unfree packages
  nixpkgs.config = {
    allowUnfree = true;
    allowBroken = true;
    permittedInsecurePackages = [
      "electron-24.8.6"
    ];
  };

  # List packages installed in system profile. To search, run:
  # $ nix search wget
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

  virtualisation.docker = {
    enable = true;
  };

  # Some programs need SUID wrappers, can be configured further or are
  # started in user sessions.
  # programs.mtr.enable = true;
  # programs.gnupg.agent = {
  #   enable = true;
  #   enableSSHSupport = true;
  # };

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
  };

  # List services that you want to enable:

  # Enable the OpenSSH daemon.
  # services.openssh.enable = true;

  # Open ports in the firewall.
  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ 80 443 ];
    allowedUDPPortRanges = [
      { from = 28800; to = 28802; }
    ];
    # allowedUDPPorts = [ 28800 28801 28802 ];
  };
  # Or disable the firewall altogether.
  # networking.firewall.enable = false;

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "23.11";
  # Did you read the comment?

}
