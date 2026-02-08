import time
import functools
import threading
import random
from collections import deque
from datetime import datetime

# =============================================================================
# 1. 模擬 Linux Kernel 的全域變數與配置
# =============================================================================

# 對應 /sys/kernel/debug/tracing/tracing_on
# 當設為 False 時，ftrace_hook 應該像 NOP 一樣，不執行任何記錄動作
TRACING_ENABLED = False

# 模擬 Ring Buffer (環形緩衝區)
# Linux Kernel 中通常是 per-cpu 的 buffer，這裡簡化為一個全域的 buffer
# 使用 deque(maxlen=N) 來模擬固定大小的 Ring Buffer，舊資料會被自動覆蓋 (FIFO)
RING_BUFFER_SIZE = 10
TRACE_BUFFER = deque(maxlen=RING_BUFFER_SIZE)

# 用來模擬 CPU ID (在多執行緒環境下大概模擬一下)
def get_cpu_id():
    # 簡化：隨機返回 0-3，模擬 4 核心
    # 實務上這會從 thread local storage 或 cpu id register 取得
    return random.randint(0, 3)

# =============================================================================
# 2. 核心機制：插樁 (Instrumentation) 與 Hook
# =============================================================================

def ftrace_hook(func):
    """
    模擬 GCC 的 -pg 選項或 -mfentry。
    在 C 語言中，這會在每個函式的開頭插入 call _mcount 指令。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # [模擬 NOP 機制]
        # Linux Kernel 使用 "Static Keys" 或動態代碼替換 (Code Patching)
        # 來讓這個檢查變得極快 (接近 0 overhead)。
        # 如果追蹤被關閉 (TRACING_ENABLED = False)，這裡直接 return func()，
        # 對 CPU 來說就像執行了幾個 NOP 指令後繼續執行函式本體。
        if not TRACING_ENABLED:
            return func(*args, **kwargs)

        # [模擬 _mcount 的紀錄行為]
        # 如果 TRACING_ENABLED = True，這裡原本的 NOP 會被替換成 call ftrace_caller
        
        # 1. 取得當前時間 (模擬 cpu_clock)
        ts = time.time()
        
        # 2. 取得當前 Context (PID, Comm, CPU)
        # 這裡用 Thread Name 模擬 Process Name (comm), Thread ID 模擬 PID
        task_comm = threading.current_thread().name
        pid = threading.get_ident() % 10000 # 簡化 PID顯示
        cpu = get_cpu_id()
        
        # 3. 準備紀錄資料
        # 在 Kernel 中這會寫入 binary ring buffer
        entry = {
            "task": task_comm,
            "pid": pid,
            "cpu": cpu,
            "timestamp": ts,
            "function": func.__name__,
            "args": args # 額外功能：紀錄參數 (類似 Function Graph Tracer 或 Tracepoint)
        }
        
        # [模擬 Ring Buffer 寫入]
        # 寫入 Buffer，若滿了則覆蓋最舊的 (Overwrite mode)
        TRACE_BUFFER.append(entry)
        
        # 執行原本的函式
        return func(*args, **kwargs)
    
    return wrapper

# =============================================================================
# 3. 模擬核心子系統 (Simulated Kernel Subsystems)
# =============================================================================

# 這些函式都被 "插樁" 了，會在 Runtime 被追蹤

@ftrace_hook
def kmalloc(size):
    # 模擬記憶體分配
    # 這裡可能會呼叫更底層的函式，產生 Call Graph
    _get_free_pages(1)
    return f"0xffff8800{random.randint(1000,9999)}"

@ftrace_hook
def kfree(ptr):
    pass

@ftrace_hook
def _get_free_pages(order):
    # 模擬底層 Page Allocator
    pass

@ftrace_hook
def schedule():
    # 模擬排程器
    __switch_to()

@ftrace_hook
def __switch_to():
    pass

@ftrace_hook
def handle_mm_fault(address):
    # page fault handler
    # 可能觸發 swap in 或 cow
    pass

# =============================================================================
# 4. 使用者空間介面 (Userspace Interface)
# =============================================================================

def ftrace_on():
    """ 模擬 echo 1 > /sys/kernel/debug/tracing/tracing_on """
    global TRACING_ENABLED
    print("[Control] tracing_on = 1 (Ftrace Enabled)")
    TRACING_ENABLED = True

def ftrace_off():
    """ 模擬 echo 0 > /sys/kernel/debug/tracing/tracing_on """
    global TRACING_ENABLED
    print("[Control] tracing_on = 0 (Ftrace Disabled)")
    TRACING_ENABLED = False

def cat_trace():
    """ 模擬 cat /sys/kernel/debug/tracing/trace """
    print("\n# tracer: function")
    print("#")
    print("# entries-in-buffer/entries-written: {}/{}".format(len(TRACE_BUFFER), len(TRACE_BUFFER)))  # 簡化
    print("#")
    print("#                              _-----=> irqs-off")
    print("#                             / _----=> need-resched")
    print("#                            | / _---=> hardirq/softirq")
    print("#                            || / _--=> preempt-depth")
    print("#                            ||| /     delay")
    print("#           TASK-PID   CPU#  ||||    TIMESTAMP  FUNCTION")
    print("#              | |       |   ||||       |         |")

    # 讀取 Buffer (因為是 deque，迭代就是從這最舊到最新)
    for entry in TRACE_BUFFER:
        ts_str = "{:.6f}".format(entry['timestamp'] % 1000) # 只取後幾位模擬 offset
        
        # 格式化輸出
        # 範例: bash-1234 [001] .... 123.456789: kmalloc <- function_caller
        print(f"{entry['task']:>16}-{entry['pid']:<5} [{entry['cpu']:03}] .... {ts_str:>12}: {entry['function']:<20} (args={entry['args']})")
    
    print("#\n")

# =============================================================================
# 5. 主程式流程 (Scenario Simulation)
# =============================================================================

def run_simulation():
    # 設定執行緒名稱模擬 Process Name
    threading.current_thread().name = "kworker/u4:0"
    
    print("=== 1. 系統啟動，但 Ftrace 預設關閉 ===")
    kmalloc(64) # 這些不應該被紀錄
    schedule()
    
    print("   (檢查 Buffer，應該要是空的)")
    cat_trace()

    print("=== 2. 啟用 Ftrace (echo 1 > tracing_on) ===")
    ftrace_on()
    
    print("   (執行一連串核心操作...)")
    ptr = kmalloc(1024)
    handle_mm_fault(0xdeadbeef)
    schedule()
    kfree(ptr)
    
    print("\n=== 3. 檢視 Trace 結果 (cat trace) ===")
    cat_trace()
    
    print("=== 4. 模擬 Ring Buffer 溢出 (Ring Buffer size = 10) ===")
    print("   (快速執行 15 次 kmalloc，舊的應該被覆蓋)")
    for i in range(15):
        kmalloc(i)
        
    cat_trace()
    
    print("=== 5. 關閉 Ftrace (echo 0 > tracing_on) ===")
    ftrace_off()
    kmalloc(128) # 這不會被紀錄
    
    print("   (Buffer 應該停留在最後的狀態，不包含上面的 kmalloc(128))")
    cat_trace()

if __name__ == "__main__":
    run_simulation()
