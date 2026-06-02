"""
land_screen.py — 地面站核心逻辑模块

功能说明：
    实现地面站全部核心业务逻辑，包括：
    - TCP Socket 客户端（连接机载计算机 192.168.10.3:8001）
    - JSON 数据收发与解析（禁飞区坐标发送、目标位置/规划路径接收）
    - 禁飞区 A/B 网格按钮选择逻辑
    - 路径航点的网格坐标转换
    - 目标汇总信息的管理与推送
    通过回调函数将 UI 状态变化推送到 Web 前端。

源码映射：
    原 C++ 文件: landScreen.h + landScreen.cpp
    原 Class:    class LandScreen : public QWidget  →  Python LandScreen 类
    原 Struct:   struct Point  →  Python Point 数据类
    原函数/变量名：全部保留（带下划线的成员变量名保持原样）
    原信号:     wayPointsReady  →  通过 callback 回调实现

通信协议：
    出站（→ 机载端 TCP 8001）：
        {"f1x":<int>,"f1y":<int>,"f2x":<int>,"f2y":<int>,
         "f3x":<int>,"f3y":<int>,"launch":<bool>}
    入站（← 机载端 TCP 8001）：
        {"tx":<float>,"ty":<float>,"tn":"<str>",
         "planner":[{"x":<float>,"y":<float>},...]}
"""

import json
import math
import re
import socket
import threading
import time
import logging

from plane_targets import Target, SharedData

# ============================================================
# 常量定义 — 对应原 C++ #define 宏
# ============================================================

# SERVER_IP : 机载计算机 IP 地址，地面站连接的目标  [原: #define SERVER_IP "192.168.10.3"]
SERVER_IP = "192.168.10.3"

# SERVER_PORT : 机载 LandScreenNode TCP 监听端口  [原: #define SERVER_PORT 8001]
SERVER_PORT = 8001

# OFFSET_X : X 轴网格偏移量（原代码定义但未在计算中使用）  [原: #define OFFSET_X 0.25]
OFFSET_X = 0.25

# OFFSET_Y : Y 轴网格偏移量（原代码定义但未在计算中使用）  [原: #define OFFSET_Y 0.25]
OFFSET_Y = 0.25

# 重连间隔（秒），原 QTimer 500ms  [原: reconnectTimer->setInterval(500)]
RECONNECT_INTERVAL = 0.5

# 日志
logger = logging.getLogger("LandScreen")


# ============================================================
# Point 数据类 — 对应原 C++ struct Point
# ============================================================
class Point:
    """
    网格坐标点

    属性说明（保留原 C++ 变量名）：
        a : int  — 网格横坐标 A（0~8 对应 UI 按钮 A1~A9）  [原: qint8 a]
        b : int  — 网格纵坐标 B（0~6 对应 UI 按钮 B1~B7）  [原: qint8 b]
    """
    def __init__(self, a: int = -1, b: int = -1):
        self.a: int = a  # [原: qint8 a]
        self.b: int = b  # [原: qint8 b]

    def to_dict(self):
        """转换为字典，用于 WebSocket JSON 序列化"""
        return {"a": self.a, "b": self.b}


# ============================================================
# LandScreen 类 — 对应原 C++ class LandScreen : public QWidget
# ============================================================
class LandScreen:
    """
    地面站核心业务逻辑类

    原 Qt 版本继承 QWidget 直接处理 UI；
    Web 版本分离 UI（前端 HTML/JS）与业务逻辑（本类），
    通过回调函数推送 UI 更新。

    成员变量（保留原 C++ 变量名）：
        mapLabel             — QLabel*     → 前端 <canvas> 元素（在 JS 中绘制）
        originalMapPixmap    — QPixmap     → 无直接对应（JS Canvas 加载地图图片）
        labelA, labelB       — QLabel*     → 前端 <span> 标签
        buttonsA[9]          — QPushButton*[9]  → 前端 9 个 A 行按钮
        buttonsB[7]          — QPushButton*[7]  → 前端 7 个 B 行按钮
        labelF1/F2/F3        — QLabel*     → 前端禁飞区标签
        connectStatusLabel   — QLabel*     → 前端连接状态标签
        sendButton           — QPushButton* → 前端发送按钮
        cancelButton         — QPushButton* → 前端取消按钮
        launchButton         — QPushButton* → 前端启动按钮
        labelTargetSummary   — QLabel*     → 前端目标汇总标签
        showTargetInfoButton — QPushButton* → 前端显示目标按钮
        targetInfoDialog     — TargetInfo* → 前端弹窗/页面
        selectedButtonA      — int         → 当前选中 A 按钮索引
        selectedButtonB      — int         → 当前选中 B 按钮索引
        receivedPoint        — Point       → 当前接收到的目标网格点
        socket               — QTcpSocket* → Python socket.socket
        reconnectTimer       — QTimer*     → 重连定时器线程
        dataSend             — QJsonObject → Python dict
        receivedTarget       — Target      → 接收到的目标
        targets              — vector      → 目标列表（通过 SharedData 管理）
        wayPoints            — vector<Point> → 已解析的路径航点列表
    """

    def __init__(self, server_ip: str = None, server_port: int = None):
        """
        构造函数  [原: LandScreen::LandScreen(QWidget *parent)]

        初始化所有成员变量、创建 TCP Socket 并启动连接。

        参数：
            server_ip   : str  — 机载计算机 IP（默认 192.168.10.3）
            server_port : int  — 机载 TCP 端口（默认 8001）
        """
        # ---------- 连接参数 ----------
        # server_ip : 连接目标 IP [原: #define SERVER_IP]
        self.server_ip: str = server_ip if server_ip else SERVER_IP
        # server_port : 连接目标端口 [原: #define SERVER_PORT]
        self.server_port: int = server_port if server_port else SERVER_PORT

        # ---------- 地图相关 ----------
        # mapLabel : 地图显示组件 [原: QLabel *mapLabel = nullptr]
        self.mapLabel = None  # Web版：前端 <canvas id="mapCanvas">

        # originalMapPixmap : 原始地图图片 [原: QPixmap originalMapPixmap]
        self.originalMapPixmap = None  # Web版：JS Image 对象加载 map.png

        # ---------- 网格坐标标签 ----------
        # labelA : "A" 行标识标签 [原: QLabel *labelA = nullptr]
        self.labelA = None
        # labelB : "B" 行标识标签 [原: QLabel *labelB = nullptr]
        self.labelB = None

        # ---------- 网格按钮数组 ----------
        # buttonsA[9] : A 行 9 个按钮 (A1~A9) [原: QPushButton *buttonsA[9]]
        self.buttonsA = [None] * 9
        # buttonsB[7] : B 行 7 个按钮 (B1~B7) [原: QPushButton *buttonsB[7]]
        self.buttonsB = [None] * 7

        # ---------- 禁飞区标签 (F1/F2/F3) ----------
        # labelF1 : 禁飞区1 标签 [原: QLabel *labelF1 = nullptr]
        self.labelF1 = None
        # labelF2 : 禁飞区2 标签 [原: QLabel *labelF2 = nullptr]
        self.labelF2 = None
        # labelF3 : 禁飞区3 标签 [原: QLabel *labelF3 = nullptr]
        self.labelF3 = None

        # ---------- 连接状态标签 ----------
        # connectStatusLabel : 显示 "已连接"/"已断开"/"未连接" [原: QLabel* connectStatusLabel = nullptr]
        self.connectStatusLabel = None

        # ---------- 操作按钮 ----------
        # sendButton : "发送" 按钮 [原: QPushButton *sendButton = nullptr]
        self.sendButton = None
        # cancelButton : "取消" 按钮 [原: QPushButton *cancelButton = nullptr]
        self.cancelButton = None
        # launchButton : "启动" 按钮 [原: QPushButton* launchButton = nullptr]
        self.launchButton = None

        # ---------- 目标信息相关 ----------
        # labelTargetSummary : 目标汇总标签 [原: QLabel *labelTargetSummary = nullptr]
        self.labelTargetSummary = None
        # showTargetInfoButton : "显示目标信息" 按钮 [原: QPushButton *showTargetInfoButton = nullptr]
        self.showTargetInfoButton = None
        # targetInfoDialog : 目标信息弹窗 [原: TargetInfo *targetInfoDialog = nullptr]
        self.targetInfoDialog = None

        # ---------- 按钮选中状态 ----------
        # selectedButtonA : 当前选中的 A 行按钮索引，-1 表示无选中 [原: int selectedButtonA = -1]
        self.selectedButtonA: int = -1
        # selectedButtonB : 当前选中的 B 行按钮索引，-1 表示无选中 [原: int selectedButtonB = -1]
        self.selectedButtonB: int = -1
        # receivedPoint : 当前接收到的目标网格点 [原: Point receivedPoint = {-1, -1}]
        self.receivedPoint: Point = Point(-1, -1)

        # ---------- 网络通信 ----------
        # socket : TCP Socket 客户端 [原: QTcpSocket *socket = nullptr]
        self.socket: socket.socket | None = None
        # reconnectTimer : 重连定时器 [原: QTimer *reconnectTimer = nullptr]
        self.reconnectTimer: threading.Timer | None = None

        # ---------- 发送数据 ----------
        # dataSend : 发送到机载端的 JSON 数据 [原: QJsonObject dataSend]
        # 初始化：所有禁飞区坐标设为 -1（未设置），launch 为 false
        self.dataSend: dict = {
            "f1x": -1, "f1y": -1,
            "f2x": -1, "f2y": -1,
            "f3x": -1, "f3y": -1,
            "launch": False
        }

        # ---------- 接收目标数据 ----------
        # receivedTarget : 从机载端接收到的目标 [原: Target receivedTarget = {-1,-1,"NULL"}]
        self.receivedTarget: Target = Target(-1, -1, "NULL")

        # targets : 目标列表引用（通过 SharedData 管理）[原: std::vector<Target> targets]
        self.targets = []

        # ---------- 路径航点 ----------
        # wayPoints : 已解析的规划路径航点列表 [原: std::vector<Point> wayPoints]
        self.wayPoints: list = []

        # ---------- 线程控制 ----------
        # _running : 控制重连和接收线程的运行标志
        self._running: bool = True
        # _receive_thread : TCP 接收线程
        self._receive_thread: threading.Thread | None = None
        # _reconnect_thread : 重连线程
        self._reconnect_thread: threading.Thread | None = None

        # ---------- 回调函数 ----------
        # _ui_callback : 向前端推送 UI 更新的回调函数
        self._ui_callback = None

        # 初始化 Socket 并启动连接  [原: initSocket()]
        self.initSocket()

    def set_ui_callback(self, callback):
        """
        设置 UI 更新回调函数
        回调函数接收 (event_type: str, data: dict) 参数，
        用于向前端 WebSocket 客户端推送状态变化。

        参数：
            callback : callable(event_type, data)
                      event_type: "update_f_labels" | "update_button_a_styles" |
                                  "update_button_b_styles" | "update_target_summary" |
                                  "update_connection_status" | "draw_waypoints" |
                                  "blink_command" | "load_targets" | "show_alert"
        """
        self._ui_callback = callback

    def _emit_ui(self, event_type: str, data: dict):
        """内部辅助方法：通过回调推送 UI 更新到前端"""
        if self._ui_callback:
            try:
                self._ui_callback(event_type, data)
            except Exception as e:
                logger.error(f"UI callback error: {e}")

    # ================================================================
    # initSocket() — TCP Socket 初始化与重连
    # ================================================================
    def initSocket(self):
        """
        初始化 TCP Socket 并启动连接/重连机制
        [原: void LandScreen::initSocket()]

        功能：
            1. 创建 TCP Socket（阻塞模式）
            2. 启动重连线程（每 RECONNECT_INTERVAL 秒检测并重连）
            3. 启动接收线程（读取机载端发来的数据）

        数据流向：
            出站 → 192.168.10.3:8001（机载 LandScreenNode）
            入站 ← 机载端 10Hz 推送的 JSON 数据
        """
        # 启动重连线程  [原: reconnectTimer->start()]
        self._running = True
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        """
        重连循环（在独立线程中运行）
        模拟原 QTimer::timeout 信号 → 每 0.5 秒检测连接状态，断线后自动重连。
        [原: connect(reconnectTimer, &QTimer::timeout, ...)]
        """
        while self._running:
            try:
                # 如果未连接，尝试连接
                if self.socket is None:
                    self._try_connect()
                time.sleep(RECONNECT_INTERVAL)
            except Exception as e:
                logger.warning(f"Reconnect loop error: {e}")
                time.sleep(RECONNECT_INTERVAL)

    def _try_connect(self):
        """
        尝试连接机载计算机 TCP 服务器。
        连接成功后通知前端更新连接状态，并启动数据接收线程。
        [原: socket->connectToHost(SERVER_IP, SERVER_PORT)]
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((self.server_ip, self.server_port))
            sock.settimeout(None)  # 接收时阻塞
            self.socket = sock

            logger.info("Connected to server")
            # 通知前端连接状态 [原: connectStatusLabel->setText("已连接")]
            self._emit_ui("update_connection_status", {"status": "已连接"})

            # 启动 TCP 接收线程  [原: connect(socket, &QTcpSocket::readyRead, ...)]
            self._receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self._receive_thread.start()

        except (socket.timeout, ConnectionRefusedError, OSError):
            pass  # 连接失败，下次重连循环再试
        except Exception as e:
            logger.warning(f"Connection error: {e}")

    def _receive_loop(self):
        """
        TCP 数据接收循环（在独立线程中运行）
        每收到一行 JSON（以 \\n 分隔），调用 ReadData() → parseJson() 解析。
        [原: connect(socket, &QTcpSocket::readyRead, this, &LandScreen::ReadData)]
        """
        buffer = ""
        while self._running and self.socket:
            try:
                data = self.socket.recv(4096)
                if not data:
                    # 连接断开
                    logger.info("Disconnected from server")
                    self._handle_disconnect()
                    break

                # 追加到缓冲区，按换行符分割处理  [原: socket->readLine().trimmed()]
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.ReadData(line)  # [原: parseJson(data)]
            except (ConnectionResetError, BrokenPipeError, OSError):
                self._handle_disconnect()
                break
            except Exception as e:
                logger.warning(f"Receive error: {e}")
                time.sleep(0.1)

    def _handle_disconnect(self):
        """
        处理 Socket 断开连接
        [原: connect(socket, &QTcpSocket::disconnected, ...)]
        """
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        # 通知前端连接状态 [原: connectStatusLabel->setText("已断开")]
        self._emit_ui("update_connection_status", {"status": "已断开"})

    # ================================================================
    # ReadData() — 读取 TCP 数据
    # ================================================================
    def ReadData(self, data: str):
        """
        处理从 TCP Socket 接收到的一行数据
        [原: void LandScreen::ReadData()]

        参数：
            data : str  — 已去除尾部换行符的单行 JSON 字符串
                         来源于 self._receive_loop() 中 socket.recv() 的数据

        数据去向：parseJson() 解析
        """
        self.parseJson(data)

    # ================================================================
    # parseJson() — JSON 解析
    # ================================================================
    def parseJson(self, json_data: str):
        """
        解析机载端发来的 JSON 数据
        [原: void LandScreen::parseJson(const QByteArray &jsonData)]

        解析内容：
            1. "planner" 数组 → 路径规划航点列表 → wayPoints
               坐标转换公式：
                 a = 8 - round(y / 0.5)    [原: pt.a = 8 - static_cast<qint8>(y/0.5)]
                 b = round(x / 0.5)        [原: pt.b = static_cast<qint8>(x/0.5)]
            2. "tx", "ty", "tn" → 目标位置和名称 → receivedTarget
               网格转换公式：
                 receivedTarget.a = 9 - round(y / 0.5)  [原: 9-static_cast<qint8>(std::round(y/0.5))]
                 receivedTarget.b = round(x / 0.5) + 1   [原: static_cast<qint8>(std::round(x/0.5))+1]

        数据来源：机载 LandScreenNode 通过 TCP 8001 端口发送（10Hz 频率）
        数据去向：
            - wayPoints → 通过回调推送到前端 Canvas 绘制路径
            - receivedTarget → SharedData.addTargetIfNew() → 目标列表
            - → updateTargetSummaryLabel() 更新前端目标汇总标签

        参数：
            json_data : str  — JSON 格式字符串（已去除换行符）
        """
        try:
            obj = json.loads(json_data)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            return

        # 检查是否包含 "planner" 数组  [原: obj.contains("planner") && obj["planner"].isArray()]
        if "planner" not in obj or not isinstance(obj["planner"], list):
            logger.warning("JSON does not contain 'planner' array.")
            return

        plannerArray = obj["planner"]

        # 仅在 wayPoints 为空时解析路径（首次收到路径）  [原: if (wayPoints.empty())]
        if not self.wayPoints:
            for pointObj in plannerArray:
                if not isinstance(pointObj, dict):
                    continue
                if "x" in pointObj and "y" in pointObj:
                    x = float(pointObj["x"])
                    y = float(pointObj["y"])
                    pt = Point()
                    # 网格坐标转换  [原: pt.a = 8-static_cast<qint8>(y/0.5)]
                    pt.a = 8 - int(round(y / 0.5))
                    pt.b = int(round(x / 0.5))
                    logger.debug(f"a: {pt.a} b: {pt.b}")
                    self.wayPoints.append(pt)

            # 发送 wayPointsReady 信号（通过回调推送航点到前端）  [原: emit wayPointsReady()]
            self.drawOnMap_callback()

        # ---------- 目标信息解析 ----------
        sharedData = SharedData.getInstance()
        receivedTarget = sharedData.getChosenTarget()

        if "tx" in obj:
            receivedTarget.x = float(obj["tx"])   # [原: receivedTarget.x = obj["tx"].toDouble()]
        if "ty" in obj:
            receivedTarget.y = float(obj["ty"])   # [原: receivedTarget.y = obj["ty"].toDouble()]
        if "tn" in obj:
            receivedTarget.name = str(obj["tn"])  # [原: receivedTarget.name = obj["tn"].toString()]

        # 网格坐标转换  [原代码]
        receivedTarget.a = 9 - int(round(receivedTarget.y / 0.5))
        receivedTarget.b = int(round(receivedTarget.x / 0.5)) + 1

        # 添加目标到共享列表  [原: data.addTargetIfNew(receivedTarget)]
        sharedData.addTargetIfNew(receivedTarget)

        # 更新前端目标汇总标签  [原: updateTargetSummaryLabel()]
        self.updateTargetSummaryLabel()

    # ================================================================
    # drawOnMap() — 路径绘制（推送到前端）
    # ================================================================
    def drawOnMap_callback(self):
        """
        将航点数据推送到前端进行 Canvas 绘制
        [原: void LandScreen::drawOnMap()]
        [原信号槽: connect(this, &LandScreen::wayPointsReady, this, &LandScreen::drawOnMap)]

        数据流向：
            wayPoints (后台) → WebSocket → 前端 JS drawOnMap()

        说明：
            原 Qt 版本使用 QPainter 在地图 QLabel 上绘制红色连线和箭头。
            Web 版本将航点数组推送到前端，由 JS 在 Canvas 上绘制。
        """
        if not self.wayPoints:
            return

        # 将航点列表转为可序列化的字典列表
        waypoints_data = [pt.to_dict() for pt in self.wayPoints]
        self._emit_ui("draw_waypoints", {"waypoints": waypoints_data})
        logger.debug(f"drawOnMap 完成，wayPoints数: {len(self.wayPoints)}")

    # ================================================================
    # onButtonAClicked() — A 行按钮点击处理
    # ================================================================
    def onButtonAClicked(self, index: int):
        """
        处理 A 行按钮点击事件
        [原: void LandScreen::onButtonAClicked(int index)]

        逻辑：
           1. 更新 selectedButtonA 为当前点击索引
           2. 调用 updateButtonAStyles() 通知前端更新按钮样式
           3. 如果 A 和 B 都有选中，调用 addForbidden() 添加禁飞区

        参数：
            index : int  — 被点击按钮的索引 (0~8，对应 A1~A9)
                           来源于前端 WebSocket 消息 "button_a_click"
        """
        self.selectedButtonA = index
        self.updateButtonAStyles()
        logger.debug(f"A行按钮被点击: {index + 1}")

        # 检查是否 A 和 B 都有选中的按钮  [原: if (selectedButtonA != -1 && selectedButtonB != -1)]
        if self.selectedButtonA != -1 and self.selectedButtonB != -1:
            self.addForbidden()

    # ================================================================
    # onButtonBClicked() — B 行按钮点击处理
    # ================================================================
    def onButtonBClicked(self, index: int):
        """
        处理 B 行按钮点击事件
        [原: void LandScreen::onButtonBClicked(int index)]

        逻辑：
           1. 更新 selectedButtonB 为当前点击索引
           2. 调用 updateButtonBStyles() 通知前端更新按钮样式
           3. 如果 A 和 B 都有选中，调用 addForbidden() 添加禁飞区

        参数：
            index : int  — 被点击按钮的索引 (0~6，对应 B1~B7)
                           来源于前端 WebSocket 消息 "button_b_click"
        """
        self.selectedButtonB = index
        self.updateButtonBStyles()
        logger.debug(f"B行按钮被点击: {index + 1}")

        # 检查是否 A 和 B 都有选中的按钮  [原: if (selectedButtonA != -1 && selectedButtonB != -1)]
        if self.selectedButtonA != -1 and self.selectedButtonB != -1:
            self.addForbidden()

    # ================================================================
    # updateButtonAStyles() — 更新 A 行按钮选中样式
    # ================================================================
    def updateButtonAStyles(self):
        """
        通知前端更新 A 行按钮的选中/未选中样式
        [原: void LandScreen::updateButtonAStyles()]

        说明：
            原 Qt 版本直接修改 QPushButton 的 StyleSheet 和 enabled 属性。
            Web 版本通过 WebSocket 推送 selectedButtonA 索引，前端更新 CSS 类。

        数据流向：selectedButtonA → WebSocket → 前端 CSS 切换
        """
        self._emit_ui("update_button_a_styles", {"selected": self.selectedButtonA})

    # ================================================================
    # updateButtonBStyles() — 更新 B 行按钮选中样式
    # ================================================================
    def updateButtonBStyles(self):
        """
        通知前端更新 B 行按钮的选中/未选中样式
        [原: void LandScreen::updateButtonBStyles()]

        说明：
            原 Qt 版本直接修改 QPushButton 的 StyleSheet 和 enabled 属性。
            Web 版本通过 WebSocket 推送 selectedButtonB 索引，前端更新 CSS 类。

        数据流向：selectedButtonB → WebSocket → 前端 CSS 切换
        """
        self._emit_ui("update_button_b_styles", {"selected": self.selectedButtonB})

    # ================================================================
    # addForbidden() — 添加禁飞区
    # ================================================================
    def addForbidden(self):
        """
        将 A/B 选中的网格坐标添加为禁飞区标签
        [原: void LandScreen::addForbidden()]

        逻辑：
           1. 遍历 labelF1/F2/F3 三个标签
           2. 找到第一个内容为 "NULL" 的标签
           3. 将该标签内容设置为 "禁飞区N（AX,BY）" 格式
           4. 恢复 A/B 按钮的选中状态

        数据流向：
            输入：selectedButtonA, selectedButtonB（用户点击的网格坐标）
            输出：labelF1/F2/F3 → 前端禁飞区标签更新

        禁飞区最多 3 个，填满后额外的选择不会生效。
        """
        logger.debug(
            f"addForbidden() 被调用 - A行按钮: {self.selectedButtonA + 1}, "
            f"B行按钮: {self.selectedButtonB + 1}"
        )

        # 三个禁飞区标签当前状态  [原: QLabel* labels[] = {labelF1, labelF2, labelF3}]
        # 需要通过回调获取前端当前标签文本，这里先假设用内部状态追踪
        # 实际在 app.py 中会维护三个标签的文本状态
        added = self._emit_ui("add_forbidden", {
            "a_index": self.selectedButtonA + 1,  # +1 因为原 UI 显示从 1 开始
            "b_index": self.selectedButtonB + 1
        })

        # 恢复 A 行按钮状态  [原: selectedButtonA = -1; updateButtonAStyles()]
        self.selectedButtonA = -1
        self.updateButtonAStyles()

        # 恢复 B 行按钮状态  [原: selectedButtonB = -1; updateButtonBStyles()]
        self.selectedButtonB = -1
        self.updateButtonBStyles()

        logger.debug("按钮状态已恢复")

    # ================================================================
    # onSendClicked() — 发送按钮处理
    # ================================================================
    def onSendClicked(self):
        """
        处理"发送"按钮点击事件
        [原: void LandScreen::onSendClicked()]

        逻辑：
           1. 从前端三个禁飞区标签文本中提取 A/B 数字
           2. 解析格式："禁飞区N（AX,BY）" → 提取 X, Y
           3. 将坐标值存入 dataSend 字典
           4. 调用 sendData() 通过 TCP 发送给机载端

        数据来源：前端 labelF1/F2/F3 的文本内容（通过回调获取）
        数据去向：TCP 8001 → 机载 LandScreenNode → ROS /nofly_zone 话题

        正则匹配格式：禁飞区\d+（A(\d+),B(\d+)）   [原: QRegularExpression]
        """
        logger.debug("发送按钮被点击")

        # 获取前端三个禁飞区标签的当前文本（通过回调）
        # 这里需要 app.py 层维护 f_labels 状态，我们在此处通过回调发送提取请求
        self._emit_ui("request_f_labels", {})  # 前端返回后调用 _on_receive_f_labels

    def _on_receive_f_labels(self, labels: list):
        """
        收到前端禁飞区标签文本后的处理（由 app.py 调用）
        [原: for循环内联逻辑]

        参数：
            labels : list[str]  — 三个标签的文本 [labelF1_text, labelF2_text, labelF3_text]
        """
        keys = ["f1", "f2", "f3"]  # [原: QString keys[] = {"f1", "f2", "f3"}]

        for i in range(3):
            text = labels[i] if i < len(labels) else "NULL"
            if text != "NULL":
                # 解析 "禁飞区1（A2,B3）" 格式  [原: QRegularExpression regex]
                match = re.search(r"禁飞区\d+（A(\d+),B(\d+)）", text)
                if match:
                    aValue = int(match.group(1))  # [原: match.captured(1).toInt()]
                    bValue = int(match.group(2))  # [原: match.captured(2).toInt()]
                    self.dataSend[keys[i] + "x"] = aValue
                    self.dataSend[keys[i] + "y"] = bValue
                    logger.debug(f"提取 {keys[i]}: A={aValue}, B={bValue}")

        self.sendData()

    # ================================================================
    # onCancelClicked() — 取消按钮处理
    # ================================================================
    def onCancelClicked(self):
        """
        处理"取消"按钮点击事件，清除所有禁飞区标签
        [原: void LandScreen::onCancelClicked()]

        功能：将三个禁飞区标签全部重置为 "NULL"

        数据去向：WebSocket → 前端 labelF1/F2/F3 文本重置
        """
        logger.debug("取消按钮被点击，清除所有标签")

        self._emit_ui("update_f_labels", {
            "f1": "NULL", "f2": "NULL", "f3": "NULL"
        })
        logger.debug("所有标签已重置为NULL")

    # ================================================================
    # sendData() — 发送数据到机载端
    # ================================================================
    def sendData(self):
        """
        将 dataSend 字典序列化为 JSON 并通过 TCP Socket 发送到机载端
        [原: void LandScreen::sendData()]

        数据格式（紧凑 JSON + 换行符）：
            {"f1x":1,"f1y":2,"f2x":-1,"f2y":-1,"f3x":-1,"f3y":-1,"launch":false}\n

        数据来源：dataSend 字典（由 onSendClicked / launch 点击填充）
        数据去向：TCP 8001 → 机载 LandScreenNode → ROS 话题
            - f1x/f1y, f2x/f2y, f3x/f3y → /nofly_zone (Polygon)
            - launch → /launch (Bool)
        """
        try:
            json_str = json.dumps(self.dataSend, separators=(",", ":"))
            json_str += "\n"
            json_bytes = json_str.encode("utf-8")

            if self.socket:
                try:
                    self.socket.sendall(json_bytes)
                    logger.debug(f"LandScreen sent data: {json_str.strip()}")
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    logger.warning(f"Send failed: {e}")
                    self._handle_disconnect()
            else:
                logger.debug("Socket not connected, cannot send data")
        except Exception as e:
            logger.error(f"sendData error: {e}")

    # ================================================================
    # onLaunchClicked() — 启动按钮处理
    # ================================================================
    def onLaunchClicked(self):
        """
        处理"启动"按钮点击事件
        [原: launchButton clicked 槽函数]

        逻辑：
           1. 设置 dataSend["launch"] = True
           2. 检查是否有禁飞区信息（至少一个禁飞区的 x 坐标非 -1）
           3. 如果没有禁飞区，弹出警告并返回
           4. 如果有禁飞区，调用 sendData() 发送

        警告弹窗通过 callback 推送到前端显示。
        """
        self.dataSend["launch"] = True

        # 检查是否有禁飞区信息  [原: if(dataSend["f1x"]==-1 && dataSend["f2x"]==-1 && dataSend["f3x"]==-1)]
        if (self.dataSend["f1x"] == -1
                and self.dataSend["f2x"] == -1
                and self.dataSend["f3x"] == -1):
            # 弹窗警告 [原: QMessageBox::warning(this, "警告", "没有禁飞区信息，无法启动")]
            self._emit_ui("show_alert", {
                "title": "警告",
                "message": "没有禁飞区信息，无法启动"
            })
            return

        self.sendData()

    # ================================================================
    # updateTargetSummaryLabel() — 更新目标汇总标签
    # ================================================================
    def updateTargetSummaryLabel(self):
        """
        更新前端的目标汇总标签文本
        [原: void LandScreen::updateTargetSummaryLabel()]

        显示格式："{name}, A:{a}, B:{b}, 数量：{n}"
        如果当前目标名称为 "NULL"，显示 "暂无目标信息"

        数据来源：SharedData.getChosenTarget()（最近接收到的目标）
        数据去向：WebSocket → 前端 labelTargetSummary
        """
        sharedData = SharedData.getInstance()
        with sharedData.getMutex():
            t = sharedData.getChosenTarget()

        if t.name != "NULL":
            # [原: labelTargetSummary->setText(QString("%1, A:%2, B:%3, 数量：%4").arg(t.name).arg(t.a).arg(t.b).arg(t.n))]
            text = f"{t.name}, A:{t.a}, B:{t.b}, 数量：{t.n}"
        else:
            text = "暂无目标信息"

        self._emit_ui("update_target_summary", {"text": text})

    # ================================================================
    # loadTargets() — 加载目标列表（供 TargetInfo 页面使用）
    # ================================================================
    def loadTargets(self):
        """
        获取当前所有目标列表（供前端 TargetInfo 页面显示）
        [原: void TargetInfo::loadTargets()]

        数据来源：SharedData.targets_（所有已记录的目标）
        数据去向：WebSocket → 前端 TargetInfo 页面

        返回：过滤掉 name=="NULL" 的目标列表
        """
        sharedData = SharedData.getInstance()
        with sharedData.getMutex():
            targets = list(sharedData.getTargets())

        # 过滤掉 NULL 目标并转换为字典列表
        result = []
        for t in targets:
            if t.name != "NULL":
                result.append(t.to_dict())
                logger.debug(f"name {t.name} x {t.x} y {t.y}")

        return result

    def getTargetStatistics(self):
        """
        获取目标统计信息
        [原: void TargetInfo::updateStatistics(const std::vector<Target>& targets)]

        返回：
            dict: {
                "type_counts": {"类型名": 总数, ...},  # 各类型目标的 n 值总和
                "total": 总计
            }
        """
        sharedData = SharedData.getInstance()
        with sharedData.getMutex():
            targets = list(sharedData.getTargets())

        typeSum = {}
        for t in targets:
            if t.name != "NULL":
                typeSum[t.name] = typeSum.get(t.name, 0) + t.n

        totalSum = sum(typeSum.values())

        return {
            "type_counts": typeSum,
            "total": totalSum
        }

    # ================================================================
    # onRescueButtonClicked() — 救援目标选择
    # ================================================================
    def onRescueButtonClicked(self, target_data: dict):
        """
        处理目标信息页面中救援按钮的点击
        [原: void TargetInfo::onRescueButtonClicked(const Target& target)]

        将选中的目标信息更新到 SharedData.target_chosen_

        参数：
            target_data : dict  — {"x": float, "y": float, "name": str}

        数据来源：前端 TargetInfo 页面的救援按钮
        数据去向：SharedData.target_chosen_（被选中的救援目标）
        """
        sharedData = SharedData.getInstance()
        with sharedData.getMutex():
            chosenTarget = sharedData.getChosenTarget()
            chosenTarget.x = target_data.get("x", -1)
            chosenTarget.y = target_data.get("y", -1)
            chosenTarget.name = target_data.get("name", "NULL")

    # ================================================================
    # shutdown() — 关闭清理
    # ================================================================
    def shutdown(self):
        """
        关闭地面站，停止所有线程并断开连接
        """
        self._running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        logger.info("LandScreen shutdown")
