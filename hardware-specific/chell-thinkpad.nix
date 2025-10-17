{ config
, lib
, pkgs
, modulesPath
, 
... }:
{
  imports =
    [ (modulesPath + "/installer/scan/not-detected.nix")
    ];

  boot = {
    initrd.availableKernelModules = [ "xhci_pci" "ahci" "usb_storage" "usbhid" "sd_mod" "rtsx_pci_sdmmc" ];
    initrd.kernelModules = [ ];

    kernelModules = [ 
      "kvm-amd" 
      "v4l2loopback"
    ];
    extraModulePackages = with config.boot.kernelPackages; [
      v4l2loopback
    ];
    
    loader = {
      grub = {
        enable = true;
        device = "nodev";

        useOSProber = false;
        efiSupport = true;

        configurationLimit = 20;

        efiInstallAsRemovable = true;
      };

      efi = {
        canTouchEfiVariables = false;
        efiSysMountPoint = "/boot";
      };
    };
  };

  fileSystems."/" =
    { device = "/dev/disk/by-uuid/3367cdcc-c53e-411b-ae94-c137b936d672";
      fsType = "ext4";
    };

  fileSystems."/boot" =
    { device = "/dev/disk/by-uuid/CA2A-83FA";
      fsType = "vfat";
      options = [ "fmask=0022" "dmask=0022" ];
    };

  swapDevices = [ ];

  networking.useDHCP = lib.mkDefault true;
  # networking.interfaces.eno2.useDHCP = lib.mkDefault true;
  # networking.interfaces.enp0s20f0u6.useDHCP = lib.mkDefault true;
  # networking.interfaces.wlo1.useDHCP = lib.mkDefault true;
  
  networking.hostName = "chell-thinkpad";

  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  hardware = {
    cpu.amd.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;

    graphics = { 
      enable = true;
      enable32Bit = true;

      extraPackages = with pkgs; [
	      libvdpau-va-gl
      ];

      extraPackages32 = with pkgs.driversi686Linux; [
	      libvdpau-va-gl
      ];
    };

    bluetooth.enable = true;
  };

  services.xserver.config = ''
    Section "InputClass"
      Identifier      "Thinkpad trackpoint setup"
      MatchProduct    "ETPS/2 Elantech TrackPoint"
      MatchDevicePath "/dev/input/event*"
      Option          "AccelSpeed" "1"
    EndSection
  '';

  services.blueman.enable = true;

  services.tlp = {
    enable = true;
    settings = {
      RUNTIME_PM_DRIVER_DENYLIST = "mei_me nouveau radeon xhci_hcd";
    };
  };

  services.fprintd = {
    enable = true;
    tod = {
      enable = true;
      driver = pkgs.libfprint-2-tod1-goodix;
    };
  };

  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="backlight", KERNEL=="amdgpu_bl1", RUN+="${pkgs.coreutils}/bin/chgrp video /sys/class/backlight/amdgpu_bl1/brightness"
    ACTION=="add", SUBSYSTEM=="backlight", KERNEL=="amdgpu_bl1", RUN+="${pkgs.coreutils}/bin/chmod g+w /sys/class/backlight/amdgpu_bl1/brightness"
  '';

  zramSwap = {
    enable = true;
    algorithm = "zstd";
  };
}
