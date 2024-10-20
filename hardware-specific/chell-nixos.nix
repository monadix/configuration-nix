{ config, lib, pkgs, modulesPath, ... }:

{
  imports =
    [ (modulesPath + "/installer/scan/not-detected.nix")
    ];

  boot = {
    initrd.availableKernelModules = [ "xhci_pci" "ehci_pci" "ahci" "nvme" "usb_storage" "usbhid" "sd_mod" ];
    initrd.kernelModules = [ ];

    extraModulePackages = with config.boot.kernelPackages; [
      rtl8821au
      v4l2loopback
    ];
    kernelModules = [
      "rtl8821au"
      "v4l2loopback"
      "kvm-intel"
    ];

    loader = {
      grub = {
        enable = true;
        device = "nodev";

        useOSProber = false;
        efiSupport = true;
        efiInstallAsRemovable = true;
        extraGrubInstallArgs = [ "--disable-shim-lock" ];

        configurationLimit = 20;

        extraEntries = ''
          menuentry "Windus" {
            insmod part_gpt
            insmod chain
            chainloader /EFI/Microsoft/Boot/bootmgfw.efi
          }
          menuentry "Arch" {
            linux /vmlinuz-linux root=/dev/nvme0n1p3 rw
            initrd /intel-ucode.img /initramfs-linux.img
          }
        '';
      };
      
      efi = {
        canTouchEfiVariables = false;
        efiSysMountPoint = "/boot";
      };
    };
  };

  fileSystems."/" =
    { device = "/dev/disk/by-uuid/800c4a3c-50de-4967-9007-0cb4b944b6a2";
      fsType = "btrfs";
      options = [ "subvol=root" "compress=zstd" ];
    };

  fileSystems."/home" =
    { device = "/dev/disk/by-uuid/800c4a3c-50de-4967-9007-0cb4b944b6a2";
      fsType = "btrfs";
      options = [ "subvol=home" "compress=zstd" ];
    };

  fileSystems."/nix" =
    { device = "/dev/disk/by-uuid/800c4a3c-50de-4967-9007-0cb4b944b6a2";
      fsType = "btrfs";
      options = [ "subvol=nix" "compress=zstd" "noatime" ];
    };

  fileSystems."/boot" =
    { device = "/dev/disk/by-uuid/D24D-EE08";
      fsType = "vfat";
    };

  swapDevices = [ ];

  networking.useDHCP = lib.mkDefault true;
  # networking.interfaces.enp0s20u4u1.useDHCP = lib.mkDefault true;
  # networking.interfaces.enp5s0.useDHCP = lib.mkDefault true;
  
  networking.hostName = "chell-nixos";

  services.xserver.videoDrivers = [ "nvidia" ];

  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  hardware = {
    cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
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
      modesetting.enable = true;
      powerManagement = {
        enable = false;
	      finegrained = false;
      };
      open = false;
      nvidiaSettings = true;
    };
  };

  zramSwap = {
    enable = true;
    algorithm = "zstd";
  };

  virtualisation.docker.storageDriver = "btrfs";
}
