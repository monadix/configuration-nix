{ config, lib, inputs, pkgs, ... }:
{
  imports = [
    inputs.disko.nixosModules.default
    ./disko.nix
  ];

  networking.hostName = "carbom";
  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";

  boot = {
    initrd.availableKernelModules = [ "xhci_pci" "nvme" "usb_storage" "usbhid" "sd_mod" ];
    kernelModules = [ "kvm-intel" ];

    loader = {
      grub = {
        enable = true;
        device = "nodev";
        efiSupport = true;
        efiInstallAsRemovable = true;
        useOSProber = false;
        configurationLimit = 20;
      };
      efi = {
        canTouchEfiVariables = false;
        efiSysMountPoint = "/boot";
      };
    };
  };

  hardware = {
    enableRedistributableFirmware = true;
    cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
    graphics = {
      enable = true;
      enable32Bit = true;
      extraPackages = [ pkgs.intel-media-driver ];
    };
    bluetooth.enable = true;
  };

  services.blueman.enable = true;
  services.tlp.enable = true;
  services.fwupd.enable = true;

  zramSwap = {
    enable = true;
    algorithm = "zstd";
  };

  # The shared module targets the oldest machine; this is a fresh install.
  system.stateVersion = lib.mkForce "26.05";
}
