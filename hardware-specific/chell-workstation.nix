{ config, lib, pkgs, modulesPath, ... }:
{
  imports =
    [ (modulesPath + "/installer/scan/not-detected.nix")
    ];

  boot = {
    initrd = {
      availableKernelModules = [ 
        "xhci_pci"
        "ahci"
        "usb_storage"
        "usbhid"
        "sd_mod"
        "rtsx_pci_sdmmc"
      ];
      kernelModules = [ ];

      luks.devices."cryptroot".device = "/dev/disk/by-uuid/bd2a93e8-785b-4665-96a9-fe8d298c18fa";
    };

    kernelModules = [ 
      "kvm-intel" 
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
        extraGrubInstallArgs = [ "--disable-shim-lock" ];
      };

      efi = {
        canTouchEfiVariables = false;
        efiSysMountPoint = "/boot";
      };
    };
  };

  fileSystems."/" =
    { device = "/dev/mapper/cryptroot";
      fsType = "ext4";
    };

  fileSystems."/boot" =
    { device = "/dev/disk/by-uuid/91BC-482A";
      fsType = "vfat";
    };

  swapDevices = [ ];

  networking.useDHCP = lib.mkDefault true;
  # networking.interfaces.eno2.useDHCP = lib.mkDefault true;
  # networking.interfaces.enp0s20f0u6.useDHCP = lib.mkDefault true;
  # networking.interfaces.wlo1.useDHCP = lib.mkDefault true;
  
  networking.hostName = "MDR024";

  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  hardware = {
    cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
    graphics = {
      enable = true;
      enable32Bit = true;

      extraPackages = with pkgs; [
        intel-media-driver
	      libvdpau-va-gl
      ];

      extraPackages32 = with pkgs.driversi686Linux; [
        intel-media-driver
	      libvdpau-va-gl
      ];
    };
    bluetooth.enable = true;
  };

  services.blueman.enable = true;

  zramSwap = {
    enable = true;
    algorithm = "zstd";
  };
}
