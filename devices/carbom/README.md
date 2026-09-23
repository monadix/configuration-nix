# carbom (ThinkPad X1 Carbon Gen 9)

`nixosConfigurations.carbom` is independent of `naumbuk`. It uses UEFI GRUB, a
1 GiB unencrypted EFI system partition at `/boot`, and LUKS on the remaining
space with Btrfs subvolumes for `/`, `/home`, and `/nix`. It has no disk swap;
zram provides swap. LUKS discards are enabled, which permits SSD TRIM but can
reveal which encrypted blocks are unused.

## Before installation

- **Disko destroys the target disk.** `disko.nix` currently targets
  `/dev/nvme0n1`. Confirm the actual internal disk using
  `lsblk -o PATH,SIZE,MODEL,TYPE,MOUNTPOINTS` from the installer, and edit
  `disko.nix` if it differs. Do not mistake the installer USB for the SSD.
- Boot the installer in UEFI mode. Check that `/sys/firmware/efi` exists.
- The shared configuration uses SOPS for both user passwords and other
  secrets. The existing encrypted `secrets/secrets.yaml` does **not** yet
  include a decryption recipient for this new machine. Arrange its key before
  running `nixos-install`; a successful Nix evaluation alone does not check
  that the installed machine can decrypt secrets.

After partitioning and mounting at `/mnt`, create a distinct age identity for
this machine at `/mnt/var/lib/sops-nix/keys.txt` with mode `0600`, and obtain
its public recipient with `age-keygen -y`. Add that public recipient to the
repository's `.sops.yaml` and run `sops updatekeys secrets/secrets.yaml` on a
machine that can already decrypt the file. Make the updated encrypted file
available to the installer before running `nixos-install --flake .#carbom`.
Keep the private age key out of Git. Its persistent path on the installed
machine is `/var/lib/sops-nix/keys.txt`.

The existing 1 GiB EFI/LUKS/Btrfs layout under `devices/madrigoal` was used as
the starting point for this separate host. Do not import `naumbuk.nix` here:
that machine has an AMD CPU and its own disk UUIDs.
