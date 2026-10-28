{ config, ... }:
{
  sops = {
    defaultSopsFile = ../secrets/secrets.yaml;

    age = {
      sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
      keyFile = "/var/lib/sops-nix/keys.txt";
      generateKey = true;
    };

    secrets = {
      monadix-password.neededForUsers = true;
      root-password.neededForUsers = true;

      mdr-l2tp-psk = {
        group = "networkmanager";
        mode = "0440";
      };

      mdr-l2tp-user = {
        group = "networkmanager";
        mode = "0440";
      };

      mdr-l2tp-pass = {
        group = "networkmanager";
        mode = "0440";
      };

      lena727-wg-private-key = { };
      lena727-wg-preshared-key = { };
    };

    templates.mdr-l2tp-env = {
      group = "networkmanager";
      mode = "0440";
      content = ''
        MDR_L2TP_PSK=${config.sops.placeholder.mdr-l2tp-psk}
        MDR_L2TP_USER=${config.sops.placeholder.mdr-l2tp-user}
        MDR_L2TP_PASS=${config.sops.placeholder.mdr-l2tp-pass}
      '';
    };
  };
}
