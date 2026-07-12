# quick-cppkh-interface

`quick-cppkh-interface` is a Python package for computing integer Khovanov
homology through `quick_cppkh`.

It keeps the familiar `cppkh-interface` style API:

```python
import quick_cppkh_interface

pd_code = [[1, 5, 2, 4], [3, 1, 4, 6], [5, 3, 6, 2]]

print(quick_cppkh_interface.solve_khovanov(pd_code))
print(quick_cppkh_interface.solve_many_khovanov([pd_code, pd_code]))
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
poetry build
poetry publish
```

For local testing:

```sh
poetry run python -m quick_cppkh_interface "[[1,5,2,4], [3,1,4,6], [5,3,6,2]]"
```
