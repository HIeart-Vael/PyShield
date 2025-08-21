import os
import shutil
import argparse
from pathlib import Path
from setuptools import setup, find_packages, Extension
from Cython.Build import cythonize

# === 配置常量 ===
EXCLUDE_DIRS = {'.idea', 'venv', '.venv', 'build', '__pycache__'}
EXCLUDE_FILES = {'setup.py'}
EXCLUDE_PREFIXES = ('.', '_')

# === 工具函数 ===

def is_valid_module(file_path: Path, exclude_dirs_set: set, exclude_py_set: set) -> bool:
    """判断是否为合法的 Python 模块文件"""
    if file_path.suffix != '.py':
        return False
    if file_path.name in EXCLUDE_FILES:
        return False
    if file_path.name.startswith(EXCLUDE_PREFIXES) and file_path.name != '__init__.py':
        return False
    if any(part in EXCLUDE_DIRS or part in exclude_dirs_set for part in file_path.parts):
        return False
    # 检查是否在排除的py文件列表中
    rel_path = file_path.relative_to(file_path.parent.parent if len(file_path.parts) > 2 else file_path.parent)
    if str(file_path.relative_to(file_path.parent.parent if len(file_path.parts) > 2 else file_path.parent)) in exclude_py_set:
        return False
    if file_path.name in exclude_py_set:
        return False
    # 更准确的相对路径检查
    try:
        rel_path_str = str(file_path.relative_to(Path().resolve()))
        if rel_path_str in exclude_py_set:
            return False
    except:
        pass
    return True

def find_extensions_and_packages(project_root: str, exclude_dirs_set: set, exclude_py_set: set) -> tuple:
    """查找所有模块并生成 Extension 列表和 package 列表"""
    extensions = []
    packages = set()
    root_path = Path(project_root)

    for py_file in root_path.rglob("*.py"):
        if not is_valid_module(py_file, exclude_dirs_set, exclude_py_set):
            continue

        module_name = ".".join(py_file.with_suffix("").relative_to(root_path).parts)
        if py_file.name != "__init__.py":
            extensions.append(Extension(module_name, [str(py_file)]))

        # 收集所有父 package
        parent = py_file.parent
        while parent != root_path:
            if (parent / "__init__.py").exists():
                pkg_name = ".".join(parent.relative_to(root_path).parts)
                packages.add(pkg_name)
            parent = parent.parent

    return extensions, sorted(packages)

def copy_non_python_files(source_root: str, dest_root: str, exclude_dirs_set: set):
    """复制所有非 .py/.pyx 文件，保持目录结构"""
    source = Path(source_root)
    dest = Path(dest_root)
    for file_path in source.rglob("*"):
        if file_path.is_dir():
            continue
        if any(part in EXCLUDE_DIRS or part in exclude_dirs_set for part in file_path.parts):
            continue
        if file_path.suffix.lower() in (".py", ".pyx"):
            continue
        rel_path = file_path.relative_to(source)
        dest_file = dest / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest_file)
        print(f"📄 Copied non-Python file: {file_path} → {dest_file}")

def copy_init_py_files(source_root: str, dest_root: str, exclude_dirs_set: set):
    """复制所有 __init__.py 文件以保留包结构"""
    source = Path(source_root)
    dest = Path(dest_root)
    for init_file in source.rglob("__init__.py"):
        if any(part in EXCLUDE_DIRS or part in exclude_dirs_set for part in init_file.parts):
            continue
        rel_path = init_file.relative_to(source)
        dest_file = dest / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(init_file, dest_file)
        print(f"📦 Copied __init__.py: {init_file} → {dest_file}")

def copy_excluded_dirs(source_root: str, dest_root: str, exclude_dirs_list: list):
    """将被排除的目录原样复制到输出目录中"""
    source = Path(source_root)
    dest = Path(dest_root)
    for dir_name in exclude_dirs_list:
        src_dir = source / dir_name
        if src_dir.is_dir():
            dst_dir = dest / dir_name
            print(f"📁 Copying excluded directory: {src_dir} → {dst_dir}")
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

def copy_excluded_py_files(source_root: str, dest_root: str, exclude_py_list: list):
    """将被排除的py文件原样复制到输出目录中"""
    source = Path(source_root)
    dest = Path(dest_root)
    for py_file_str in exclude_py_list:
        py_file = source / py_file_str
        if py_file.exists() and py_file.suffix == '.py':
            dest_file = dest / py_file_str
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(py_file, dest_file)
            print(f"🐍 Copied excluded Python file: {py_file} → {dest_file}")

# === 主流程 ===

def main():
    parser = argparse.ArgumentParser(description="Compile a Python project to .pyd/.so using Cython.")
    parser.add_argument("project_path", help="Path to the Python project root directory")
    parser.add_argument("-o", "--output", default="build", help="Output directory for compiled files (default: build)")
    parser.add_argument("--exclude-dir", default="", help="Comma-separated list of directories to exclude from compilation but copy as-is")
    parser.add_argument("--exclude-py", default="", help="Comma-separated list of Python files to exclude from compilation but copy as-is")
    args = parser.parse_args()

    PROJECT_ROOT = args.project_path
    OUTPUT_DIR = args.output
    exclude_dirs_list = [d.strip() for d in args.exclude_dir.split(",") if d.strip()]
    exclude_dirs_set = set(exclude_dirs_list)
    exclude_py_list = [f.strip() for f in args.exclude_py.split(",") if f.strip()]
    exclude_py_set = set(exclude_py_list)

    if not Path(PROJECT_ROOT).is_dir():
        print(f"❌ Project path '{PROJECT_ROOT}' does not exist or is not a directory.")
        return

    BUILD_DIR = Path(OUTPUT_DIR)
    BUILD_LIB_DIR = BUILD_DIR / Path(PROJECT_ROOT).name
    BUILD_C_DIR = BUILD_DIR / "__cython__"
    BUILD_TEMP_DIR = BUILD_DIR / "__temp__"

    print("🔍 Finding Python modules and packages...")
    extensions, packages = find_extensions_and_packages(PROJECT_ROOT, exclude_dirs_set, exclude_py_set)
    print(f"Found {len(extensions)} extensions and {len(packages)} packages.")、

    print(f"📦 packages ({len(packages)}):")
    for pkg in packages:
        print(f"  🔹 {pkg}")

    # print(f"🔧 extensions ({len(extensions)}):")
    # for ext in extensions:
    #     print(f"  🔹 {ext.name} -> {ext.sources[0]}")

    print("⚙️  Cythonizing extensions...")
    compiler_directives = {
        'language_level': "3",
        'boundscheck': False,
        'wraparound': False,
        'initializedcheck': False,
        'nonecheck': False,
        'cdivision': True,
        'infer_types': True,
        'embedsignature': True,
    }

    cythonized_extensions = cythonize(
        extensions,
        compiler_directives=compiler_directives,
        build_dir=str(BUILD_C_DIR)
    )

    print("🏗️  Building extensions...")
    BUILD_LIB_DIR.mkdir(parents=True, exist_ok=True)

    setup(
        name="generate-pyd",
        ext_modules=cythonized_extensions,
        packages=packages,
        script_args=[
            "build_ext",
            f"--build-lib={BUILD_LIB_DIR}",
            f"--build-temp={BUILD_TEMP_DIR}"
        ]
    )

    print("📂 Copying non-Python files and __init__.py files...")
    copy_non_python_files(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_dirs_set)
    copy_init_py_files(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_dirs_set)

    if exclude_dirs_list:
        print("📁 Copying excluded directories...")
        copy_excluded_dirs(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_dirs_list)

    if exclude_py_list:
        print("🐍 Copying excluded Python files...")
        copy_excluded_py_files(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_py_list)

    print("✅ Build complete! Check the output directory:", BUILD_DIR.resolve())

if __name__ == "__main__":
    main()

