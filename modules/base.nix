{
  config,
  inputs,
  pkgs,
  system,
  ...
}:
{
  time.timeZone = "Europe/Moscow";

  i18n = {
    defaultLocale = "en_US.UTF-8";

    extraLocaleSettings = {
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
  };

  boot.supportedFilesystems = {
    btrfs = true;
    exfat = true;
    ext = true;
    ntfs = true;
    vfat = true;
    xfs = true;
  };

  environment.interactiveShellInit = ''
    alias cl=clear
    export PATH="$PATH:$HOME/.pub-cache/bin"
  '';

  nix = {
    package = pkgs.nixVersions.latest;

    settings = {
      experimental-features = [ "nix-command" "flakes" ];
      trusted-users = [ "root" "monadix" ];

      substituters = [
        "https://mirror.yandex.ru/nixos"
        "https://cache.nixos.org/"
      ];

      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      ];
    };
  };

  users.users = {
    monadix = {
      isNormalUser = true;
      description = "monadix";
      extraGroups = [
        "cdrom"
        "dialout"
        "docker"
        "lp"
        "networkmanager"
        "plugdev"
        "sys"
        "video"
        "wheel"
      ];
      hashedPasswordFile = config.sops.secrets.monadix-password.path;
      shell = pkgs.nushell;
    };

    root.hashedPasswordFile = config.sops.secrets.root-password.path;
  };

  nixpkgs.config = {
    allowUnfree = true;
    permittedInsecurePackages = [
      "electron-24.8.6"
      "electron-19.1.9"
    ];
  };

  system.stateVersion = "23.11";
}
