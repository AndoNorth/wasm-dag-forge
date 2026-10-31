{
  description = "wasm-dag-forge dev shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      devShells.${system}.default = pkgs.mkShellNoCC {
        packages =
          with pkgs;
          [
            kind
            ctlptl
            kubectl
            yq-go
            kubernetes-helm
            tilt
            just
            uv
            python313
            ruff
            yamllint
            shellcheck
            actionlint
            kubeconform
            nixfmt
          ]
          ++ pkgs.lib.optionals (builtins.pathExists ./nix/wasm-tools.nix) (
            import ./nix/wasm-tools.nix { inherit pkgs; }
          );

        shellHook = ''
          if [ -f scripts/env.sh ]; then source scripts/env.sh; fi
        '';
      };
    };
}
