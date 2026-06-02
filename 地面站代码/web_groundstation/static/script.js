/*
 * script.js — 地面站前端交互逻辑
 *
 * 功能说明：
 *     处理所有前端用户交互、WebSocket 通信、Canvas 地图绘制。
 *     完整复现原 Qt LandScreen 的信号槽机制。
 *
 * 源码映射：
 *     connect(socket, readyRead, ...)    → socket.on("draw_waypoints", ...)
 *     connect(buttonsA[i], clicked, ...) → btn.addEventListener("click", ...)
 *     drawOnMap() QPainter              → Canvas 2D API
 *     updateButtonAStyles()             → updateButtonStyles("a", ...)
 *     QMessageBox::warning              → showAlert()
 *     Blink::paintEvent()               → blinkCommand handler
 */

// ============================================================
// 全局状态变量
// ============================================================

// socket : WebSocket 连接对象 [原: QTcpSocket *socket]
let socket = null;

// originalMapPixmap : 地图图片 Image 对象 [原: QPixmap originalMapPixmap]
let originalMapPixmap = null;

// mapLoaded : 地图是否已成功加载
let mapLoaded = false;

// selectedButtonA : A 行选中按钮索引，-1 表示无选中 [原: int selectedButtonA = -1]
let selectedButtonA = -1;

// selectedButtonB : B 行选中按钮索引，-1 表示无选中 [原: int selectedButtonB = -1]
let selectedButtonB = -1;

// wayPoints : 路径航点列表 [原: std::vector<Point> wayPoints]
let wayPoints = [];

// F_labels : 禁飞区标签文本状态 [原: labelF1/F2/F3 的 text()]
let F_labels = { f1: "NULL", f2: "NULL", f3: "NULL" };

// canvas context
let mapCanvas = null;
let mapCtx = null;

// 网格参数 [原: const int cols = 9, rows = 7]
const COLS = 9;   // A 列数
const ROWS = 7;   // B 行数

// ============================================================
// 初始化 — DOM 加载完成后执行
// ============================================================
document.addEventListener("DOMContentLoaded", function () {

    // ---------- 获取 DOM 元素引用 ----------

    // mapLabel : 地图 Canvas [原: QLabel *mapLabel]
    mapCanvas = document.getElementById("mapCanvas");
    mapCtx = mapCanvas.getContext("2d");

    // labelF1/F2/F3 : 禁飞区标签 [原: QLabel *labelF1/F2/F3]
    const labelF1 = document.getElementById("labelF1");
    const labelF2 = document.getElementById("labelF2");
    const labelF3 = document.getElementById("labelF3");

    // sendButton : 发送按钮 [原: QPushButton *sendButton]
    const sendButton = document.getElementById("sendButton");
    // cancelButton : 取消按钮 [原: QPushButton *cancelButton]
    const cancelButton = document.getElementById("cancelButton");
    // launchButton : 启动按钮 [原: QPushButton *launchButton]
    const launchButton = document.getElementById("launchButton");

    // labelTargetSummary : 目标汇总标签 [原: QLabel *labelTargetSummary]
    const labelTargetSummary = document.getElementById("labelTargetSummary");
    // connectStatusLabel : 连接状态标签 [原: QLabel *connectStatusLabel]
    const connectStatusLabel = document.getElementById("connectStatusLabel");
    // showTargetInfoButton : 显示目标信息按钮 [原: QPushButton *showTargetInfoButton]
    const showTargetInfoButton = document.getElementById("showTargetInfoButton");

    // 闪烁覆盖层 [原: Blink widget]
    const blinkOverlay = document.getElementById("blinkOverlay");

    // 警告弹窗 [原: QMessageBox]
    const alertOverlay = document.getElementById("alertOverlay");
    const alertTitle = document.getElementById("alertTitle");
    const alertMessage = document.getElementById("alertMessage");
    const alertClose = document.getElementById("alertClose");

    // ---------- 加载地图图片 ----------
    // [原: originalMapPixmap = QPixmap("../map.png")]
    loadMapImage();

    // ---------- 建立 WebSocket 连接 ----------
    // [原: initSocket() → socket->connectToHost(SERVER_IP, SERVER_PORT)]
    connectWebSocket();

    // ---------- 事件绑定 ----------

    // 发送按钮点击 [原: connect(sendButton, &QPushButton::clicked, ...)]
    sendButton.addEventListener("click", function () {
        onSendClicked();
    });

    // 取消按钮点击 [原: connect(cancelButton, &QPushButton::clicked, ...)]
    cancelButton.addEventListener("click", function () {
        onCancelClicked();
    });

    // 启动按钮点击 [原: connect(launchButton, &QPushButton::clicked, ...)]
    launchButton.addEventListener("click", function () {
        onLaunchClicked();
    });

    // 显示目标信息按钮点击 [原: connect(showTargetInfoButton, &QPushButton::clicked, ...)]
    showTargetInfoButton.addEventListener("click", function () {
        showTargetInfo();
    });

    // 告警弹窗关闭 [原: QDialog::accept]
    alertClose.addEventListener("click", function () {
        alertOverlay.classList.add("hidden");
    });

    // A 行网格按钮 [原: connect(buttonsA[i], &QPushButton::clicked, ...)]
    document.querySelectorAll(".btn-a").forEach(function (btn) {
        btn.addEventListener("click", function () {
            const index = parseInt(btn.getAttribute("data-index"));
            onButtonAClicked(index);
        });
    });

    // B 行网格按钮 [原: connect(buttonsB[i], &QPushButton::clicked, ...)]
    document.querySelectorAll(".btn-b").forEach(function (btn) {
        btn.addEventListener("click", function () {
            const index = parseInt(btn.getAttribute("data-index"));
            onButtonBClicked(index);
        });
    });

    // ============================================================
    // 事件处理函数（本地）
    // ============================================================

    /**
     * onSendClicked() — 发送按钮点击处理
     * [原: void LandScreen::onSendClicked()]
     *
     * 将当前 F_labels 状态通过 WebSocket 发送给服务器，
     * 服务器提取 A/B 坐标后通过 TCP 发送到机载端。
     */
    function onSendClicked() {
        console.log("发送按钮被点击");
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.emit("send_click");
        }
    }

    /**
     * onCancelClicked() — 取消按钮点击处理
     * [原: void LandScreen::onCancelClicked()]
     *
     * 将所有禁飞区标签重置为 "NULL"。
     */
    function onCancelClicked() {
        console.log("取消按钮被点击，清除所有标签");
        F_labels = { f1: "NULL", f2: "NULL", f3: "NULL" };
        labelF1.textContent = "NULL";
        labelF2.textContent = "NULL";
        labelF3.textContent = "NULL";
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.emit("cancel_click");
        }
    }

    /**
     * onLaunchClicked() — 启动按钮点击处理
     * [原: launchButton clicked 槽]
     *
     * 通过 WebSocket 发送启动指令，服务器检查禁飞区后通过 TCP 发送。
     */
    function onLaunchClicked() {
        console.log("启动按钮被点击");
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.emit("launch_click");
        }
    }

    /**
     * onButtonAClicked(index) — A 行按钮点击处理
     * [原: void LandScreen::onButtonAClicked(int index)]
     *
     * 更新本地按钮样式并通过 WebSocket 通知服务器。
     *
     * 参数: index : int — 按钮索引 (0~8)
     */
    function onButtonAClicked(index) {
        selectedButtonA = index;
        updateButtonAStyles();
        console.log("A行按钮被点击:", index + 1);
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.emit("button_a_click", { index: index });
        }
    }

    /**
     * onButtonBClicked(index) — B 行按钮点击处理
     * [原: void LandScreen::onButtonBClicked(int index)]
     *
     * 更新本地按钮样式并通过 WebSocket 通知服务器。
     *
     * 参数: index : int — 按钮索引 (0~6)
     */
    function onButtonBClicked(index) {
        selectedButtonB = index;
        updateButtonBStyles();
        console.log("B行按钮被点击:", index + 1);
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.emit("button_b_click", { index: index });
        }
    }

    /**
     * updateButtonAStyles() — 更新 A 行按钮样式
     * [原: void LandScreen::updateButtonAStyles()]
     *
     * 选中按钮高亮为蓝色，其他恢复白色，选中按钮禁用点击。
     */
    function updateButtonAStyles() {
        document.querySelectorAll(".btn-a").forEach(function (btn) {
            const idx = parseInt(btn.getAttribute("data-index"));
            if (idx === selectedButtonA) {
                btn.classList.add("selected");
                btn.disabled = true;       // [原: setEnabled(false)]
            } else {
                btn.classList.remove("selected");
                btn.disabled = false;      // [原: setEnabled(true)]
            }
        });
    }

    /**
     * updateButtonBStyles() — 更新 B 行按钮样式
     * [原: void LandScreen::updateButtonBStyles()]
     *
     * 选中按钮高亮为蓝色，其他恢复白色，选中按钮禁用点击。
     */
    function updateButtonBStyles() {
        document.querySelectorAll(".btn-b").forEach(function (btn) {
            const idx = parseInt(btn.getAttribute("data-index"));
            if (idx === selectedButtonB) {
                btn.classList.add("selected");
                btn.disabled = true;
            } else {
                btn.classList.remove("selected");
                btn.disabled = false;
            }
        });
    }

    /**
     * showTargetInfo() — 显示目标信息页面
     * [原: targetInfoDialog->showFullScreen()]
     *
     * 在新窗口/标签页打开目标信息页面。
     */
    function showTargetInfo() {
        window.open("/target_info", "_blank", "width=800,height=600");
    }

    /**
     * showAlert(title, message) — 显示警告弹窗
     * [原: QMessageBox::warning(this, title, message)]
     *
     * 参数: title   : str — 弹窗标题
     *       message : str — 弹窗内容
     */
    function showAlert(title, message) {
        alertTitle.textContent = title;
        alertMessage.textContent = message;
        alertOverlay.classList.remove("hidden");
    }

    // ============================================================
    // WebSocket 连接
    // ============================================================
    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = protocol + "//" + window.location.host;

        socket = io(wsUrl, {
            transports: ["websocket", "polling"],
            reconnection: true,
            reconnectionDelay: 500,      // 0.5 秒重连 [原: reconnectTimer->setInterval(500)]
            reconnectionAttempts: Infinity
        });

        // --- WebSocket 事件监听 ---

        // 连接成功 [原: connect(socket, &QTcpSocket::connected, ...)]
        socket.on("connect", function () {
            console.log("WebSocket 已连接");
        });

        // 连接断开
        socket.on("disconnect", function () {
            console.log("WebSocket 已断开");
        });

        // --- 服务器推送事件处理 ---

        /**
         * update_f_labels — 更新禁飞区标签文本
         * [原: labelF1/F2/F3->setText()]
         *
         * 服务器在 addForbidden() / onCancelClicked() 后推送
         */
        socket.on("update_f_labels", function (data) {
            F_labels = data;
            labelF1.textContent = data.f1 || "NULL";
            labelF2.textContent = data.f2 || "NULL";
            labelF3.textContent = data.f3 || "NULL";
        });

        /**
         * update_button_a_styles — 更新 A 行按钮样式
         * [原: signals → updateButtonAStyles()]
         *
         * 服务器在 onButtonAClicked 处理后回传选中索引
         */
        socket.on("update_button_a_styles", function (data) {
            selectedButtonA = data.selected;
            updateButtonAStyles();
        });

        /**
         * update_button_b_styles — 更新 B 行按钮样式
         * [原: signals → updateButtonBStyles()]
         *
         * 服务器在 onButtonBClicked 处理后回传选中索引
         */
        socket.on("update_button_b_styles", function (data) {
            selectedButtonB = data.selected;
            updateButtonBStyles();
        });

        /**
         * update_connection_status — 更新 TCP 连接状态
         * [原: connectStatusLabel->setText("已连接"/"已断开")]
         *
         * 数据来源：LandScreen.initSocket() 中的连接/断线处理
         */
        socket.on("update_connection_status", function (data) {
            connectStatusLabel.textContent = data.status || "未连接";
        });

        /**
         * update_target_summary — 更新目标汇总标签
         * [原: labelTargetSummary->setText(...)]
         *
         * 数据来源：LandScreen.updateTargetSummaryLabel()
         */
        socket.on("update_target_summary", function (data) {
            labelTargetSummary.textContent = data.text || "暂无目标信息";
        });

        /**
         * draw_waypoints — 绘制路径航点
         * [原: void LandScreen::drawOnMap() → QPainter]
         *
         * 使用 Canvas 2D API 替代 QPainter 绘制：
         *   - 红色连线连接各航点中心
         *   - 线段中点绘制箭头指示方向
         *
         * 数据来源：LandScreen.parseJson() 解析 "planner" 数组后推送
         */
        socket.on("draw_waypoints", function (data) {
            wayPoints = data.waypoints || [];
            drawOnMap();
        });

        /**
         * blink_command — 闪烁告警指令
         * [原: Blink::toggleColor() → paintEvent() → fillRect()]
         *
         * 控制全屏闪烁覆盖层的显示/隐藏。
         */
        socket.on("blink_command", function (data) {
            if (data.is_lighted) {
                blinkOverlay.style.backgroundColor = data.color || "red";
                blinkOverlay.classList.add("blink-on");
            } else {
                blinkOverlay.classList.remove("blink-on");
            }
        });

        /**
         * show_alert — 显示警告弹窗
         * [原: QMessageBox::warning()]
         *
         * 数据来源：LandScreen.onLaunchClicked() 中无禁飞区时弹出
         */
        socket.on("show_alert", function (data) {
            showAlert(data.title || "警告", data.message || "");
        });

        /**
         * load_targets — 加载目标信息（用于 TargetInfo 页面）
         * [原: TargetInfo::loadTargets()]
         */
        socket.on("load_targets", function () {
            // TargetInfo 页面通过 REST API 独立加载数据
            console.log("收到加载目标信息请求");
        });
    }

    // ============================================================
    // Canvas 地图绘制
    // ============================================================

    /**
     * loadMapImage() — 加载地图图片
     * [原: originalMapPixmap = QPixmap("../map.png")]
     *
     * 数据来源：Flask 路由 /map.png 提供的地图文件
     * 数据去向：Canvas 背景绘制
     */
    function loadMapImage() {
        originalMapPixmap = new Image();
        originalMapPixmap.onload = function () {
            mapLoaded = true;
            drawMapBackground();
            drawOnMap();  // 如果已有航点数据，立即绘制
        };
        originalMapPixmap.onerror = function () {
            console.warn("无法加载地图文件: map.png");
            mapPlaceholder.style.display = "block";
        };
        originalMapPixmap.src = "/map.png";
    }

    /**
     * drawMapBackground() — 绘制地图背景
     * [原: mapLabel->setPixmap(scaledPixmap)]
     *
     * 将地图图片缩放至 Canvas 尺寸 (360x280) 并绘制为背景。
     */
    function drawMapBackground() {
        if (!mapLoaded || !mapCtx) return;
        const w = mapCanvas.width;    // 360
        const h = mapCanvas.height;   // 280
        mapCtx.clearRect(0, 0, w, h);
        // 等比缩放绘制 [原: QPixmap::scaled(360, 280, Qt::KeepAspectRatio)]
        mapCtx.drawImage(originalMapPixmap, 0, 0, w, h);
    }

    /**
     * drawOnMap() — 在地图上绘制路径航点
     * [原: void LandScreen::drawOnMap()]
     *
     * 原 Qt 版本使用 QPainter 绘制：
     *   - 红色线条连接各航点
     *   - 线段中点绘制箭头指示方向
     *
     * 坐标转换公式 [原]:
     *   cx = (a + 0.5) * cellWidth              // 单元格中心 X
     *   cy = mapHeight - (b + 0.5) * cellHeight // 单元格中心 Y (翻转Y轴)
     *
     * 输入：wayPoints 全局数组 [{a, b}, ...]
     * 输出：Canvas 绘制结果
     */
    function drawOnMap() {
        if (!mapLoaded || !mapCtx || wayPoints.length === 0) return;

        drawMapBackground();  // 先重绘背景，清除旧路径

        const mapWidth = mapCanvas.width;    // 360
        const mapHeight = mapCanvas.height;  // 280
        const cellWidth = mapWidth / COLS;    // 网格单元宽度
        const cellHeight = mapHeight / ROWS;  // 网格单元高度

        // 计算各航点的网格中心坐标
        const centers = [];
        for (let i = 0; i < wayPoints.length; i++) {
            const pt = wayPoints[i];
            const a = pt.a;
            const b = pt.b;

            // 边界检查 [原: if (a<0 || a>=cols || b<0 || b>=rows)]
            if (a < 0 || a >= COLS || b < 0 || b >= ROWS) {
                console.log("无效的坐标点: a=" + a + " b=" + b);
                continue;
            }

            // 单元格中心 X  [原: cx = ((a + 0.5) * cellWidth)]
            let cx = (a + 0.5) * cellWidth;
            // 单元格中心 Y（翻转） [原: cy = (mapHeight - (b + 0.5) * cellHeight)]
            let cy = mapHeight - (b + 0.5) * cellHeight;

            // 裁剪到画布范围 [原: cx = qBound(0.0, cx, mapWidth)]
            cx = Math.max(0, Math.min(cx, mapWidth));
            cy = Math.max(0, Math.min(cy, mapHeight));

            centers.push({ x: cx, y: cy });
        }

        // 设置画笔样式 [原: QPen pen(Qt::red, 4)]
        mapCtx.save();
        mapCtx.strokeStyle = "red";
        mapCtx.lineWidth = 4;
        mapCtx.lineCap = "round";
        mapCtx.lineJoin = "round";

        // 依次连接中心点并在中点画箭头 [原: for (int i = 1; i < centers.size(); ++i)]
        for (let i = 1; i < centers.length; i++) {
            const p1 = centers[i - 1];
            const p2 = centers[i];

            // 绘制连线 [原: painter.drawLine(p1, p2)]
            mapCtx.beginPath();
            mapCtx.moveTo(p1.x, p1.y);
            mapCtx.lineTo(p2.x, p2.y);
            mapCtx.stroke();

            // 绘制箭头（线段中点） [原: QPointF mid = (p1 + p2) / 2]
            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            const angle = Math.atan2(p2.y - p1.y, p2.x - p1.x);  // [原: std::atan2]
            const arrowLen = 10;            // [原: double arrowLen = 10]
            const arrowAngle = Math.PI / 7; // [原: double arrowAngle = M_PI / 7 约25度]

            // 箭头左翼 [原: arrowP1]
            const arrowP1x = midX - arrowLen * Math.cos(angle - arrowAngle);
            const arrowP1y = midY - arrowLen * Math.sin(angle - arrowAngle);
            // 箭头右翼 [原: arrowP2]
            const arrowP2x = midX - arrowLen * Math.cos(angle + arrowAngle);
            const arrowP2y = midY - arrowLen * Math.sin(angle + arrowAngle);

            // 绘制箭头两翼 [原: painter.drawLine(mid, arrowP1); painter.drawLine(mid, arrowP2)]
            mapCtx.beginPath();
            mapCtx.moveTo(midX, midY);
            mapCtx.lineTo(arrowP1x, arrowP1y);
            mapCtx.stroke();

            mapCtx.beginPath();
            mapCtx.moveTo(midX, midY);
            mapCtx.lineTo(arrowP2x, arrowP2y);
            mapCtx.stroke();
        }

        mapCtx.restore();

        console.log("drawOnMap 完成，wayPoints数:", wayPoints.length);
    }
});
