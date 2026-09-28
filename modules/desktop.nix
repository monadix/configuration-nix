{
  config,
  inputs,
  pkgs,
  ...
}:
let
  nord = {
    nord0 = "2e3440";
    nord1 = "3b4252";
    nord2 = "434c5e";
    nord3 = "4c566a";
    nord4 = "d8dee9";
    nord5 = "e5e9f0";
    nord6 = "eceff4";
    nord7 = "8fbcbb";
    nord8 = "88c0d0";
    nord9 = "81a1c1";
    nord10 = "5e81ac";
    nord11 = "bf616a";
    nord12 = "d08770";
    nord13 = "ebcb8b";
    nord14 = "a3be8c";
    nord15 = "b48ead";
  };
in
{
  boot.loader.grub.splashImage = "${inputs.assets.images}/nixos-nord-dark.png";

  # Tuigreet uses the virtual console's 16-color palette.
  console.colors = with nord; [
    nord0 nord11 nord14 nord13
    nord9 nord15 nord8 nord5
    nord3 nord11 nord14 nord13
    nord9 nord15 nord7 nord6
  ];

  services.xserver = {
    enable = true;

    displayManager.startx.enable = true;

    xkb = {
      layout = "us,ru";
      variant = ",";
    };
  };

  services.greetd = {
    enable = true;
    useTextGreeter = true;
    settings.default_session.command =
      "${pkgs.tuigreet}/bin/tuigreet --time --remember --theme 'border=blue;text=white;time=cyan;container=black;title=bright-blue;greet=white;prompt=cyan;input=white;action=blue;button=bright-cyan' --cmd '${pkgs.xinit}/bin/startx ${config.services.displayManager.sessionData.wrapper}'";
  };

  services.printing.enable = true;

  security = {
    rtkit.enable = true;
    pam.services.xscreensaver.enable = true;
    polkit.enable = true;
  };

  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    jack.enable = true;

    extraConfig.pipewire."99-silent-bell".context.properties = {
      "module.x11.bell" = false;
    };
  };

  services.libinput.enable = true;

  programs = {
    cdemu.enable = true;
    dconf.enable = true;
    fuse.enable = true;
  };

  xdg.portal = {
    enable = true;
    extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
    config.common.default = "*";
  };
}
