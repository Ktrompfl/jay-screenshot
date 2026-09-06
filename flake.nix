{
  description = "grim screenshot helper for jay";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    jay = {
      url = "github:mahkoh/jay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      jay,
    }:
    let
      inherit (nixpkgs) lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      jayFor = pkgs: jay.packages.${pkgs.stdenv.hostPlatform.system}.jay;
    in
    {
      overlays.default = final: _prev: {
        jay-screenshot = final.callPackage ./package.nix { jay = jayFor final; };
      };

      packages = forAllSystems (pkgs: rec {
        jay-screenshot = pkgs.callPackage ./package.nix { jay = jayFor pkgs; };
        default = jay-screenshot;
      });

      apps = forAllSystems (pkgs: rec {
        jay-screenshot = {
          type = "app";
          program = lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.jay-screenshot;
          meta = {
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}.jay-screenshot.meta) description;
          };
        };
        default = jay-screenshot;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.click ]))
            pkgs.ruff
            pkgs.grim
            pkgs.libnotify
            pkgs.slurp
            pkgs.wl-clipboard
            (jayFor pkgs)
          ];
        };
      });

      checks = forAllSystems (pkgs: {
        inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) jay-screenshot;
        devShell = self.devShells.${pkgs.stdenv.hostPlatform.system}.default;
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
