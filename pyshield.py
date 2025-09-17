#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cython 批量编译工具 —— 将整个 Python 项目编译为 .pyd/.so 文件
支持排除目录、排除文件、保留非Python资源文件、保留 __init__.py 等

使用方法：
    python generate_pyd.py <project_path> [options]

参数说明：
    project_path        项目根目录路径（必须）
    -o, --output        输出目录（默认: build）
    -t, --threads       并行编译线程数（0=自动检测，默认: 0）
    --exclude-dir       逗号分隔的目录名，排除编译但保留复制（如: tests,docs）
    --exclude-py        通配符规则：
                        - 精确路径: "hello/greeter.py" → 只排除 hello/greeter.py
                        - 全局通配: "*greeter.py" → 排除所有目录下的 greeter.py
                        - 非法格式: "*/greeter.py", "utils/*.py" → 直接报错拒绝

示例：
    # 使用4线程编译
    python generate_pyd.py my_project/ -t 4

    # 自动使用所有CPU核心编译
    python generate_pyd.py my_project/ -t 0

    # 排除根目录下的 config.py 和 utils/debug.py
    python generate_pyd.py my_project/ --exclude-py config.py,utils/debug.py

    # 排除所有 greeter.py 文件（无论在哪层目录）
    python generate_pyd.py my_project/ --exclude-py *greeter.py

    # 排除 tests 目录和所有 config.py 文件
    python generate_pyd.py my_project/ --exclude-dir tests --exclude-py *config.py

版本历史：
    v1.0: 初始版本
    v1.1: 支持命令行参数，优化编译体验
    v1.2: 修复通配符格式校验，禁止非法格式如 */path/file.py
    v1.3: 优化is_valid_module逻辑，确保通配符正确排除编译
    v1.4: 修复通配符匹配问题，确保所有匹配文件不被编译
    v1.5: 优化输出格式，增强可读性
    v1.6: 优化进度显示，添加资源使用提示，改进错误处理
    v2.0: 重构输出系统，提供更专业、简洁的编译日志
    v2.1: 添加多线程编译支持，显著提升大型项目编译速度
    v2.2: 添加-D,-P短参数，简化命令行
    v2.3: 新增默认过滤目录：.cache
    v2.4: 修复路径解析问题，确保输出目录正确 (2025-09-17 09:00)
    v2.5: 支持跳过编译失败文件，保留 .py 源码并提示用户 (2025-09-17 10.12)
    v2.6: 增强错误报告，显示错误文件的行号以及代码上下文 (2025-09-17 10:41)
"""

from contextlib import redirect_stderr
import io
import os
import sys
import shutil
import argparse
import textwrap
import multiprocessing
from pathlib import Path
from typing import Dict, List, Tuple, Set
from setuptools import setup, Extension
from Cython.Build import cythonize


# ========================
# 📦 全局常量配置
# ========================

VERSION = "v2.6 (2025-09-17)"
AUTHOR = "Kaining Wang"

# 默认排除的目录名集合（不影响用户自定义）
EXCLUDE_DIRS: Set[str] = {".idea", "venv", ".venv", ".cache", "build", "__pycache__"}

# 永久排除的特定文件（如构建脚本）
EXCLUDE_FILES: Set[str] = {"setup.py"}

# 忽略以这些前缀开头的模块（保留 __init__.py）
EXCLUDE_PREFIXES: Tuple[str, ...] = (".", "_")

# ANSI 颜色定义（新增）
RESET = "\033[0m"
GRAY = "\033[90m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"
LIGHT_PURPLE = "\033[95m"   # 淡紫色用于标记
BOLD = "\033[1m"


# ========================
# 🔍 工具函数：判断是否应编译该模块
# ========================
def is_valid_module(
    file_path: Path,
    exclude_dirs_set: Set[str],
    exclude_py_set: Set[str],
    root_path: Path,
) -> bool:
    """
    判断一个 .py 文件是否应该参与 Cython 编译。

    Args:
        file_path: 待检查的 .py 文件路径
        exclude_dirs_set: 合并后的排除目录集合
        exclude_py_set: 用户指定要排除的 .py 文件集合（支持精确路径或 *filename.py）
        root_path: 项目根目录，用于计算相对路径

    Returns:
        True 表示应编译；False 表示跳过
    """

    # 仅处理 .py 文件
    if file_path.suffix != ".py":
        return False

    # 排除固定名称文件（如 setup.py）
    if file_path.name in EXCLUDE_FILES:
        return False

    # 排除隐藏或私有文件（但保留 __init__.py）
    if file_path.name.startswith(EXCLUDE_PREFIXES) and file_path.name != "__init__.py":
        return False

    # 排除位于任何排除目录中的文件
    if any(part in exclude_dirs_set for part in file_path.parts):
        return False

    try:
        rel_path = str(file_path.relative_to(root_path))
    except ValueError:
        return False  # 不在项目内

    # 精确路径排除（如 "utils/config.py"）
    if rel_path in exclude_py_set:
        return False

    # 通配符排除（只允许 "*filename.py" 格式）
    for pattern in exclude_py_set:
        if not pattern.startswith("*") or not pattern.endswith(".py"):
            continue
        if len(pattern) <= 2 or "/" in pattern[1:]:
            continue  # 非法格式，如 */file.py 或 utils/*.py
        if file_path.name == pattern[1:]:
            return False

    return True


# ========================
# 🧩 核心函数：扫描项目获取扩展模块与包结构
# ========================
def collect_extensions_and_packages(
    project_root: str, exclude_dirs_set: Set[str], exclude_py_set: Set[str]
) -> Tuple[List[Extension], List[str]]:
    """
    递归扫描项目目录，收集可编译模块及包结构。

    Args:
        project_root: 项目根目录路径
        exclude_dirs_set: 排除目录集合
        exclude_py_set: 排除 .py 文件集合

    Returns:
        (extensions列表, packages列表)
    """
    root_path = Path(project_root).resolve()
    extensions: List[Extension] = []
    packages: Set[str] = set()

    print(f"{LIGHT_PURPLE}[SCAN]{RESET} 正在扫描项目目录，收集模块和包结构...")

    # 遍历所有 .py 文件
    for py_file in root_path.rglob("*.py"):
        # 跳过不符合编译条件的文件
        if not is_valid_module(py_file, exclude_dirs_set, exclude_py_set, root_path):
            continue

        # 构造模块名（如 src/utils/helper.py → src.utils.helper）
        module_name = ".".join(py_file.with_suffix("").relative_to(root_path).parts)

        # 非 __init__.py 的文件才作为扩展模块编译
        if py_file.name != "__init__.py":
            extensions.append(Extension(module_name, [str(py_file)]))

        # 向上追溯包含 __init__.py 的父级目录，注册为包
        parent = py_file.parent
        while True:
            init_file = parent / "__init__.py"
            if init_file.exists():
                pkg_name = ".".join(parent.relative_to(root_path).parts)
                packages.add(pkg_name or ".")  # 根目录记为 "."
            # 到达项目根目录则终止向上查找
            if parent == root_path:
                break
            parent = parent.parent

    sorted_packages = sorted(packages)
    print(f"{GREEN}[SUCCESS]{RESET} 找到 {len(extensions)} 个可编译模块，{len(sorted_packages)} 个包")

    if sorted_packages:
        print(f"{BLUE}[PKG ]{RESET} 包结构 ({len(sorted_packages)}):")
        for pkg in sorted_packages:
            print(f"  - {pkg}")

    return extensions, sorted_packages


# ========================
# 📁 工具函数：复制非 Python 资源文件
# ========================
def copy_non_python_files(source_root: str, dest_root: str, exclude_dirs_set: Set[str]):
    """
    复制所有非 .py/.pyx 文件（如 .json, .txt, .yaml 等）到输出目录。

    Args:
        source_root: 源路径
        dest_root: 目标路径
        exclude_dirs_set: 排除目录集合
    """
    source = Path(source_root)
    dest = Path(dest_root)
    allowed_suffixes = {".py", ".pyx"}

    # 收集所有符合条件的非 Python 文件
    files_to_copy = [
        f
        for f in source.rglob("*")
        if f.is_file()
        and f.suffix.lower() not in allowed_suffixes
        and not any(part in exclude_dirs_set for part in f.parts)
    ]

    if not files_to_copy:
        print("- 资源文件: 无非Python资源文件需要复制")
        return

    print(f"- 资源文件: 找到 {len(files_to_copy)} 个文件，开始复制...")

    copied_count = 0
    for i, file_path in enumerate(files_to_copy, 1):
        rel_path = None
        try:
            rel_path = file_path.relative_to(source)
            target = dest / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)  # 确保目标目录存在
            shutil.copy2(file_path, target)  # 复制文件并保留元数据
            copied_count += 1

            # 每10个或最后一个更新一次进度条
            if copied_count % 10 == 0 or i == len(files_to_copy):
                sys.stdout.write(f"  - 进度: {copied_count}/{len(files_to_copy)}\r")
                sys.stdout.flush()
        except Exception as e:
            print(f"\n  X 复制资源文件失败 {rel_path}: {e}")

    print(f"\n  ✓ 已复制 {copied_count} 个资源文件")


# ========================
# 📦 工具函数：复制 __init__.py 文件以维持包结构
# ========================
def copy_init_py_files(source_root: str, dest_root: str, exclude_dirs_set: Set[str]):
    """
    复制所有有效的 __init__.py 文件，保证编译后仍能正常导入。

    Args:
        source_root: 源路径
        dest_root: 目标路径
        exclude_dirs_set: 排除目录集合
    """
    source = Path(source_root)
    dest = Path(dest_root)

    # 获取所有 __init__.py 文件，并过滤掉在排除目录中的
    init_files = [
        f
        for f in source.rglob("__init__.py")
        if not any(part in exclude_dirs_set for part in f.parts)
    ]

    if not init_files:
        print("- 包结构: 未找到有效的 __init__.py 文件")
        return

    print(f"- 包结构: 找到 {len(init_files)} 个 __init__.py 文件，开始复制...")

    copied_count = 0
    for i, file_path in enumerate(init_files, 1):
        rel_path = None
        try:
            rel_path = file_path.relative_to(source)
            target = dest / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)  # 创建中间目录
            shutil.copy2(file_path, target)  # 复制并保留时间戳等信息
            copied_count += 1

            # 每5个或最后一个刷新进度
            if copied_count % 5 == 0 or i == len(init_files):
                sys.stdout.write(f"  - 进度: {copied_count}/{len(init_files)}\r")
                sys.stdout.flush()
        except Exception as e:
            print(f"\n  X 复制包初始化文件失败 {rel_path}: {e}")

    print(f"\n  ✓ 已保留 {copied_count} 个包结构")


# ========================
# 🗃️ 工具函数：复制用户排除但需保留的目录（如 tests/, docs/）
# ========================
def copy_excluded_directories(
    source_root: str, dest_root: str, exclude_dirs_list: List[str]
):
    """
    复制用户指定的排除目录（例如测试或文档），不参与编译但保留在输出中。

    Args:
        source_root: 源路径
        dest_root: 目标路径
        exclude_dirs_list: 要复制的目录名列表
    """
    if not exclude_dirs_list:
        return

    source = Path(source_root)
    dest = Path(dest_root)

    print(f"- 排除目录: 准备复制 {len(exclude_dirs_list)} 个排除目录...")

    copied = []
    for name in exclude_dirs_list:
        src_dir = source / name
        dst_dir = dest / name

        if not src_dir.is_dir():
            print(f"  ⚠️  跳过不存在的目录: {name}")
            continue

        try:
            # 递归复制整个目录树，允许目标已存在
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
            copied.append(name)
            print(f"  ✓ 已复制排除目录: {name}")
        except Exception as e:
            print(f"  X 复制目录失败 {name}: {e}")

    print(f"  ✓ 共成功复制 {len(copied)} 个排除目录")


# ========================
# 📄 工具函数：复制用户排除但需保留的 .py 文件（如配置文件）
# ========================
def copy_excluded_python_files(
    source_root: str, dest_root: str, exclude_py_list: List[str]
):
    """
    复制用户指定的 .py 文件（如 config.py），即使它们不参与编译。

    Args:
        source_root: 源路径
        dest_root: 目标路径
        exclude_py_list: 要保留的 .py 文件路径或通配模式列表
    """
    if not exclude_py_list:
        return

    source = Path(source_root)
    dest = Path(dest_root)

    print(f"- 排除文件: 准备复制 {len(exclude_py_list)} 个排除的 Python 文件...")

    matched_files = []
    invalid_patterns = []

    # 分析每种排除模式
    for pattern in exclude_py_list:
        pattern = pattern.strip()
        if "*" in pattern:
            # 仅允许 "*filename.py" 形式
            if (
                not pattern.startswith("*")
                or not pattern.endswith(".py")
                or len(pattern) < 3
                or "/" in pattern[1:]
            ):
                invalid_patterns.append(pattern)
                continue

            filename = pattern[1:]
            found = list(source.rglob(filename))  # 全局搜索匹配文件
            matched_files.extend((f, pattern) for f in found)
        else:
            file_path = source / pattern
            if file_path.exists() and file_path.suffix == ".py":
                matched_files.append((file_path, pattern))
            else:
                invalid_patterns.append(pattern)

    # 报告无效模式
    if invalid_patterns:
        print(f"  {YELLOW}[WARN]{RESET} 跳过 {len(invalid_patterns)} 个无效排除模式:")
        for p in invalid_patterns:
            print(f"    - {p}")

    # 开始复制
    if not matched_files:
        print("  - 未找到匹配的 Python 文件")
        return

    print(f"  - 找到 {len(matched_files)} 个匹配文件，开始复制...")

    copied_count = 0
    for file_path, pattern in matched_files:
        rel_path = None
        try:
            rel_path = file_path.relative_to(source)
            target = dest / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target)
            copied_count += 1

            # 每5个或最后一个刷新进度
            if copied_count % 5 == 0 or copied_count == len(matched_files):
                sys.stdout.write(f"    - 进度: {copied_count}/{len(matched_files)}\r")
                sys.stdout.flush()
        except Exception as e:
            print(f"\n    X 复制失败 {rel_path}: {e}")

    print(f"\n  ✓ 已复制 {copied_count} 个排除的 Python 文件")


# ========================
# ⚙️ 安全编译函数：逐个尝试编译并捕获详细错误
# ========================
def safe_cythonize(
    extensions: List[Extension],
    compiler_directives: Dict[str, bool],
    build_temp_dir: str,
) -> Tuple[List[Extension], Dict[str, str]]:
    """
    安全地对每个扩展进行 Cython 预处理，跳过失败项并记录错误详情。

    Args:
        extensions: 扩展模块列表
        compiler_directives: Cython 编译指令
        build_temp_dir: 临时构建目录

    Returns:
        (成功编译的扩展列表, {失败文件: 错误信息})
    """
    compiled = []
    failed = {}

    print(f"{LIGHT_PURPLE}[CYTHON]{RESET} 开始安全编译 {len(extensions)} 个模块（逐个尝试）...")

    for idx, ext in enumerate(extensions, 1):
        src_file = ext.sources[0]
        rel_path = os.path.relpath(src_file, start=os.getcwd())

        # 检查源文件是否存在
        if not os.path.isfile(src_file):
            msg = "源文件不存在"
            print(f"[{idx}/{len(extensions)}] {rel_path} ... {RED}X{RESET}")
            failed[src_file] = msg
            continue

        print(f"[{idx}/{len(extensions)}] Cythonizing {rel_path} ... ", end="")

        stderr_capture = io.StringIO()
        try:
            with redirect_stderr(stderr_capture):
                result = cythonize(
                    [ext],
                    compiler_directives=compiler_directives,
                    build_dir=build_temp_dir,
                    nthreads=1,
                    language_level=3,
                    quiet=True,
                    compile_time_env=False,
                )
            if result:
                compiled.extend(result)
                print(f"{GREEN}✓{RESET}")
            else:
                error_msg = stderr_capture.getvalue().strip()
                print(f"{RED}X{RESET}")
                failed[src_file] = error_msg or "未知编译错误"
        except Exception as e:
            full_error = f"{stderr_capture.getvalue().strip()}\n\nException: {repr(e)}"
            print(f"{RED}X{RESET}\n{full_error}")
            failed[src_file] = full_error

    return compiled, failed


# ========================
# 📂 辅助函数：将编译失败的 .py 文件原样复制
# ========================
def copy_failed_py_files(failed_files: List[str], dest_root: str, project_root: str):
    """
    将编译失败的 .py 文件直接复制到输出目录，避免功能丢失。

    Args:
        failed_files: 编译失败的源文件路径列表
        dest_root: 输出目录
        project_root: 项目根目录
    """
    if not failed_files:
        return

    dest = Path(dest_root)
    proj = Path(project_root).resolve()

    print(f"\n{YELLOW}[COPY]{RESET} 正在复制 {len(failed_files)} 个编译失败的 .py 文件以便保留功能...")

    copied = 0
    for src in failed_files:
        try:
            src_path = Path(src).resolve()
            if not src_path.exists():
                continue
            rel_path = src_path.relative_to(proj)
            target = dest / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, target)
            copied += 1
            print(f"  ✓ 保留源码: {rel_path}")
        except Exception as e:
            print(f"  X 复制失败 {src}: {e}")

    print(f"  ✓ 已保留 {copied} 个未编译的 Python 文件")


# ========================
# ⚙️ 主编译流程：调用 Cython + setuptools 构建
# ========================
def compile_with_cython(
    extensions: List[Extension], build_lib_dir: str, build_temp_dir: str, nthreads: int
) -> Tuple[List[Extension], Dict[str, str]]:
    """
    使用 Cython 和 setuptools 编译扩展模块。

    Args:
        extensions: 待编译的扩展列表
        build_lib_dir: 最终输出目录（存放 .pyd/.so）
        build_temp_dir: 临时工作目录
        nthreads: 并行线程数（0 表示自动）

    Returns:
        (编译成功的扩展, 失败文件及其错误)
    """
    if not extensions:
        print(f"{YELLOW}[WARN]{RESET} 无模块需要编译，跳过 Cython 步骤。")
        return [], {}

    # 自动选择线程数
    actual_threads = multiprocessing.cpu_count() if nthreads == 0 else nthreads
    print(f"- 使用 {actual_threads} 个线程进行编译")

    if actual_threads > 8:
        print(f"{YELLOW}提示：高线程数可能占用大量内存，若失败请减少线程数。{RESET}")

    # Cython 编译器指令（性能优化）
    compiler_directives = {
        "language_level": 3,
        "boundscheck": False,
        "wraparound": False,
        "initializedcheck": False,
        "nonecheck": False,
        "cdivision": True,
        "infer_types": True,
        "embedsignature": True,
    }

    # 第一步：Cython 预处理（生成 .c 文件）
    cythonized_exts, failed_dict = safe_cythonize(
        extensions,
        compiler_directives=compiler_directives,
        build_temp_dir=build_temp_dir,
    )

    if not cythonized_exts:
        print(f"{YELLOW}[WARN]{RESET} 所有模块均未通过 Cython 预处理，终止构建。")
        return [], failed_dict

    # 第二步：调用 setuptools 构建原生扩展
    print(f"{LIGHT_PURPLE}[LINK]{RESET} 正在构建本地扩展模块 (.pyd/.so)...")

    script_args = [
        "build_ext",
        f"--build-lib={build_lib_dir}",
        f"--build-temp={build_temp_dir}",
    ]

    # 添加并行编译标志（Windows用/m，Unix用-j）
    if actual_threads > 1:
        script_args.append(f"--parallel={actual_threads}")

    try:
        setup(
            name="compiled_project",
            ext_modules=cythonized_exts,
            script_args=script_args,
        )
    except Exception as e:
        print(f"{RED}X 构建过程出错: {e}{RESET}")
        print("- 请确认已安装 C 编译器（如 MSVC / GCC / Clang）")
        return cythonized_exts, failed_dict

    return cythonized_exts, failed_dict


# ========================
# 🎯 主函数：程序入口
# ========================
def main():
    """主入口函数：解析参数 → 扫描 → 编译 → 复制辅助文件 → 输出结果"""
    print(f"{CYAN}+++++ 当前版本：{VERSION} +++++{RESET}")
    print(f"{CYAN}+++++ 脚本作者：{AUTHOR} +++++{RESET}")

    # --- 1. 解析命令行 ---
    parser = argparse.ArgumentParser(
        description="Cython 批量编译工具 —— 将 Python 项目编译为 .pyd/.so",
        epilog=textwrap.dedent(f"""
        {YELLOW}使用示例:{RESET}
          python generate_pyd.py myproj/ -D tests,docs -P config.py,*debug.py

        {YELLOW}参数说明:{RESET}
          -D DIRS        排除指定目录（但仍复制结构）
          -P FILES       排除指定 .py 文件（支持 *通配符）
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_path", help="项目根目录路径")
    parser.add_argument(
        "-o", "--output", default="build", help="输出目录（默认: build）"
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=int,
        default=0,
        metavar="NUM",
        help="并行编译线程数（0=自动检测所有核心，默认: 0）",
    )
    parser.add_argument(
        "-D",
        "--exclude-dir",
        default="",
        metavar="DIR",
        help="逗号分隔的目录名，排除编译但保留复制（如: tests,docs）",
    )
    parser.add_argument(
        "-P",
        "--exclude-py",
        default="",
        metavar="FILE",
        help="逗号分隔的 .py 文件路径（支持 '*filename.py' 通配）",
    )

    args = parser.parse_args()

    # --- 2. 初始化变量 ---
    PROJECT_ROOT = args.project_path
    OUTPUT_DIR = args.output
    THREADS = args.threads
    exclude_dirs_list = [d.strip() for d in args.exclude_dir.split(",") if d.strip()]
    exclude_py_list = [f.strip() for f in args.exclude_py.split(",") if f.strip()]

    # 合并排除规则
    exclude_dirs_set = set(exclude_dirs_list) | EXCLUDE_DIRS
    exclude_py_set = set(exclude_py_list)

    # --- 3. 验证输入路径 ---
    project_path = Path(PROJECT_ROOT)
    if not project_path.is_dir():
        print(f"{RED}X 错误：项目路径 '{PROJECT_ROOT}' 不存在或不是目录。{RESET}")
        sys.exit(1)

    # --- 4. 创建构建目录 ---
    BUILD_DIR = Path(OUTPUT_DIR).resolve()
    PROJECT_NAME = project_path.name
    BUILD_LIB_DIR = BUILD_DIR / PROJECT_NAME
    BUILD_TEMP_DIR = BUILD_DIR / "__temp__"

    BUILD_LIB_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"- 项目: {PROJECT_ROOT}")
    print(f"- 输出目录: {BUILD_LIB_DIR}")

    # --- 5. 收集模块 ---
    extensions, _ = collect_extensions_and_packages(
        PROJECT_ROOT, exclude_dirs_set, exclude_py_set
    )

    # --- 6. 执行编译 ---
    _, failed_dict = compile_with_cython(
        extensions, str(BUILD_LIB_DIR), str(BUILD_TEMP_DIR), THREADS
    )

    # --- 7. 复制各类辅助文件 ---
    copy_non_python_files(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_dirs_set)
    copy_init_py_files(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_dirs_set)
    copy_excluded_directories(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_dirs_list)
    copy_excluded_python_files(PROJECT_ROOT, str(BUILD_LIB_DIR), exclude_py_list)
    copy_failed_py_files(list(failed_dict.keys()), str(BUILD_LIB_DIR), PROJECT_ROOT)

    # --- 8. 输出最终状态 ---
    used_threads = THREADS if THREADS > 0 else multiprocessing.cpu_count()
    print(f"- 提示：共使用 {used_threads} 个线程完成编译")

    if failed_dict:
        print(
            f"{YELLOW}[WARN] 注意：以下文件因编译错误未转为二进制，已保留为 .py 源码：{RESET}"
        )
        for src, err in failed_dict.items():
            rel_f = os.path.relpath(src, PROJECT_ROOT)
            print(f"    - {rel_f}")
            if err.strip():
                lines = [f"        {RED}{line}{RESET}" for line in err.splitlines()]
                print("\n" + "\n".join(lines) + "\n")
        print(f"{YELLOW}💡 建议修复语法/Cython兼容性问题后重新编译。{RESET}")
    else:
        print(f"\n{GREEN}[SUCCESS] 所有模块均已成功编译为 .pyd/.so 文件！{RESET}")

    print(f"\n{GREEN}✓ 编译完成！输出目录: {BUILD_LIB_DIR.resolve()}{RESET}")


# ========================
# 🚀 启动入口
# ========================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[INTERRUPT]{RESET} 编译过程被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}[ERROR]{RESET} 意外错误: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
