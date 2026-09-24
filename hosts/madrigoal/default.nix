{ 
  config,
  lib,
  inputs,
  pkgs,
  ...
}:
{
  imports = [ 
    inputs.disko.nixosModules.default
    ./disko.nix
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

  networking.useDHCP = lib.mkDefault true;
  
  networking.hostName = "MDR024";

  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  hardware = {
    enableRedistributableFirmware = true;

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
