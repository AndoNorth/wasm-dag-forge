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

      # Each pre-push check runs only when the pushed commits changed a file that concerns it, so a push that
      # touches no code costs almost nothing. The full `just ci` stays the CI gate.
      pushChecks = {
        ruff = ''\.py$|^ruff\.toml$'';
        yamllint = ''\.ya?ml$|^\.yamllint$'';
        shellcheck = ''\.sh$|^\.shellcheckrc$'';
        actionlint = ''^\.github/workflows/'';
        kubeconform = ''^deploy/.*\.ya?ml$'';
        nixfmt = ''\.nix$'';
        verify-lint = ''^\.claude/skills/'';
        contracts-map = ''^(contracts/[^/]+/|contracts/[^/]+\.json$|docs/contracts\.md$|scripts/check-contracts-map\.sh$)'';
        self-test = ''^(ruff\.toml|\.yamllint|\.shellcheckrc)$'';
      };
      # A change to the linter driver, the hook scripts or the pinned tools concerns every check.
      pushShared = ''|^(scripts/lint\.sh|scripts/hooks/.*|flake\.nix|flake\.lock)$'';
      pushHooks = pkgs.lib.mapAttrs' (
        check: files:
        pkgs.lib.nameValuePair "push-${check}" {
          enable = true;
          name = "push ${check}";
          entry = "scripts/hooks/in-shell.sh just pre-push ${check}";
          language = "system";
          stages = [ "pre-push" ];
          pass_filenames = false;
          files = files + pushShared;
          require_serial = true;
        }
      ) pushChecks;

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
        }
        // pushHooks;
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
