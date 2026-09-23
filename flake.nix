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

    c3c = {
      url = "github:c3lang/c3c";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
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
    commonModules = [ 
      ./configuration.nix
      sops-nix.nixosModules.sops
    ];

    pkgsStable = nixpkgs-stable.legacyPackages."${system}";

    specialArgs = {
      inherit system pkgsStable inputs;
    };
  in 
  {

    nixosConfigurations = {
      conputer = nixpkgs.lib.nixosSystem rec {
        inherit system specialArgs;
        modules = [ ./devices/conputer.nix ] ++ commonModules;
      };
      
      naumbuk = nixpkgs.lib.nixosSystem rec {
        inherit system specialArgs;
        modules = [ ./devices/naumbuk.nix ] ++ commonModules;
      };

      carbom = nixpkgs.lib.nixosSystem {
        inherit system specialArgs;
        modules = [ ./devices/carbom ] ++ commonModules;
      };

      MDR024 = nixpkgs.lib.nixosSystem rec {
        inherit system specialArgs;
        modules = [ 
          ./devices/madrigoal
        ] ++ commonModules;
      };
    };
  };
}
