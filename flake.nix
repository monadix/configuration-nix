{
  description = "Chell's system flake";
  
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    nixpkgs-stable.url = "github:NixOS/nixpkgs/25.11";

    flatpaks.url = "github:gmodena/nix-flatpak";

    c3c = {
      url = "github:c3lang/c3c";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  
  outputs = { self, nixpkgs, nixpkgs-stable, flatpaks, c3c, ... }: 
  let
    system = "x86_64-linux";
    commonModules = [ 
      ./configuration.nix
      flatpaks.nixosModules.nix-flatpak
      {
        _module.args = { c3c = c3c.packages.${system}.c3c; };
      }
    ];

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
