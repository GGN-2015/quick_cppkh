# quick-cppkh-interface

`quick-cppkh-interface` is a Python package for computing integer Khovanov
homology through `quick_cppkh`.

It keeps the familiar `cppkh-interface` style API:

```python
import quick_cppkh_interface

pd_code = [[1, 5, 2, 4], [3, 1, 4, 6], [5, 3, 6, 2]]

print(quick_cppkh_interface.solve_khovanov(pd_code))
print(quick_cppkh_interface.solve_many_khovanov([pd_code, pd_code]))
print(quick_cppkh_interface.compute_signed_variants(pd_code, [[1, 1, 1]]))
```

The package ships C++ source code in built distributions and compiles local
executables on first use through `cpp-simple-interface`. The compiled
executables are cached for later calls:

- `quick_cppkh`
- `cppkh`
- `pd_simplify`

The default homology path races direct `cppkh` against `pd_simplify` followed
by `cppkh --no-simplify-pd`, returning whichever successful route finishes
first.

The API is compatible with `cppkh-interface` 0.2.1. All four combinations of
`de_r1` and `de_k8` are accepted for single and batch calls. The default
`(True, True)` combination uses the quick race; mixed combinations use cppkh's
independent native simplification switches. `compile_cppkh_shared` and
`compute_signed_variants` expose the new signed-variant C API.

## Install

```sh
pip install quick-cppkh-interface
```

A `g++` compatible compiler must be available at runtime. To select a compiler,
set `CXX` before importing or calling the package:

```sh
CXX=clang++ python your_script.py
```

Windows PowerShell:

```powershell
$env:CXX = "C:\path\to\g++.exe"
python your_script.py
```

## Build And Publish

From this directory:

```sh
python -m build
poetry publish
```

The PEP 517 build step runs the custom backend that embeds the tested C++
sources. Publish the existing artifacts with Poetry; do not use
`poetry publish --build`.

For local testing:

```sh
poetry run python -m quick_cppkh_interface "[[1,5,2,4], [3,1,4,6], [5,3,6,2]]"
```
