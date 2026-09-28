{
  description = "Chell's system flake";
  
  inputs = {
    assets.url = "github:monadix/assets";

    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    nixpkgs-stable.url = "github:NixOS/nixpkgs/26.05";

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-config = {
      url = "github:monadix/home-manager-config";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
      inputs.sops-nix.follows = "sops-nix";
    };

    c3c = {
      url = "github:c3lang/c3c";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    import-tree.url = "github:denful/import-tree";
  };
  
  outputs = inputs @ { 
    self,
    nixpkgs,
    nixpkgs-stable,
    sops-nix,
    ... 
  }: 
  let
    system = "x86_64-linux";
    pkgsStable = nixpkgs-stable.legacyPackages."${system}";

    specialArgs = {
      inherit system pkgsStable inputs;
    };

    mkHost = name: hostModule: nixpkgs.lib.nixosSystem {
      inherit system specialArgs;
      modules = [
        (inputs.import-tree ./common)
        sops-nix.nixosModules.sops
        inputs.home-manager.nixosModules.home-manager
        hostModule
        {
          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            users.monadix = inputs.home-config.homeModules.${name};
          };
        }
      ];
    };

    hosts = {
      conputer = ./hosts/conputer.nix;
      naumbuk = ./hosts/naumbuk.nix;
      ugly-rod = ./hosts/ugly-rod;
      MDR024 = ./hosts/madrigoal;
    };
  in
  {
    nixosConfigurations = builtins.mapAttrs mkHost hosts;
  };
}
