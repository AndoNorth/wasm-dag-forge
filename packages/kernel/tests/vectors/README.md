# Kernel golden vectors

`splitmix64.json` holds the first values of SplitMix64 for a few seeds, written as lower-case hex.
`workloads.json` holds, per input, the digest that `expected_digest` gives, and the blocks of `expected_blocks` when the result has at most ten rows, with a hex value as its text.
The values were written by the kernel itself, so they hold every later change of the generators, the row formulas or the digest to the values they first gave.
A change that moves a value changes every stored digest that depends on it, so it is a new kernel version and not an edit of this file.

The level 0 vectors of the workload contract, under `contracts/workload/vectors/level0/`, are derived by hand from their formulas and are checked by `test_vectors.py` beside these.

## The spike value

The epic records that SplitMix64 feeding an FNV-1a digest gave `d07cdf67a5d64fea` on host Python, CPython in WebAssembly and Rust in WebAssembly.
The seed, the number of values and the byte encoding that gave it are not recorded in the repository, and the spike code is not in it.
Seeds 0 to 100 and a few common ones, with up to 100,000 values hashed as 8 bytes either endian, as decimal text or as hex text, did not give it.
The value is therefore not reproduced, and these vectors replace it as the reference.
