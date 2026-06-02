#!/bin/bash
#
# commit_web_gs.sh — 将 web_groundstation 提交到 GitHub
#
# 用法（在 Ubuntu 20.04 上执行）：
#   chmod +x commit_web_gs.sh
#   ./commit_web_gs.sh
#
#   # 如果 GitHub 仓库尚未关联，指定远程地址：
#   ./commit_web_gs.sh --remote git@github.com:你的用户名/仓库名.git
#
#   # 首次推送新分支：
#   ./commit_web_gs.sh --remote git@github.com:你的用户名/仓库名.git --branch main
#
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
INFO="${GREEN}[INFO]${NC}"
WARN="${YELLOW}[WARN]${NC}"
ERR="${RED}[ERROR]${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"    # 仓库根目录 = 上级的上级

REMOTE_URL=""
BRANCH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --remote) REMOTE_URL="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        *) echo -e "${WARN} 未知参数: $1"; shift ;;
    esac
done

# ================================================================
# 1. 检查 git
# ================================================================
if ! command -v git &> /dev/null; then
    echo -e "${ERR} 未找到 git，请先安装: sudo apt install git"
    exit 1
fi

# ================================================================
# 2. 进入仓库根目录并检查/初始化 git
# ================================================================
cd "$GIT_ROOT"

if [ ! -d ".git" ]; then
    echo -e "${INFO} 仓库根目录 ($GIT_ROOT) 尚未初始化 git，正在初始化..."
    git init
    echo -e "${INFO} git init 完成"
else
    echo -e "${INFO} 检测到已有 git 仓库"
fi

# ================================================================
# 3. 设置远程仓库
# ================================================================
CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")

if [ -n "$REMOTE_URL" ]; then
    if [ -n "$CURRENT_REMOTE" ]; then
        git remote set-url origin "$REMOTE_URL"
        echo -e "${INFO} 已更新远程仓库地址"
    else
        git remote add origin "$REMOTE_URL"
        echo -e "${INFO} 已添加远程仓库: $REMOTE_URL"
    fi
elif [ -n "$CURRENT_REMOTE" ]; then
    echo -e "${INFO} 远程仓库: $CURRENT_REMOTE"
else
    echo -e "${WARN} 未检测到远程仓库地址"
    echo -e "${WARN} 请使用 --remote 参数指定，例如："
    echo -e "${WARN}   ./commit_web_gs.sh --remote git@github.com:你的用户名/仓库名.git"
    echo ""
    echo -e "${INFO} 本次将只做本地提交，不推送"
fi

# ================================================================
# 4. 添加 web_groundstation 目录下的所有文件
# ================================================================
echo ""
echo -e "${INFO} ========================================"
echo -e "${INFO}   待提交的文件列表"
echo -e "${INFO} ========================================"

git add "地面站代码/web_groundstation/.gitignore"
git add "地面站代码/web_groundstation/requirements.txt"
git add "地面站代码/web_groundstation/app.py"
git add "地面站代码/web_groundstation/land_screen.py"
git add "地面站代码/web_groundstation/plane_targets.py"
git add "地面站代码/web_groundstation/blink.py"
git add "地面站代码/web_groundstation/mock_tcp_server.py"
git add "地面站代码/web_groundstation/launch_groundstation.sh"
git add "地面站代码/web_groundstation/COMPARISON_REPORT.md"
git add "地面站代码/web_groundstation/USAGE_GUIDE.md"
git add "地面站代码/web_groundstation/static/style.css"
git add "地面站代码/web_groundstation/static/script.js"
git add "地面站代码/web_groundstation/templates/index.html"
git add "地面站代码/web_groundstation/templates/target_info.html"

echo ""
echo -e "${INFO} 文件已暂存。状态如下："
git status --short "地面站代码/web_groundstation/"

# ================================================================
# 5. 确认提交
# ================================================================
echo ""
echo -e "${YELLOW}========================================"
echo -e "  确认提交以上文件？"
echo -e "========================================${NC}"
read -p "  输入 y 确认 / n 取消: " CONFIRM

if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo -e "${INFO} 已取消"
    exit 0
fi

# ================================================================
# 6. 提交
# ================================================================
COMMIT_MSG="feat: 地面站 Qt C++ → Python Web 重写

新增文件：
- app.py            Flask + WebSocket 主入口（替代 main.cpp）
- land_screen.py    地面站核心业务逻辑（替代 landScreen.h/.cpp）
- plane_targets.py  Target/SharedData 数据结构（替代 plane_targets.h）
- blink.py          闪烁告警单例（替代 blink.h/.cpp）
- mock_tcp_server.py 模拟机载端测试脚本
- templates/index.html       主界面（替代 CreateUI）
- templates/target_info.html 目标信息页面（替代 TargetInfo）
- static/style.css           界面样式（替代 Qt StyleSheet）
- static/script.js           前端交互 + Canvas 绘制（替代 QPainter）
- requirements.txt           Python 依赖
- launch_groundstation.sh    ROS 兼容启动脚本
- COMPARISON_REPORT.md       前后文件完整对比报告
- USAGE_GUIDE.md             保姆级使用说明
- .gitignore                 Git 忽略规则

通信协议：TCP 8001 JSON + \\n 分隔（与机载端完全兼容）
函数覆盖率：30/30 (100%)
变量保留率：77/77 (100%)"

git commit -m "$COMMIT_MSG"

echo ""
echo -e "${INFO} ========================================"
echo -e "${INFO}   提交成功!"
echo -e "${INFO} ========================================"

# ================================================================
# 7. 推送到远程
# ================================================================
CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")

if [ -z "$CURRENT_REMOTE" ]; then
    echo -e "${INFO} 未配置远程仓库，跳过推送"
    echo -e "${INFO} 后续可手动执行:"
    echo -e "${INFO}   git remote add origin git@github.com:你的用户名/仓库名.git"
    echo -e "${INFO}   git push -u origin main"
    exit 0
fi

echo ""
echo -e "${YELLOW}是否推送到远程仓库?${NC}"
echo -e "  远程地址: $CURRENT_REMOTE"
read -p "  输入 y 确认 / n 跳过: " PUSH_CONFIRM

if [ "$PUSH_CONFIRM" != "y" ] && [ "$PUSH_CONFIRM" != "Y" ]; then
    echo -e "${INFO} 已跳过推送"
    exit 0
fi

# 确定分支名
if [ -z "$BRANCH" ]; then
    BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
fi
if [ -z "$BRANCH" ]; then
    BRANCH="main"
fi

echo -e "${INFO} 正在推送到 origin/${BRANCH}..."
git push -u origin "$BRANCH"

echo ""
echo -e "${GREEN}========================================"
echo -e "   全部完成! 文件已提交并推送到 GitHub"
echo -e "========================================${NC}"
