{
  description = "Chell's system flake";
  
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    flatpaks.url = "github:gmodena/nix-flatpak";
  };
  
  outputs = { self, nixpkgs, flatpaks, ... }: 
  let
    commonModules = [ ./configuration.nix  flatpaks.nixosModules.nix-flatpak ];
  in 
  {

    nixosConfigurations = {
      chell-nixos = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-nixos.nix ] ++ commonModules;
      };
      
      chell-ssd = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-ssd.nix ] ++ commonModules;
      };

      chell-thinkpad = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-thinkpad.nix ] ++ commonModules;
      };
    };
  };
}
