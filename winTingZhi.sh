#!/usr/bin/env bash
# 指定使用 bash 解释器执行此脚本

# 检查当前是否正在使用 bash，如果不是，则用 bash 重新执行本脚本
if [ -z "${BASH_VERSION:-}" ]; then
    exec /usr/bin/env bash "$0" "$@"
fi

# 获取脚本所在目录的绝对路径
ROOT="$(cd "$(dirname "$0")" && pwd)"
# 切换到脚本所在目录
cd "$ROOT"

# 检测操作系统类型
IS_WINDOWS=0
# 通过 uname 命令获取系统名称，如果失败则返回 'unknown'
case "$(uname -s 2>/dev/null || echo 'unknown')" in
    # 如果是 Windows 环境（MinGW、MSYS 或 Cygwin），标记为 Windows
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac

# 检查是否存在 app.pid 文件（启动时保存的进程ID）

    
    # 删除 PID 文件
    rm -f app.pid
    
    if [ "$IS_WINDOWS" -eq 1 ]; then
        # Windows 环境：通过 PowerShell 查找并终止 uvicorn 相关的 Python 进程
        # 使用 PowerShell 的 Get-Process 和 Where-Object 过滤包含 "uvicorn" 的进程
        # 这种方法可以查看完整的命令行参数，比 tasklist 更可靠
        powershell -Command "Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'python.exe' -and \$_.CommandLine -like '*uvicorn*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null || true
        
        echo "Service stopped (Windows fallback method)"
    else
        # Linux/Mac 环境：使用 pkill 命令终止包含特定字符串的进程
        # -f 表示匹配完整的命令行
        pkill -f "uvicorn text2sql_api.main:app" 2>/dev/null || true
        echo "Service stopped"
    fi
    
