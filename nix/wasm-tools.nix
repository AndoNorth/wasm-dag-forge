# Hash-checked release fetches: nixos-26.05 carries wasmtime 45.0.2 (not an LTS line) and an older wasm-tools.
# Versions are shared with the compile image, see docs/versions-policy.md.
{ pkgs }:
let
  fetchRelease =
    {
      pname,
      version,
      url,
      hash,
      bin,
    }:
    pkgs.stdenv.mkDerivation {
      inherit pname version;
      src = pkgs.fetchurl { inherit url hash; };
      nativeBuildInputs = [ pkgs.autoPatchelfHook ];
      buildInputs = [ pkgs.stdenv.cc.cc.lib ];
      installPhase = ''
        install -Dm755 ${bin} $out/bin/${pname}
      '';
    };
in
[
  (fetchRelease {
    pname = "wasmtime";
    version = "48.0.7";
    url = "https://github.com/bytecodealliance/wasmtime/releases/download/v48.0.7/wasmtime-v48.0.7-x86_64-linux.tar.xz";
    hash = "sha256-IZLitbkR3EIyG56sK38fCw0DNWBYweKywriSYlvdD0U=";
    bin = "wasmtime";
  })
  (fetchRelease {
    pname = "wasm-tools";
    version = "1.262.0";
    url = "https://github.com/bytecodealliance/wasm-tools/releases/download/v1.262.0/wasm-tools-1.262.0-x86_64-linux.tar.gz";
    hash = "sha256-ZVuN9n1Uw++bUvwjfE6Uu/u19fQ4JSRcsJQKcAKp7WU=";
    bin = "wasm-tools";
  })
]
