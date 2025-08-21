## 项目名：**PyShield**

## 项目描述：

# PyShield - Python 项目编译保护工具

PyShield 是一个强大的 Python 项目编译工具，可以将 Python 源代码编译为二进制扩展文件（.pyd/.so），有效保护您的知识产权和商业机密。

## 🚀 主要功能

- **源码编译保护**：将 `.py` 文件编译为 `.pyd`（Windows）或 `.so`（Linux/Mac）二进制文件
- **智能文件过滤**：自动识别并排除开发环境文件（如 `.idea`, `venv`, `__pycache__` 等）
- **完整项目结构保持**：编译后保持原有的包结构和目录组织
- **灵活的排除机制**：支持指定目录和文件不参与编译，直接复制到输出目录
- **非Python文件处理**：自动复制配置文件、资源文件等非Python文件到编译目录
- **Cython优化编译**：内置优化编译指令，提升运行性能

## 📦 使用场景

- 商业Python项目源码保护
- Python应用程序分发
- 敏感算法代码加密
- Python库的二进制分发

## 💡 快速开始

```bash
# 基本使用
python generate_pyd.py ./your_project -o output_dir

# 排除特定目录和文件
python generate_pyd.py ./your_project -o output_dir --exclude-dir venv,tests --exclude-py config.py
```

## 🔧 特性

- **跨平台支持**：Windows、Linux、macOS
- **保留项目结构**：编译后的目录结构与源项目完全一致
- **高性能编译**：利用Cython进行优化编译
- **易于集成**：简单的命令行接口，易于集成到CI/CD流程中

## 🎯 适用人群

- Python开发者
- 软件安全工程师
- 企业技术负责人
- 开源项目维护者

通过 PyShield，您可以轻松地将 Python 项目转换为难以逆向工程的二进制分发版本，同时保持项目的完整功能和性能。
