#!/usr/bin/env python3
"""
mock_tcp_server.py — 模拟机载端 TCP 服务（用于地面站独立测试）

功能说明：
    在没有真实机载计算机的情况下，模拟 LandScreenNode（TCP 8001端口）的行为，
    用于测试地面站 Web 服务的全部功能。

模拟行为：
    1. 监听 TCP 8001 端口，等待地面站连接
    2. 每 100ms (10Hz) 向地面站发送模拟数据：
       - 规划路径 (planner) — 穿越 A/B 网格的航点序列
       - 目标检测数据 (tx, ty, tn) — 模拟色块检测结果
    3. 接收地面站发来的禁飞区坐标 + 启动指令，打印到控制台

通信协议（与真实机载端完全一致）：
    出站（→ 地面站）:
        {"tx":float, "ty":float, "tn":"str", "planner":[{"x":float,"y":float},...]}\n
    入站（← 地面站）:
        {"f1x":int,"f1y":int,"f2x":int,"f2y":int,"f3x":int,"f3y":int,"launch":bool}\n

使用方式：
    python mock_tcp_server.py
    python mock_tcp_server.py --port 8001 --interval 0.1

启动后，再启动地面站 Web 服务即可测试全部功能。
"""

import socket
import json
import time
import threading
import argparse
import random
import sys

# ============================================================
# 配置
# ============================================================
HOST = "0.0.0.0"     # 监听所有网络接口
PORT = 8001           # 与机载 LandScreenNode 端口一致
TX_INTERVAL = 0.1     # 100ms = 10Hz 发送频率

# ============================================================
# 模拟数据生成
# ============================================================

# 模拟的目标类型列表（颜色/种类）
TARGET_NAMES = ["red", "blue", "green", "yellow", "orange", "purple"]

# 模拟规划路径：一条穿越 A/B 网格的路径
# 世界坐标 (x, y)，覆盖9列7行网格区域
# 每个网格单元 0.5m x 0.5m
SIMULATED_PLANNER = [
    {"x": 0.25, "y": 0.25},   # 起点 (A=1, B=1 附近)
    {"x": 1.25, "y": 0.25},   # →
    {"x": 1.75, "y": 0.75},   # ↗
    {"x": 2.25, "y": 0.75},   # →
    {"x": 2.75, "y": 1.25},   # ↗
    {"x": 3.25, "y": 1.25},   # →
    {"x": 3.75, "y": 1.75},   # ↗
    {"x": 4.25, "y": 2.25},   # ↗
    {"x": 3.75, "y": 2.75},   # ↖
    {"x": 2.75, "y": 2.75},   # ←
    {"x": 2.25, "y": 2.25},   # ↙
    {"x": 1.25, "y": 2.75},   # ↗
    {"x": 0.75, "y": 3.25},   # ↗
]

# 模拟目标数据：在地图的不同位置产生目标检测
# 格式: (tx, ty, tn) — 世界坐标x, 世界坐标y, 目标名称
SIMULATED_TARGETS = [
    (-0.75, 0.25, "red"),      # A=6, B=1
    (0.25, 0.75, "blue"),      # A=7, B=2
    (0.75, 0.75, "red"),       # A=7, B=2 (同位置第二个red, n会累计为2)
    (1.25, 1.25, "green"),     # A=6, B=3
    (1.75, 1.75, "yellow"),    # A=5, B=4
    (2.25, 1.25, "red"),       # A=6, B=3 (不同位置red, 新增条目)
    (2.75, 2.25, "blue"),      # A=4, B=5
    (3.25, 2.75, "green"),     # A=3, B=6
    (0.25, 2.25, "orange"),    # A=4, B=5
    (1.75, 3.25, "purple"),    # A=2, B=7
    (3.75, 0.75, "red"),       # A=7, B=2 (同网格, 距上次>0.08m, n累计)
    (4.25, 0.25, "blue"),      # A=8, B=1
    (0.25, 0.25, "yellow"),    # A=8, B=1 (网格a=9,b=1是起点,会被过滤)
]


class MockLandScreenNode:
    """模拟机载 LandScreenNode 的行为"""

    def __init__(self, host="0.0.0.0", port=8001, interval=0.1):
        self.host = host
        self.port = port
        self.interval = interval
        self.server_sock = None
        self.client_sock = None
        self.running = False
        self.send_index = 0  # 当前发送的目标索引

    def start(self):
        """启动模拟服务器"""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(1)
        self.server_sock.settimeout(1.0)

        print(f"[MockServer] 模拟机载端 TCP 服务器启动")
        print(f"[MockServer] 监听地址: {self.host}:{self.port}")
        print(f"[MockServer] 等待地面站连接...")
        print(f"[MockServer] 按 Ctrl+C 停止")
        print("-" * 60)

        self.running = True
        try:
            while self.running:
                try:
                    self.client_sock, addr = self.server_sock.accept()
                    print(f"\n[MockServer] ✅ 地面站已连接: {addr[0]}:{addr[1]}")
                    print(f"[MockServer] 开始发送模拟数据 (10Hz)...")
                    self._handle_client()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        print(f"[MockServer] 连接异常: {e}")
        except KeyboardInterrupt:
            print("\n[MockServer] 收到中断信号")
        finally:
            self.stop()

    def _handle_client(self):
        """处理单个客户端连接"""
        self.send_index = 0
        recv_buffer = ""
        send_thread = threading.Thread(target=self._send_loop, daemon=True)
        send_thread.start()

        try:
            while self.running and self.client_sock:
                try:
                    data = self.client_sock.recv(4096)
                    if not data:
                        print("[MockServer] ❌ 地面站已断开")
                        break

                    recv_buffer += data.decode("utf-8", errors="ignore")
                    while "\n" in recv_buffer:
                        line, recv_buffer = recv_buffer.split("\n", 1)
                        line = line.strip()
                        if line:
                            self._process_received(line)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    print("[MockServer] ❌ 连接中断")
                    break
                except Exception as e:
                    time.sleep(0.01)
        finally:
            if self.client_sock:
                try:
                    self.client_sock.close()
                except Exception:
                    pass
                self.client_sock = None
            print("[MockServer] 等待新的地面站连接...")

    def _send_loop(self):
        """10Hz 发送循环，模拟机载端定时推送"""
        while self.running and self.client_sock:
            try:
                data = self._generate_send_data()
                json_str = json.dumps(data, ensure_ascii=False) + "\n"
                self.client_sock.sendall(json_str.encode("utf-8"))
                self.send_index += 1
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
            except Exception as e:
                print(f"[MockServer] 发送异常: {e}")
                break
            time.sleep(self.interval)

    def _generate_send_data(self):
        """
        生成模拟发送数据
        随机选择一个目标检测结果，始终附带完整规划路径。

        返回格式 (与原协议一致):
            {"tx":float, "ty":float, "tn":"str", "planner":[{...},...]}
        """
        # 循环使用模拟目标数据
        target_idx = self.send_index % len(SIMULATED_TARGETS)
        tx, ty, tn = SIMULATED_TARGETS[target_idx]

        # 随机微调坐标（±0.05m），模拟真实传感器的噪声
        tx += random.uniform(-0.03, 0.03)
        ty += random.uniform(-0.03, 0.03)

        return {
            "tx": round(tx, 4),
            "ty": round(ty, 4),
            "tn": tn,
            "planner": SIMULATED_PLANNER
        }

    def _process_received(self, json_str: str):
        """
        解析并显示地面站发来的数据
        接收格式: {"f1x":int,"f1y":int,"f2x":int,"f2y":int,
                     "f3x":int,"f3y":int,"launch":bool}
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            print(f"[MockServer] ⚠ 收到无效 JSON: {json_str[:80]}")
            return

        # 打印禁飞区信息
        nofly_info = []
        for i in range(1, 4):
            fx = data.get(f"f{i}x", -1)
            fy = data.get(f"f{i}y", -1)
            if fx > 0 and fy > 0:
                nofly_info.append(f"F{i}: (A{fx}, B{fy})")

        if nofly_info:
            print(f"[MockServer] 📍 收到禁飞区: {', '.join(nofly_info)}")

        # 打印启动指令
        if data.get("launch", False):
            print(f"[MockServer] 🚀 收到启动指令！")
            if not nofly_info:
                print(f"[MockServer] ⚠ 警告: 启动时无有效禁飞区")

        # 打印完整 JSON（调试用）
        print(f"[MockServer] ← 接收: {json.dumps(data, ensure_ascii=False)}")

    def stop(self):
        """关闭服务器"""
        self.running = False
        if self.client_sock:
            try:
                self.client_sock.close()
            except Exception:
                pass
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        print("[MockServer] 服务器已关闭")


# ============================================================
# 命令行入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="模拟机载端 TCP 服务 — 用于地面站独立测试"
    )
    parser.add_argument(
        "--port", type=int, default=8001,
        help="监听端口 (默认: 8001，与机载 LandScreenNode 一致)"
    )
    parser.add_argument(
        "--interval", type=float, default=0.1,
        help="数据发送间隔/秒 (默认: 0.1 = 10Hz)"
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="监听地址 (默认: 0.0.0.0)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  模拟机载端 TCP 服务 (Mock LandScreenNode)")
    print("=" * 60)
    print(f"  端口: {args.port}")
    print(f"  频率: {1/args.interval:.0f} Hz")
    print(f"  模拟目标: {len(SIMULATED_TARGETS)} 个")
    print(f"  规划路径: {len(SIMULATED_PLANNER)} 个航点")
    print("=" * 60)
    print()
    print("  预期测试效果：")
    print("  1. 地面站连接后 → 状态显示 '已连接'")
    print("  2. 地图上绘制红色路径箭头")
    print("  3. 目标汇总标签更新为最新目标信息")
    print("  4. 点击 '显示目标信息' → 查看累计目标列表+统计")
    print("  5. 选择A/B网格 → 禁飞区标签更新")
    print("  6. 点击 '发送' → 本窗口显示收到的禁飞区坐标")
    print("  7. 点击 '启动' → 本窗口显示启动指令")
    print()
    print("-" * 60)

    server = MockLandScreenNode(
        host=args.host, port=args.port, interval=args.interval
    )

    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        sys.exit(0)
