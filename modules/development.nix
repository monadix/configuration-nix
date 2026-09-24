{
  inputs,
  pkgs,
  system,
  ...
}:
{
  environment.systemPackages = with pkgs; [
    acpi
    android-tools
    inputs.c3c.packages.${system}.c3c
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

  programs = {
    neovim = {
      enable = true;
      defaultEditor = true;
      viAlias = true;
      vimAlias = true;
    };

    direnv.enable = true;
  };
}
