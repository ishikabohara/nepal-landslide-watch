"""Make XGBoost use scikit-learn's bundled OpenMP on macOS; no system install."""
import importlib.util
import platform
import subprocess
from pathlib import Path

if platform.system()=="Darwin":
    spec=importlib.util.find_spec("xgboost")
    sklearn=importlib.util.find_spec("sklearn")
    if spec and sklearn:
        library=Path(spec.origin).parent/"lib/libxgboost.dylib"
        omp=Path(sklearn.origin).parent/".dylibs/libomp.dylib"
        rpath="@loader_path/../../sklearn/.dylibs"
        if omp.exists() and rpath not in subprocess.check_output(["otool","-l",str(library)],text=True):
            subprocess.run(["install_name_tool","-add_rpath",rpath,str(library)],check=True)
            print("Configured local XGBoost OpenMP runtime")
