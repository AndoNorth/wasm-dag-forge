{
  description = "wasm-dag-forge dev shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      git-hooks,
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # One hook config serves the local hooks and `checks.pre-commit`, so the two cannot drift.
      # Each hook carries its tool by store path, so a commit made outside the shell still works.
      hooks = git-hooks.lib.${system}.run {
        src = ./.;
        package = pkgs.prek;
        # scripts/hooks/install.sh installs the shims, because the bundled installer rewrites core.hooksPath.
        install.enable = false;
        # The lint fixtures are bad on purpose, the contract vectors are byte-exact, and draw.io generates the diagrams.
        excludes = [
          "^scripts/fixtures/lint-bad/"
          "^contracts/.*/vectors/"
          "^docs/architecture/.*\\.drawio(\\.svg)?$"
        ];
        hooks = {
          trim-trailing-whitespace.enable = true;
          end-of-file-fixer.enable = true;
          check-merge-conflicts = {
            enable = true;
            # Without this it only looks while a merge is in progress.
            args = [ "--assume-in-merge" ];
          };
          check-added-large-files.enable = true;

          ruff-format.enable = true;
          ruff.enable = true;
          yamllint.enable = true;
          shellcheck.enable = true;
          actionlint.enable = true;
          nixfmt.enable = true;

          # The fast gate; the full `just ci` stays the CI gate.
          pre-push-gate = {
            enable = true;
            name = "fast gate";
            entry = "scripts/hooks/in-shell.sh just pre-push";
            language = "system";
            stages = [ "pre-push" ];
            pass_filenames = false;
            always_run = true;
            require_serial = true;
          };
        };
      };
    in
    {
      checks.${system}.pre-commit = hooks;

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
            prek
          ]
          ++ pkgs.lib.optionals (builtins.pathExists ./nix/wasm-tools.nix) (
            import ./nix/wasm-tools.nix { inherit pkgs; }
          );

        shellHook = ''
          if [ -f scripts/env.sh ]; then source scripts/env.sh; fi
          if top=$(git rev-parse --show-toplevel 2>/dev/null) && [ -x "$top/scripts/hooks/install.sh" ]; then
            "$top/scripts/hooks/install.sh" "$top" ${hooks.config.configFile}
          fi
        '';
      };
    };
}
