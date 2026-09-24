{
  inputs,
  pkgs,
  pkgsStable,
  ...
}:
{
  boot.loader.grub.splashImage = "${inputs.assets.images}/nixos-nord-dark.png";

  services.xserver = {
    enable = true;

    displayManager = {
      session = [
        {
          manage = "desktop";
          name = "xsession";
          start = "exec ~/.xsession";
        }
      ];

      lightdm = {
        enable = true;
        background = "${inputs.assets.images}/nixos-nord-dark.png";

        greeters.gtk = {
          enable = true;

          theme = {
            name = "Nordic";
            package = pkgsStable.nordic;
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

  services.displayManager.defaultSession = "xsession";
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
