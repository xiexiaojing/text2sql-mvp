#!/usr/bin/env bash
# 指定使用 bash 解释器执行此脚本

# 检查当前是否正在使用 bash，如果不是，则用 bash 重新执行本脚本
if [ -z "${BASH_VERSION:-}" ]; then
    exec /usr/bin/env bash "$0" "$@"
fi

# 设置 shell 选项：
# -e: 遇到错误立即退出
# -u: 使用未定义的变量时报错
# -o pipefail: 管道中任何一个命令失败都视为整体失败
set -euo pipefail

# 获取脚本所在目录的绝对路径，并切换到该目录
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 检测操作系统类型
IS_WINDOWS=0
# 通过 uname 命令获取系统名称，如果失败则返回 'unknown'
case "$(uname -s 2>/dev/null || echo 'unknown')" in
    # 如果是 Windows 环境（MinGW、MSYS 或 Cygwin），标记为 Windows
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac

# 根据操作系统设置不同的虚拟环境路径和 Python 可执行文件名
if [ "$IS_WINDOWS" -eq 1 ]; then
    # Windows 环境下，虚拟环境的可执行文件在 Scripts 目录
    VENV_BIN="Scripts"
    PYTHON_EXE="python"
    PATH_SEP=";"  # Windows 使用分号作为路径分隔符
else
    # Linux/Mac 环境下，虚拟环境的可执行文件在 bin 目录
    VENV_BIN="bin"
    PYTHON_EXE="python3"
    PATH_SEP=":"  # Linux/Mac 使用冒号作为路径分隔符
fi

# 如果仍然找不到 Python，输出错误信息并退出
if ! command -v "$PYTHON_EXE" >/dev/null 2>&1; then
    echo "ERROR: No Python interpreter found (tried python3, python, py)." >&2
    echo "Please install Python >=3.11 and add it to PATH." >&2
    exit 1
fi

# 显示当前使用的 Python 版本信息
echo "Using Python: $PYTHON_EXE ($("$PYTHON_EXE" --version 2>&1))"

# 定义函数：查找已存在的虚拟环境目录
resolve_venv_dir() {
    # 依次检查 .venv 和 venv 两个可能的虚拟环境目录
    for dir in .venv venv; do
        # 构造 Python 可执行文件的完整路径
        local py_exe="$ROOT/$dir/$VENV_BIN/python"
        # Windows 环境下需要添加 .exe 扩展名
        if [ "$IS_WINDOWS" -eq 1 ]; then
            py_exe="$py_exe.exe"
        fi
        # 如果该 Python 可执行文件存在且可执行，返回虚拟环境目录路径
        if [ -x "$py_exe" ]; then
            echo "$ROOT/$dir"
            return 0
        fi
    done
    # 如果没有找到可用的虚拟环境，返回失败
    return 1
}

# 尝试查找现有的虚拟环境，如果不存在则创建新的
if ! VENV_DIR="$(resolve_venv_dir)"; then
    echo "Virtualenv not found, creating .venv ..."
    # 使用 Python 创建新的虚拟环境
    "$PYTHON_EXE" -m venv "$ROOT/.venv"
    VENV_DIR="$ROOT/.venv"
    # 升级 pip 到最新版本
    # "$VENV_DIR/$VENV_BIN/python" -m pip install -q -U pip
    # 以可编辑模式安装当前项目及其依赖
    "$VENV_DIR/$VENV_BIN/python" -m pip install -q -e "$ROOT"
fi

# 构造 uvicorn 服务器的完整路径
UVICORN="$VENV_DIR/$VENV_BIN/uvicorn"
# Windows 环境下需要添加 .exe 扩展名
if [ "$IS_WINDOWS" -eq 1 ]; then
    UVICORN="$UVICORN.exe"
fi

# 如果 uvicorn 不可执行，重新安装项目依赖
if [ ! -x "$UVICORN" ]; then
    echo "Installing project dependencies ..."
    "$VENV_DIR/$VENV_BIN/python" -m pip install -q -e "$ROOT"
fi

# 设置 Python 模块搜索路径，包含 API 应用和运行时包
export PYTHONPATH="apps/api/src${PATH_SEP}packages/text2sql_runtime/src"

# 创建日志目录（如果不存在）
mkdir -p logs

# 在后台启动 FastAPI 服务：
# - nohup: 使进程在终端关闭后继续运行
# - uvicorn: ASGI 服务器，运行 text2sql_api.main 模块中的 app 对象
# - --host 0.0.0.0: 监听所有网络接口
# - --port 8777: 使用 8777 端口
# - > logs/api.log 2>&1: 将标准输出和错误输出都重定向到日志文件
# - &: 在后台运行
nohup "$UVICORN" text2sql_api.main:app --host 0.0.0.0 --port 8777 > logs/api.log 2>&1 &

# 将后台进程的 PID（进程ID）保存到 app.pid 文件中
echo $! > app.pid

# 显示服务已成功启动，并输出进程ID
echo "Service started with PID: $(cat app.pid)"
