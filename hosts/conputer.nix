{ config, lib, pkgs, modulesPath, ... }:

{
  boot = {
    initrd.availableKernelModules = [ "xhci_pci" "ehci_pci" "ahci" "nvme" "usb_storage" "usbhid" "sd_mod" ];
    initrd.kernelModules = [ ];

    extraModulePackages = with config.boot.kernelPackages; [
      v4l2loopback
    ];
    kernelModules = [
      "v4l2loopback"
      "kvm-intel"
    ];

    loader = {
      grub = {
        enable = true;
        efiSupport = true;
        useOSProber = false;

        device = "nodev";

        extraGrubInstallArgs = [ "--disable-shim-lock" ];

        configurationLimit = 20;
      };
      
      efi = {
        canTouchEfiVariables = true;
        efiSysMountPoint = "/boot/efi";
      };
    };
  };

  fileSystems."/" =
    { device = "/dev/disk/by-uuid/c307b4eb-3806-469b-8407-31ed63117f93";
      fsType = "btrfs";
      options = [ "subvol=root" "compress=zstd" ];
    };

  fileSystems."/home" =
    { device = "/dev/disk/by-uuid/c307b4eb-3806-469b-8407-31ed63117f93";
      fsType = "btrfs";
      options = [ "subvol=home" "compress=zstd" ];
    };

  fileSystems."/nix" =
    { device = "/dev/disk/by-uuid/c307b4eb-3806-469b-8407-31ed63117f93";
      fsType = "btrfs";
      options = [ "subvol=nix" "compress=zstd" "noatime" ];
    };

  fileSystems."/boot/efi" =
    { device = "/dev/disk/by-uuid/5464-CF3B";
      fsType = "vfat";
    };

  swapDevices = [ ];

  networking.useDHCP = lib.mkDefault true;
  
  networking.hostName = "conputer";

  services.xserver.videoDrivers = [ "nvidia" ];

  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  hardware = {
    enableRedistributableFirmware = true;

    cpu.intel.updateMicrocode = true;
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
    
    nvidia = {
      package = config.boot.kernelPackages.nvidiaPackages.legacy_580;
      modesetting.enable = true;
      powerManagement = {
        enable = false;
	      finegrained = false;
      };
      open = false;
      nvidiaSettings = true;
    };

    bluetooth.enable = true;
  };

  zramSwap = {
    enable = true;
    algorithm = "zstd";
  };

  virtualisation.docker.storageDriver = "btrfs";
}
