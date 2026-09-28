{
  config,
  inputs,
  pkgs,
  ...
}:
{
  boot.loader.grub.splashImage = "${inputs.assets.images}/nixos-nord-dark.png";

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
      "${pkgs.tuigreet}/bin/tuigreet --time --remember --cmd '${pkgs.xinit}/bin/startx ${config.services.displayManager.sessionData.wrapper}'";
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
