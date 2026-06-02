"""
blink.py — 全屏闪烁告警模块

功能说明：
    提供全屏闪烁视觉告警功能（单例模式）。
    原 Qt 版本通过 paintEvent 绘制全屏彩色闪烁矩形；
    Web 版本通过 WebSocket 通知前端执行 CSS 动画闪烁。

源码映射：
    原 C++ 文件: blink.h + blink.cpp
    原 Class:    class Blink  →  Python Blink 类
    原函数:     getInstance()  →  getInstance()
               toggleColor()  →  toggleColor() (保留函数名，内部触发前端闪烁)
    原变量:     instance, isLighted, color, timer  — 全部保留

数据来源：外部调用 getInstance(time, color) 触发闪烁
数据去向：通过 callback 函数发送 WebSocket 消息给浏览器前端
"""

import threading
import time


class Blink:
    """
    全屏闪烁告警单例类

    对应原 C++ 变量：
        instance    : static Blink*  — 单例实例指针
        isLighted   : bool           — 当前是否为亮色状态（true=红色，false=白色）
        color       : QColor         — 闪烁颜色
        timer       : QTimer*        — 定时器，每 500ms 触发 toggleColor()

    Web 版实现说明：
        原 Qt 版本通过 QTimer 每 500ms 切换颜色并重绘全屏窗口。
        Web 版本通过设置回调函数，在 toggleColor() 中调用回调发送 WebSocket
        指令给前端，由前端 CSS 动画实现闪烁效果。
    """

    # ---------- 单例成员 ----------
    instance = None             # 单例实例指针  [原: static Blink* instance]
    _lock = threading.Lock()    # 单例创建锁

    @staticmethod
    def getInstance(time_ms: int, color: str = "red"):
        """
        获取 Blink 单例实例（若不存在则创建）  [原: static Blink* getInstance(int32_t time, QColor color, QWidget* parent)]

        参数：
            time_ms : int   — 闪烁持续时间（毫秒），超时后自动停止  [原: int32_t time]
            color   : str   — 闪烁颜色，支持 "red" / "green" / "blue" 等  [原: QColor color]

        返回：Blink 单例实例
        """
        if Blink.instance is None:
            with Blink._lock:
                if Blink.instance is None:
                    Blink.instance = Blink(time_ms, color)
        return Blink.instance

    def __init__(self, time_ms: int, color: str):
        """
        私有构造函数  [原: Blink::Blink(int32_t time, QColor color, QWidget* parent)]

        启动定时器，每 500ms 切换一次闪烁状态（toggleColor）。
        到达指定时间后自动停止闪烁。

        参数：
            time_ms : int   — 闪烁总持续时间（毫秒）
            color   : str   — 闪烁颜色
        """
        # isLighted : 当前是否为亮色  [原: bool isLighted = true]
        self.isLighted: bool = True

        # color : 闪烁颜色  [原: QColor color]
        self.color: str = color

        # timer : 闪烁定时器线程  [原: QTimer *timer]
        self.timer: threading.Timer | None = None

        # _callback : WebSocket 回调函数，用于向前端发送闪烁指令
        self._callback = None

        # _running : 是否正在闪烁
        self._running: bool = False

        # _timer_interval : 闪烁切换间隔（秒），原 QTimer 500ms  [原: timer->start(500)]
        self._timer_interval: float = 0.5

        # 启动闪烁（通过线程模拟 QTimer）
        self._start_blink(time_ms)

    def set_callback(self, callback):
        """
        设置闪烁状态变化时的回调函数，用于向前端发送 WebSocket 消息
        参数：
            callback : callable  — 接收 (is_blink: bool, color: str) 的回调函数
        """
        self._callback = callback

    def __del__(self):
        """
        析构函数，停止闪烁并重置单例  [原: Blink::~Blink()]
        """
        self._running = False
        if self.instance is self:
            Blink.instance = None

    def toggleColor(self):
        """
        切换闪烁颜色（亮/灭交替）  [原: void Blink::toggleColor()]

        说明：
            原 Qt 版每 500ms 由 QTimer 触发，切换 isLighted 并重绘窗口。
            Web 版通过回调通知前端切换 CSS 类来改变页面背景色。
        """
        self.isLighted = not self.isLighted
        if self._callback:
            # 通知前端：isLighted=True 显示 color 色，False 显示白色/正常
            self._callback(self.isLighted, self.color)

    def _start_blink(self, time_ms: int):
        """
        启动闪烁定时器线程
        模拟原 QTimer 每 500ms 触发 toggleColor()，并在 time_ms 后自动停止。
        [原: timer->start(500) + QTimer::singleShot(time, close)]
        """

        def _blink_loop():
            """闪烁循环线程"""
            start_time = time.time()
            self._running = True
            while self._running:
                elapsed = (time.time() - start_time) * 1000
                if elapsed >= time_ms:
                    break
                self.toggleColor()
                time.sleep(self._timer_interval)
            # 闪烁结束，通知前端恢复正常
            if self._callback:
                self._callback(False, "none")
            Blink.instance = None

        blink_thread = threading.Thread(target=_blink_loop, daemon=True)
        blink_thread.start()
