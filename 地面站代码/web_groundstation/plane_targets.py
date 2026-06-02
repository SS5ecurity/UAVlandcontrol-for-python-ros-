"""
plane_targets.py — 共享数据结构模块

功能说明：
    定义 Target 数据类和 SharedData 线程安全单例类。
    保存所有目标信息（位置、名称、数量、网格坐标），
    提供线程安全的目标增删查改接口。

源码映射：
    原 C++ 文件: plane_targets.h
    原 Struct:   struct Target  →  Python Target 类
    原 Class:    class SharedData  →  Python SharedData 类
    原函数:     addTargetIfNew()  →  addTargetIfNew()
    原变量:     targets_, target_chosen_, target_display, mutex_  — 全部保留

数据来源：机载端通过 TCP 8001 端口发送的目标检测数据 (JSON: tx, ty, tn)
数据去向：Web 前端（用于显示目标汇总、目标信息弹窗）、日志文件 log.txt
"""
import math
import threading
import os


# ============================================================
# Target 类 — 对应原 C++ struct Target
# ============================================================
class Target:
    """
    目标数据结构

    属性说明（保留原 C++ 变量名）：
        x  : float  — 目标的 x 坐标（真实世界坐标，米）       [原: double x]
        y  : float  — 目标的 y 坐标（真实世界坐标，米）       [原: double y]
        name : str  — 目标名称/种类标识                       [原: QString name]
        n  : int    — 该类型目标在当前网格位置的累计数量       [原: int n]
        a  : int    — 网格横坐标 A 值（0~8），-1 表示未初始化  [原: int a]
        b  : int    — 网格纵坐标 B 值（0~6），-1 表示未初始化  [原: int b]
    """
    def __init__(self, x=-1.0, y=-1.0, name="NULL", n=0, a=-1, b=-1):
        self.x: float = x
        self.y: float = y
        self.name: str = name
        self.n: int = n
        self.a: int = a
        self.b: int = b

    def __eq__(self, other):
        """重载 == 运算符，同名字即视为同一类型目标  [原: operator==]"""
        if isinstance(other, Target):
            return self.name == other.name
        return False

    def to_dict(self):
        """将 Target 转换为字典，用于 JSON 序列化发送给前端"""
        return {
            "x": self.x,
            "y": self.y,
            "name": self.name,
            "n": self.n,
            "a": self.a,
            "b": self.b
        }


# ============================================================
# SharedData 类 — 对应原 C++ class SharedData（单例模式）
# ============================================================
class SharedData:
    """
    线程安全的共享数据单例类
    存储所有已发现的目标列表和当前被选中的救援目标。

    数据流：
        数据来源：land_screen.py 中的 parseJson() 方法解析机载端发来的目标数据
        数据去向：TargetInfo 页面显示、前端目标汇总标签、log.txt 日志文件

    对应原 C++ 变量：
        targets_        : std::vector<Target>  →  List[Target]
        target_chosen_  : Target               →  Target 对象
        target_display  : Target               →  Target 对象（保留，原代码中未实际使用）
        mutex_          : std::mutex           →  threading.Lock()
    """

    # ---------- 单例模式 ----------
    _instance = None        # 单例实例指针  [原: static Blink* instance]
    _lock_singleton = threading.Lock()  # 单例创建锁

    @staticmethod
    def getInstance():
        """
        获取 SharedData 单例实例  [原: static SharedData& getInstance()]

        返回：SharedData 的唯一实例
        线程安全：使用双重检查锁定确保单例创建安全
        """
        if SharedData._instance is None:
            with SharedData._lock_singleton:
                if SharedData._instance is None:
                    SharedData._instance = SharedData()
        return SharedData._instance

    def __init__(self):
        """私有构造函数，仅由 getInstance() 调用  [原: private SharedData()]"""
        # targets_ : 已发现的所有目标列表  [原: std::vector<Target> targets_]
        # 存储每个被检测到的目标信息（去重后）
        self.targets_: list = []

        # target_chosen_ : 当前被选中/接收的目标  [原: Target target_chosen_]
        # 存储最近一次从机载端接收到的最新目标信息
        self.target_chosen_: Target = Target(
            x=-1, y=-1, name="NULL", n=0, a=-1, b=-1
        )

        # target_display : 预留显示目标变量，保留原代码结构  [原: Target target_display]
        self.target_display: Target = Target()

        # mutex_ : 线程互斥锁，保护 targets_ 和 target_chosen_ 的并发访问  [原: std::mutex mutex_]
        self.mutex_: threading.Lock = threading.Lock()

    # ---------- 数据访问方法 ----------

    def getTargets(self):
        """
        获取目标列表的引用  [原: std::vector<Target>& getTargets()]
        注意：调用者应在获取后立即加锁使用，避免竞争
        返回：目标列表
        """
        return self.targets_

    def getChosenTarget(self):
        """
        获取当前选中目标的引用  [原: Target& getChosenTarget()]
        返回：当前被选中的目标对象
        """
        return self.target_chosen_

    def getMutex(self):
        """
        获取互斥锁  [原: std::mutex& getMutex()]
        返回：线程锁对象
        """
        return self.mutex_

    # ---------- 目标管理方法 ----------

    def addTargetIfNew(self, newTarget: Target):
        """
        如果目标尚未被记录过，则将其添加到目标列表；如果已存在同位置同类型，则增加计数。
        [原: void addTargetIfNew(Target& newTarget)]

        逻辑说明：
            1. 如果目标列表为空，直接添加（首个目标，n=1）
            2. 过滤起点网格 (a=9, b=1)，不记录
            3. 遍历已有目标：
               a. 如果找到同类型同网格(a,b)的目标：
                  - 计算新旧位置的距离
                  - 距离 > 0.08m 则增加计数 + 更新位置
               b. 如果找到同类型但不同网格的目标，标记 abSameFound=True
            4. 遍历完后，如果没有任何同类型同网格的目标存在，则添加新目标 (n=1)
            5. 每次添加/更新都会写入 log.txt 日志文件

        参数：
            newTarget : Target  — 新接收到的目标数据
                            来源于 TCP 8001 接收的 JSON: {"tx":x, "ty":y, "tn":"name"}
                            经过 parseJson() 转换为网格坐标 a, b 后传入
        """
        with self.mutex_:
            if len(self.targets_) == 0:
                newTarget.n = 1
                self.targets_.append(newTarget)
                self._log_target(newTarget)
                return

            # 起点过滤：网格位置 (a=9, b=1) 为起飞点，不记录
            if newTarget.a == 9 and newTarget.b == 1:
                return

            abSameFound = False
            for t in self.targets_:
                if t.name == newTarget.name:
                    if t.a == newTarget.a and t.b == newTarget.b:
                        # 同类型同网格目标，检查距离是否足够远（>0.08m 视为新目标）
                        dis = math.sqrt(
                            (newTarget.x - t.x) ** 2 + (newTarget.y - t.y) ** 2
                        )
                        if dis > 0.08:
                            t.n += 1               # 累计数量 +1
                            t.x = newTarget.x       # 更新最新位置
                            t.y = newTarget.y
                            newTarget.n = t.n       # 同步计数
                            self._log_target(newTarget)
                        # 无论距离如何，同网格同类型不新增，直接返回
                        return
                    abSameFound = True

            # 没有找到同类型同网格的目标，作为新目标添加
            newTarget.n = 1
            self.targets_.append(newTarget)
            self._log_target(newTarget)

    def _log_target(self, target: Target):
        """
        将目标信息写入 log.txt 日志文件（追加模式）
        写入格式：x,y,name（每行一个目标）
        [原: 内联代码 std::ofstream logFile("log.txt", std::ios::app)]
        """
        try:
            log_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "log.txt"
            )
            with open(log_path, "a", encoding="utf-8") as logFile:
                logFile.write(f"{target.x},{target.y},{target.name}\n")
        except Exception:
            pass  # 日志写入失败不影响主流程
