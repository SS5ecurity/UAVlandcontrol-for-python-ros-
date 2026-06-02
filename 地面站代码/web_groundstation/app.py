"""
app.py — 地面站 Web 服务主入口

功能说明：
    替代原 main.cpp，作为地面站的启动入口。
    使用 Flask 框架提供 Web 服务，使用 Flask-SocketIO 实现 WebSocket 双向通信。
    将 LandScreen 业务逻辑与前端浏览器连接。

源码映射：
    原 C++ 文件: main.cpp
    原流程: QApplication → LandScreen → showFullScreen → a.exec()
    新流程: Flask app → LandScreen → HTTP 服务 → 浏览器全屏显示

架构说明：
    ┌──────────────────────────────────────────────────┐
    │  浏览器 (Web 前端)                                │
    │  - index.html (主界面)                            │
    │  - target_info.html (目标信息)                     │
    │  - Canvas 地图绘制                                │
    │  - WebSocket 实时通信                             │
    └────────────┬─────────────────────────────────────┘
                 │ WebSocket / HTTP
    ┌────────────▼─────────────────────────────────────┐
    │  app.py (Flask + SocketIO)                        │
    │  - 路由：/, /target_info                          │
    │  - WebSocket 事件处理                              │
    │  - LandScreen 实例管理                            │
    └────────────┬─────────────────────────────────────┘
                 │ TCP 8001
    ┌────────────▼─────────────────────────────────────┐
    │  机载计算机 LandScreenNode (ROS)                   │
    │  - 接收禁飞区坐标 → /nofly_zone                    │
    │  - 接收启动命令 → /launch                          │
    │  - 发送目标数据 + 规划路径 (10Hz)                  │
    └──────────────────────────────────────────────────┘

运行方式：
    python app.py                    # 默认在 0.0.0.0:5000 启动
    python app.py --port 8080        # 自定义端口
    python app.py --map ./map.png    # 自定义地图路径
"""

import os
import sys
import argparse
import logging

from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

from land_screen import LandScreen
from blink import Blink
from plane_targets import Target, SharedData

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("GroundStation")

# ============================================================
# Flask 应用初始化
# ============================================================
app = Flask(__name__)
app.config["SECRET_KEY"] = "uav_ground_station_secret_key"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ============================================================
# 全局变量
# ============================================================

# MAP_FILE : 地图图片文件路径（原: ../map.png）
MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map.png")

# F_labels : 前端三个禁飞区标签的当前文本状态  [原: labelF1/F2/F3 的 text()]
F_labels = {"f1": "NULL", "f2": "NULL", "f3": "NULL"}

# land_screen : LandScreen 核心逻辑实例
land_screen: LandScreen | None = None


# ============================================================
# 路由定义
# ============================================================
@app.route("/")
def index():
    """
    主页面路由 — 显示地面站主界面
    [原: LandScreen homeWindow → showFullScreen()]

    返回：index.html 页面
    """
    return render_template("index.html")


@app.route("/target_info")
def target_info():
    """
    目标信息页面路由 — 显示目标详细列表和统计
    [原: TargetInfoDialog → showFullScreen()]

    返回：target_info.html 页面
    """
    return render_template("target_info.html")


@app.route("/map.png")
def serve_map():
    """
    提供地图图片文件
    [原: originalMapPixmap = QPixmap("../map.png")]

    返回：map.png 图片文件
    """
    map_dir = os.path.dirname(os.path.abspath(MAP_FILE))
    map_name = os.path.basename(MAP_FILE)
    return send_from_directory(map_dir, map_name)


@app.route("/api/targets")
def api_targets():
    """
    REST API: 获取所有目标列表
    [原: TargetInfo::loadTargets()]

    数据来源：SharedData.targets_
    返回：JSON 数组，每个目标包含 x, y, name, n, a, b
    """
    if land_screen is None:
        return jsonify([])
    targets = land_screen.loadTargets()
    return jsonify(targets)


@app.route("/api/statistics")
def api_statistics():
    """
    REST API: 获取目标统计信息
    [原: TargetInfo::updateStatistics()]

    数据来源：SharedData.targets_
    返回：{"type_counts": {"类型": 数量, ...}, "total": 总计}
    """
    if land_screen is None:
        return jsonify({"type_counts": {}, "total": 0})
    stats = land_screen.getTargetStatistics()
    return jsonify(stats)


# ============================================================
# WebSocket 事件处理
# ============================================================

@socketio.on("connect")
def handle_connect():
    """
    WebSocket 客户端连接事件
    连接后向前端发送当前禁飞区标签状态和连接状态
    """
    logger.info("WebSocket client connected")
    # 发送当前 F 标签状态
    emit("update_f_labels", F_labels)
    # 发送连接状态
    if land_screen and land_screen.socket:
        emit("update_connection_status", {"status": "已连接"})
    else:
        emit("update_connection_status", {"status": "未连接"})


@socketio.on("disconnect")
def handle_disconnect():
    """WebSocket 客户端断开事件"""
    logger.info("WebSocket client disconnected")


# ---------- 禁飞区按钮交互 ----------

@socketio.on("button_a_click")
def handle_button_a_click(data):
    """
    处理前端 A 行按钮 (A1~A9) 点击
    [原: buttonsA[i] clicked → onButtonAClicked(i)]

    参数：{"index": 0~8}
    """
    if land_screen:
        index = data.get("index", -1)
        if 0 <= index <= 8:
            land_screen.onButtonAClicked(index)


@socketio.on("button_b_click")
def handle_button_b_click(data):
    """
    处理前端 B 行按钮 (B1~B7) 点击
    [原: buttonsB[i] clicked → onButtonBClicked(i)]

    参数：{"index": 0~6}
    """
    if land_screen:
        index = data.get("index", -1)
        if 0 <= index <= 6:
            land_screen.onButtonBClicked(index)


# ---------- 操作按钮 ----------

@socketio.on("send_click")
def handle_send_click():
    """
    处理"发送"按钮点击
    [原: sendButton clicked → onSendClicked()]

    流程：获取当前 F_labels → _on_receive_f_labels → sendData()
    """
    if land_screen:
        labels = [F_labels["f1"], F_labels["f2"], F_labels["f3"]]
        land_screen._on_receive_f_labels(labels)


@socketio.on("cancel_click")
def handle_cancel_click():
    """
    处理"取消"按钮点击
    [原: cancelButton clicked → onCancelClicked()]

    重置所有禁飞区标签为 "NULL"
    """
    global F_labels
    F_labels = {"f1": "NULL", "f2": "NULL", "f3": "NULL"}
    if land_screen:
        land_screen.onCancelClicked()


@socketio.on("launch_click")
def handle_launch_click():
    """
    处理"启动"按钮点击
    [原: launchButton clicked → dataSend["launch"]=true → sendData()]
    """
    if land_screen:
        land_screen.onLaunchClicked()


# ---------- 目标信息 ----------

@socketio.on("show_target_info")
def handle_show_target_info():
    """
    处理"显示目标信息"按钮点击
    [原: showTargetInfoButton clicked → targetInfoDialog->showFullScreen()]

    通知前端加载目标列表数据。
    """
    emit("load_targets", {})


@socketio.on("rescue_target")
def handle_rescue_target(data):
    """
    处理目标信息页面中的救援按钮点击
    [原: TargetInfo::onRescueButtonClicked(const Target& target)]

    参数：{"x": float, "y": float, "name": str}
    """
    if land_screen:
        land_screen.onRescueButtonClicked(data)


# ---------- 闪烁告警 ----------

@socketio.on("trigger_blink")
def handle_trigger_blink(data):
    """
    触发全屏闪烁告警
    [原: Blink::getInstance(time, color)]

    参数：{"time_ms": int, "color": str}
    """
    time_ms = data.get("time_ms", 3000)
    color = data.get("color", "red")
    blink = Blink.getInstance(time_ms, color)

    def blink_callback(is_lighted, blink_color):
        """闪烁回调，通过 WebSocket 通知前端"""
        socketio.emit("blink_command", {
            "is_lighted": is_lighted,
            "color": blink_color
        })

    blink.set_callback(blink_callback)


# ---------- 禁飞区标签同步 ----------

@socketio.on("update_f_label_state")
def handle_update_f_label_state(data):
    """
    前端禁飞区标签状态变更回传
    [原: addForbidden() 中 labelF1/F2/F3 的 setText()]

    参数：{"f1": "文本", "f2": "文本", "f3": "文本"}
    """
    global F_labels
    F_labels = data


# ============================================================
# LandScreen UI 回调设置
# ============================================================

def _setup_land_screen_ui_callback(ls: LandScreen):
    """
    设置 LandScreen 的 UI 回调，桥接到 SocketIO 向前端推送

    参数：
        ls : LandScreen 实例
    """

    def ui_callback(event_type: str, data: dict):
        """LandScreen → WebSocket 前端推送"""
        socketio.emit(event_type, data)

    ls.set_ui_callback(ui_callback)

    # add_forbidden 事件的自定义处理
    def handle_add_forbidden(data):
        """
        处理 addForbidden() 的标签更新
        [原: addForbidden() 中遍历 labelF1/F2/F3，找到第一个 "NULL" 并更新]
        """
        global F_labels
        a_idx = data["a_index"]
        b_idx = data["b_index"]
        text = f"禁飞区N（A{a_idx},B{b_idx}）"

        labels = [F_labels["f1"], F_labels["f2"], F_labels["f3"]]
        for i in range(3):
            if labels[i] == "NULL":
                key = f"f{i + 1}"
                text_final = text.replace("N", str(i + 1))
                F_labels[key] = text_final
                logger.debug(f"设置标签F{i + 1}内容为: {text_final}")
                break

        socketio.emit("update_f_labels", F_labels)

    # 重写 add_forbidden 回调
    original_callback = ls._ui_callback

    def wrapped_callback(event_type, data):
        if event_type == "add_forbidden":
            handle_add_forbidden(data)
        else:
            original_callback(event_type, data)

    ls._ui_callback = wrapped_callback


# ============================================================
# 启动入口 — 对应原 main.cpp
# ============================================================
def main():
    """
    程序主入口  [原: int main(int argc, char* argv[])]

    原流程：
        QApplication a(argc, argv)
        LandScreen homeWindow
        homeWindow.showFullScreen()      # 全屏显示
        return a.exec()                  # 进入事件循环

    新流程：
        解析命令行参数
        初始化 LandScreen 业务逻辑
        启动 Flask Web 服务器
    """
    global land_screen, MAP_FILE

    # ---------- 命令行参数解析 ----------
    parser = argparse.ArgumentParser(description="UAV Ground Station Web Server")
    parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="服务器监听地址 (默认: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="服务器监听端口 (默认: 5000)"
    )
    parser.add_argument(
        "--map", type=str, default=MAP_FILE,
        help=f"地图图片文件路径 (默认: {MAP_FILE})"
    )
    parser.add_argument(
        "--server-ip", type=str, default="192.168.10.3",
        help="机载计算机 TCP 地址 (默认: 192.168.10.3)"
    )
    parser.add_argument(
        "--server-port", type=int, default=8001,
        help="机载计算机 TCP 端口 (默认: 8001)"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="启用 Flask 调试模式"
    )
    args = parser.parse_args()

    MAP_FILE = os.path.abspath(args.map)

    # 检查地图文件是否存在
    if not os.path.exists(MAP_FILE):
        logger.warning(f"地图文件不存在: {MAP_FILE}，请将 map.png 放入程序目录")

    # ---------- 初始化 LandScreen ----------
    # [原: LandScreen homeWindow]
    logger.info("正在初始化地面站核心模块...")
    logger.info(f"TCP 目标: {args.server_ip}:{args.server_port}")
    land_screen = LandScreen(
        server_ip=args.server_ip,
        server_port=args.server_port
    )
    _setup_land_screen_ui_callback(land_screen)

    # ---------- 启动 Web 服务器 ----------
    # [原: homeWindow.showFullScreen() + a.exec()]
    logger.info(f"地面站 Web 服务器启动: http://{args.host}:{args.port}")
    logger.info("请在浏览器中打开上述地址，按 F11 进入全屏模式")

    try:
        socketio.run(
            app,
            host=args.host,
            port=args.port,
            debug=args.debug,
            allow_unsafe_werkzeug=True,
            use_reloader=False  # 避免重复初始化 LandScreen
        )
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    finally:
        if land_screen:
            land_screen.shutdown()
        logger.info("地面站已关闭")


if __name__ == "__main__":
    main()
