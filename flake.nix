{
  description = "Chell's system flake";
  
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    nixpkgs-stable.url = "github:NixOS/nixpkgs/26.05";

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    c3c = {
      url = "github:c3lang/c3c";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  
  outputs = { 
    self,
    nixpkgs,
    nixpkgs-stable,
    sops-nix,
    c3c,
    disko,
    ... 
  }: 
  let
    system = "x86_64-linux";
    commonModules = [ 
      ./configuration.nix
      sops-nix.nixosModules.sops
      {
        _module.args = { c3c = c3c.packages.${system}.c3c; };
      }
    ];

    pkgsStableFor = system: nixpkgs-stable.legacyPackages."${system}";
  in 
  {

    nixosConfigurations = {
      chell-nixos = nixpkgs.lib.nixosSystem rec {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-nixos.nix ] ++ commonModules;
        specialArgs = { pkgsStable = pkgsStableFor system; };
      };
      
      chell-ssd = nixpkgs.lib.nixosSystem rec {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-ssd.nix ] ++ commonModules;
        specialArgs = { pkgsStable = pkgsStableFor system; };
      };

      chell-thinkpad = nixpkgs.lib.nixosSystem rec {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-thinkpad.nix ] ++ commonModules;
        specialArgs = { pkgsStable = pkgsStableFor system; };
      };

      chell-workstation = nixpkgs.lib.nixosSystem rec {
        system = "x86_64-linux";
        modules = [ ./hardware-specific/chell-workstation.nix ./hardware-specific/chell-workstation-disko.nix disko.nixosModules.disko ] ++ commonModules;
        specialArgs = { pkgsStable = pkgsStableFor system; };
      };
    };
  };
}
