#!/bin/bash
#
# launch_groundstation.sh — 地面站启动脚本
#
# 功能说明：
#     在 Ubuntu 20.04 + ROS 环境下启动地面站 Web 服务。
#     自动安装 Python 依赖、检查环境、启动 Flask 服务器。
#
# 使用方式：
#     chmod +x launch_groundstation.sh
#     ./launch_groundstation.sh                    # 默认端口 5000
#     ./launch_groundstation.sh --port 8080        # 自定义端口
#     ./launch_groundstation.sh --map /path/to/map.png  # 自定义地图
#
# 源码映射：
#     原 C++ build.sh 中的编译运行流程

set -e

# ============================================================
# 颜色输出定义
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================
# 环境检查
# ============================================================
echo_info "正在检查运行环境..."

# 获取脚本所在目录（即 web_groundstation/ 目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo_error "未找到 python3，请先安装 Python 3.8+"
    exit 1
fi
echo_info "Python3: $(python3 --version)"

# 检查 ROS 环境（可选）
if [ -d "/opt/ros" ]; then
    echo_info "检测到 ROS 安装目录: /opt/ros"
    # 自动 source ROS setup
    if [ -f "/opt/ros/noetic/setup.bash" ]; then
        source /opt/ros/noetic/setup.bash
        echo_info "已加载 ROS Noetic 环境"
    elif [ -f "/opt/ros/melodic/setup.bash" ]; then
        source /opt/ros/melodic/setup.bash
        echo_info "已加载 ROS Melodic 环境"
    fi
else
    echo_warn "未检测到 ROS，非 ROS 模式下仅 TCP 通信可用"
fi

# ============================================================
# 安装 Python 依赖
# ============================================================
echo_info "正在检查 Python 依赖..."

if [ -f "requirements.txt" ]; then
    # 检查关键依赖是否已安装
    MISSING=""
    python3 -c "import flask" 2>/dev/null || MISSING="$MISSING flask"
    python3 -c "import flask_socketio" 2>/dev/null || MISSING="$MISSING flask-socketio"
    python3 -c "import eventlet" 2>/dev/null || MISSING="$MISSING eventlet"

    if [ -n "$MISSING" ]; then
        echo_info "正在安装缺失的依赖:$MISSING"
        pip3 install -r requirements.txt --user
    else
        echo_info "所有 Python 依赖已就绪"
    fi
else
    echo_warn "未找到 requirements.txt，跳过依赖安装"
fi

# ============================================================
# 解析启动参数
# ============================================================
HOST="0.0.0.0"
PORT=5000
MAP_FILE="$SCRIPT_DIR/map.png"
SERVER_IP="192.168.10.3"
SERVER_PORT=8001

while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --map)
            MAP_FILE="$2"
            shift 2
            ;;
        --server-ip)
            SERVER_IP="$2"
            shift 2
            ;;
        --server-port)
            SERVER_PORT="$2"
            shift 2
            ;;
        --debug)
            DEBUG_FLAG="--debug"
            shift
            ;;
        *)
            echo_warn "未知参数: $1"
            shift
            ;;
    esac
done

# ============================================================
# 检查地图文件
# ============================================================
if [ ! -f "$MAP_FILE" ]; then
    echo_warn "地图文件不存在: $MAP_FILE，地图将无法显示"
    echo_warn "请将 map.png 放入 $SCRIPT_DIR 目录，或使用 --map 参数指定路径"
fi

# ============================================================
# 显示启动信息
# ============================================================
echo ""
echo_info "========================================"
echo_info "  UAV 地面站 Web 服务"
echo_info "========================================"
echo_info "监听地址: http://${HOST}:${PORT}"
echo_info "地图文件: $MAP_FILE"
echo_info "主页面:   http://${HOST}:${PORT}/"
echo_info "目标信息: http://${HOST}:${PORT}/target_info"
echo_info ""
echo_info "请在浏览器中打开上述地址"
echo_info "按 F11 进入全屏模式以获得最佳体验"
echo_info "按 Ctrl+C 停止服务"
echo_info "========================================"
echo ""

# ============================================================
# 启动 Flask 应用
# ============================================================
exec python3 app.py --host "$HOST" --port "$PORT" --map "$MAP_FILE" $DEBUG_FLAG
