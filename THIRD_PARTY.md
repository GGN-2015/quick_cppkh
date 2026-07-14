# Third-Party Source Snapshots

`quick_cppkh` is an independent repository. It does not use Git submodules and
does not clone dependencies during a build. The minimal source snapshots needed
to create the companion executables are committed as ordinary files:

- `third_party/cppkh` comes from
  [`TopologicalKnotIndexer/cppkh`](https://github.com/TopologicalKnotIndexer/cppkh)
  commit `ff0489e7763f727a798bcd3fac808534ab4d35f5`.
- `third_party/cpp-pd-code-simplify` comes from
  [`TopologicalKnotIndexer/cpp-pd-code-simplify`](https://github.com/TopologicalKnotIndexer/cpp-pd-code-simplify)
  commit `0869536e85ae018ef5b8fb1cd2e150f5560969e3`.

Both snapshots retain their upstream `LICENSE` files. When updating a snapshot,
copy the required source and license files from a reviewed commit, update the
commit above and in `tools/build.py`, then run
`python tools/test_cli_compatibility.py --rebuild`.
