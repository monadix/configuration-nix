{
  description = "Chell's system flake";
  
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    nixpkgs-stable.url = "github:NixOS/nixpkgs/24.11";

    flatpaks.url = "github:gmodena/nix-flatpak";
  };
  
  outputs = { self, nixpkgs, nixpkgs-stable, flatpaks, ... }: 
  let
    commonModules = [ ./configuration.nix  flatpaks.nixosModules.nix-flatpak ];
    pkgsStableFor = system: nixpkgs-stable.legacyPackages."${system}";
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

      chell-thinkpad = nixpkgs.lib.nixosSystem rec {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-thinkpad.nix ] ++ commonModules;
      };

      chell-workstation = nixpkgs.lib.nixosSystem rec {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-workstation.nix ] ++ commonModules;
      };
    };
  };
}
