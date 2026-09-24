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
    commonModules = [
      (inputs.import-tree ./modules)
      sops-nix.nixosModules.sops
    ];

    pkgsStable = nixpkgs-stable.legacyPackages."${system}";

    specialArgs = {
      inherit system pkgsStable inputs;
    };

    mkHost = hostModule: nixpkgs.lib.nixosSystem {
      inherit system specialArgs;
      modules = [ hostModule ] ++ commonModules;
    };
  in 
  {

    nixosConfigurations = {
      conputer = mkHost ./hosts/conputer.nix;
      naumbuk = mkHost ./hosts/naumbuk.nix;
      carbom = mkHost ./hosts/carbom;
      MDR024 = mkHost ./hosts/madrigoal;
    };
  };
}
