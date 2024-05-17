{
  description = "Chell's system flake";
  
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };
  
  outputs = { self, nixpkgs }: {

    nixosConfigurations = {
      chell-nixos = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ./hardware-specific/chell-nixos.nix ];
      };
      
      chell-ssd = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ./hardware-specific/chell-ssd.nix ];
      };

      chell-thinkpad = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ./hardware-specific/chell-thinkpad.nix ];
      };
    };
  };
}
