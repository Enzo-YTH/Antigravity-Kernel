import http.server
import socketserver
import json
import webbrowser
import time
import functools
import threading
import random
from collections import deque
from datetime import datetime

PORT = 8004

# =============================================================================
# 1. 模擬 Linux Kernel Ftrace 機制
# =============================================================================

TRACING_ENABLED = False
RING_BUFFER_SIZE = 100 # Increased for graph view
TRACE_BUFFER = deque(maxlen=RING_BUFFER_SIZE)

# System Logs for Kernel Events (Code Patching simulation)
SYSTEM_LOGS = deque(maxlen=50)

def log_system_event(msg, level="info"):
    """
    Log a kernel system event (e.g., stop_machine, text_poke).
    level: 'info', 'warn', 'crit'
    """
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    SYSTEM_LOGS.append({
        "ts": ts,
        "msg": msg,
        "type": level
    })

# Thread-local storage for "current task" context simulation
tl = threading.local()

def get_current_task():
    if not hasattr(tl, 'task_comm'):
        # Assign a random task if not set (for standalone calls)
        tasks = [("bash", 1001), ("sshd", 543), ("systemd", 1), ("kworker/u4:0", 8400), ("nginx", 2048)]
        t = random.choice(tasks)
        tl.task_comm = t[0]
        tl.pid = t[1]
        tl.cpu = random.randint(0, 3)
        tl.depth = 0
    return tl

def ftrace_hook(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        ctx = get_current_task()
        
        # [Graph Tracer] Entry Event
        start_ts = time.time()
        
        if TRACING_ENABLED:
            # Simulate real kernel flags
            flags_opts = [".", "N", "p", "h", "s"]
            f1 = "." if random.random() > 0.9 else "d" # irqs-off (rarely off for userspace tasks)
            f2 = "." if random.random() > 0.9 else "N"
            f3 = "."
            f4 = "." 
            flags = f"{f1}{f2}{f3}{f4}"
            
            # Entry Log
            TRACE_BUFFER.append({
                "type": "ENTRY",
                "task": ctx.task_comm,
                "pid": ctx.pid,
                "cpu": ctx.cpu,
                "flags": flags,
                "timestamp": start_ts,
                "function": func.__name__,
                "args": f"({', '.join(map(str, args))})",
                "depth": ctx.depth
            })
        
        ctx.depth += 1
        
        # EXECUTE FUNCTION
        result = func(*args, **kwargs)
        
        ctx.depth -= 1
        end_ts = time.time()
        duration_us = (end_ts - start_ts) * 1000000
        
        # [Graph Tracer] Return Event
        if TRACING_ENABLED:
            TRACE_BUFFER.append({
                "type": "RETURN",
                "task": ctx.task_comm,
                "pid": ctx.pid,
                "cpu": ctx.cpu,
                "flags": "....",
                "timestamp": end_ts,
                "function": func.__name__,
                "duration": duration_us,
                "depth": ctx.depth
            })

        return result
    return wrapper

# --- Simulated Kernel Functions (vfs_read chain) ---

@ftrace_hook
def vfs_read(file_ptr, buf, count, pos):
    # ret = rw_verify_area(READ, file, pos, count)
    if not rw_verify_area(0, file_ptr, pos, count):
        return -1
    return __vfs_read(file_ptr, buf, count, pos)

@ftrace_hook
def rw_verify_area(read_write, file_ptr, pos, count):
    return security_file_permission(file_ptr, read_write)

@ftrace_hook
def security_file_permission(file_ptr, mask):
    # Simulate check overhead
    time.sleep(0.0001) 
    return True

@ftrace_hook
def __vfs_read(file_ptr, buf, count, pos):
    # Simulate different file system ops
    if "ext4" in file_ptr:
        return ext4_file_read_iter(file_ptr, buf, count)
    else:
        return 0

@ftrace_hook
def ext4_file_read_iter(iocb, to, count):
    # Simulate IO
    time.sleep(0.0002)
    return count

# --- Other Random Functions ---

@ftrace_hook
def kmalloc(size):
    _get_free_pages(1)
    return f"0xffff8800{random.randint(1000,9999)}"

@ftrace_hook
def kfree(ptr):
    pass

@ftrace_hook
def _get_free_pages(order):
    pass

@ftrace_hook
def schedule():
    __switch_to()

@ftrace_hook
def __switch_to():
    pass

@ftrace_hook
def handle_mm_fault(address):
    pass

# Helper to run a full chain
def run_vfs_read_simulation():
    # Force a specific task context for this chain
    tl.task_comm = "cat"
    tl.pid = random.randint(2000, 3000)
    tl.cpu = random.randint(0, 3)
    tl.depth = 0
    
    fd = f"0xffff8800{random.randint(1000,9999)} [ext4]"
    vfs_read(fd, "0x7ffd...", 4096, 0)

# =============================================================================
# 2. Web Server & API
# =============================================================================

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Linux Ftrace Visualization</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0d1117;
            --panel-bg: rgba(22, 27, 34, 0.8);
            --border-color: #30363d;
            --accent-primary: #58a6ff;
            --accent-secondary: #ff7b72;
            --accent-tertiary: #3fb950;
            --accent-warn: #d29922;
            --text-main: #c9d1d9;
            --text-dim: #8b949e;
        }

        body { 
            font-family: 'Inter', sans-serif; background: var(--bg-color); color: var(--text-main); 
            margin: 0; padding: 20px; min-height: 100vh; display: flex; flex-direction: column; align-items: center;
        }

        h1 { font-family: 'Space Mono', monospace; margin-bottom: 20px; text-shadow: 0 0 10px rgba(88,166,255,0.3); }

        .container { 
            display: flex; gap: 20px; width: 100%; max-width: 1400px; 
            align-items: flex-start;
        }

        .panel {
            background: var(--panel-bg); border: 1px solid var(--border-color); border-radius: 8px;
            padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }

        .title { 
            font-family: 'Space Mono', monospace; font-size: 14px; color: var(--text-dim); 
            border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-bottom: 15px;
            text-transform: uppercase; letter-spacing: 1px; display: flex; justify-content: space-between;
        }

        /* Controls */
        .controls { width: 280px; display: flex; flex-direction: column; gap: 15px; }
        
        .toggle-container {
            display: flex; align-items: center; justify-content: space-between;
            background: #161b22; padding: 10px; border-radius: 6px; border: 1px solid var(--border-color);
        }
        
        .switch { position: relative; display: inline-block; width: 40px; height: 20px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #30363d; transition: .4s; border-radius: 20px; }
        .slider:before { position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 3px; background-color: white; transition: .4s; border-radius: 50%; }
        input:checked + .slider { background-color: var(--accent-tertiary); }
        input:checked + .slider:before { transform: translateX(20px); }

        .btn-group { display: flex; flex-direction: column; gap: 8px; }
        button {
            background: #21262d; color: var(--text-main); border: 1px solid var(--border-color);
            padding: 8px 12px; border-radius: 6px; cursor: pointer; font-family: 'Space Mono', monospace; font-size: 12px;
            transition: all 0.2s; text-align: left;
        }
        button:hover { background: #30363d; border-color: var(--text-dim); }
        button:active { transform: translateY(1px); }
        .btn-func::before { content: "run "; color: var(--accent-warn); opacity: 0.7; }

        /* System Logs */
        .system-logs {
            background: #0d1117; height: 150px; overflow-y: auto; padding: 10px; 
            border: 1px solid #30363d; border-radius: 4px; font-family: 'Space Mono', monospace; font-size: 11px;
            display: flex; flex-direction: column; gap: 4px;
        }
        .sys-log-entry { display: flex; gap: 8px; }
        .sys-ts { color: #8b949e; min-width: 80px; }
        .sys-msg-info { color: #a5d6ff; }
        .sys-msg-warn { color: #d29922; }
        .sys-msg-crit { color: #ff7b72; font-weight: bold; }

        /* Code View */
        .code-view { margin-bottom: 20px; }
        .code-line { display: flex; gap: 10px; padding: 2px 5px; border-radius: 4px; transition: background 0.2s; font-family: 'Space Mono', monospace; font-size: 12px; }
        .line-num { color: var(--text-dim); min-width: 30px; text-align: right; }
        .asm-instr { color: var(--accent-primary); font-weight: bold; min-width: 60px; }
        .asm-args { color: #a5d6ff; }
        .hook-point { background: rgba(210, 153, 34, 0.1); border: 1px dashed var(--accent-warn); }
        .hook-active { background: rgba(88, 166, 255, 0.2); border-color: var(--accent-primary); animation: pulse-border 1.5s infinite; }
        @keyframes pulse-border { 0% { border-color: var(--accent-primary); } 50% { border-color: #fff; } 100% { border-color: var(--accent-primary); } }

        /* View Panel */
        .view-panel { flex: 3; display: flex; flex-direction: column; height: 600px; }
        .tab-bar { display: flex; gap: 10px; margin-bottom: 10px; }
        .tab { padding: 5px 10px; cursor: pointer; border-radius: 4px; font-size: 12px; font-weight: bold; color: var(--text-dim); border: 1px solid transparent; }
        .tab.active { background: #1f2428; color: var(--accent-primary); border-color: var(--border-color); }
        .buffer-container { flex-grow: 1; overflow-y: auto; background: #000; border-radius: 6px; padding: 10px; border: 1px solid var(--border-color); font-family: 'Space Mono', monospace; font-size: 12px; }

        .trace-entry { display: grid; grid-template-columns: 140px 40px 40px 90px 1fr; gap: 10px; padding: 4px 8px; border-bottom: 1px solid #1f2428; color: #8b949e; }
        .trace-header { font-weight: bold; color: var(--text-dim); border-bottom: 1px solid var(--border-color); }
        .col-task { color: var(--accent-warn); }
        .col-cpu { color: var(--text-dim); }
        .col-flags { color: #d19a66; font-weight:bold; }
        .col-ts { color: var(--accent-secondary); }
        .col-func { color: var(--accent-primary); }

        .graph-entry { padding: 2px 0; border-left: 1px solid #333; display: flex; align-items: center; color: #8b949e; }
        .func-name { color: var(--accent-primary); font-weight: bold; }
        .duration { color: var(--accent-tertiary); font-size: 0.9em; margin-left: auto; padding-right: 10px; }
        .return-brace { color: var(--text-dim); }

        /* Patching Overlay */
        #patching-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(13, 17, 23, 0.95);
            display: none; flex-direction: column; align-items: center; justify-content: center;
            z-index: 1000; backdrop-filter: blur(5px);
        }
        .step-box {
            font-family: 'Space Mono', monospace; font-size: 24px; color: #fff;
            margin: 20px; opacity: 0; transition: opacity 0.5s; text-align: center;
            border: 2px solid var(--border-color); padding: 20px 40px; border-radius: 12px;
            background: #161b22; width: 600px;
        }
        .step-box.active { opacity: 1; border-color: var(--accent-primary); box-shadow: 0 0 20px rgba(88,166,255,0.2); }
        .highlight-text { color: var(--accent-warn); font-weight: bold; }
        .big-icon { font-size: 40px; margin-bottom: 10px; display: block; }

    </style>
</head>
<body>
    <h1>Linux Ftrace & Code Patching</h1>
    
    <!-- Code Patching Animation Overlay -->
    <div id="patching-overlay">
        <h2 style="color:var(--text-dim); margin-bottom:20px;">KERNEL LIVE PATCHING IN PROGRESS...</h2>
        
        <div id="step-1" class="step-box">
            <span class="big-icon">🛑</span>
            <div>1. STOP_MACHINE</div>
            <div style="font-size:16px; color:#8b949e; margin-top:10px;">Pausing all CPUs to ensure safety...</div>
        </div>
        
        <div id="step-2" class="step-box">
            <span class="big-icon">🔧</span>
            <div>2. HOT PATCHING</div>
            <div style="font-size:16px; color:#8b949e; margin-top:10px;">
                Modifying Text Segment:<br>
                <span style="text-decoration: line-through; color: #ff7b72;">NOP</span> &rarr; <span style="color: #3fb950; font-weight:bold;">CALL ftrace_caller</span>
            </div>
        </div>
        
        <div id="step-3" class="step-box">
            <span class="big-icon">▶️</span>
            <div>3. RESUME</div>
            <div style="font-size:16px; color:#8b949e; margin-top:10px;">System Running. Tracing Active.</div>
        </div>

        <button id="overlay-btn" class="overlay-btn" onclick="nextStep()" style="margin-top:40px; font-size:18px; padding:10px 30px; background:var(--accent-primary); color:#0d1117; font-weight:bold; border:none; cursor:pointer; opacity:0; transition:opacity 0.5s;">Next Step &rarr;</button>
    </div>

    <div class="container">
        <!-- Controls & System Info -->
        <div class="panel controls">
            <div class="title">Control Panel</div>
            
            <div class="toggle-container">
                <span style="font-size: 13px; font-weight:600;">tracing_on</span>
                <label class="switch">
                    <input type="checkbox" id="trace-toggle" onchange="toggleTrace()">
                    <span class="slider"></span>
                </label>
            </div>
            
            <!-- Code Visualization -->
            <div class="code-view">
                <div style="font-size: 11px; color:#8b949e; margin-bottom:5px;">Code Segment (Simulated):</div>
                <div class="code-line"><span class="line-num">00</span> <span class="asm-instr">push</span> <span class="asm-args">%rbp</span></div>
                <div class="code-line"><span class="line-num">04</span> <span class="asm-instr">mov</span> <span class="asm-args">%rsp, %rbp</span></div>
                <div class="code-line hook-point" id="hook-line">
                    <span class="line-num">08</span> <span class="asm-instr" id="instr-hook">nop</span> <span class="asm-args" id="args-hook"></span> 
                    <span style="margin-left:auto; color:var(--accent-warn); font-size: 10px;">// _mcount</span>
                </div>
                <div class="code-line"><span class="line-num">0C</span> <span class="asm-instr">sub</span> <span class="asm-args">$0x10, %rsp</span></div>
            </div>

            <div style="font-size: 12px; color:var(--text-dim);">Kernel Events Log:</div>
            <div class="system-logs" id="sys-logs">
                <div class="sys-log-entry"><span class="sys-ts">00:00:00</span><span class="sys-msg-info">System Ready.</span></div>
            </div>

            <div style="font-size: 12px; color:var(--text-dim); margin-top:10px;">Scenarios:</div>
            <div class="btn-group">
                <button class="btn-func" onclick="runFunc('vfs_read')">vfs_read() (Chain)</button>
                <button class="btn-func" onclick="runFunc('kmalloc')">kmalloc(1024)</button>
                <button class="btn-func" onclick="runFunc('mixed')">Run Mixed Load</button>
            </div>
            
            <button onclick="clearBuffer()" style="margin-top:auto; background:#301c1c; border-color:#502020; color:#ff7b72;">Clear Buffer</button>
        </div>
        
        <!-- View Panel -->
        <div class="panel view-panel">
            <div class="tab-bar">
                <div class="tab" id="tab-list" onclick="switchTab('list')">Ring Buffer (List)</div>
                <div class="tab active" id="tab-graph" onclick="switchTab('graph')">Function Graph</div>
            </div>
            
            <div id="list-header" class="trace-entry trace-header" style="display:none;">
                <span>TASK-PID</span> <span>CPU</span> <span>FLAGS</span> <span>TIMESTAMP</span> <span>FUNCTION</span>
            </div>
            <div id="graph-header" class="trace-entry trace-header" style="grid-template-columns: 140px 40px 1fr 100px;">
                <span>TASK-PID</span> <span>CPU</span> <span>FUNCTION CALL GRAPH</span> <span style="text-align:right">DURATION</span>
            </div>

            <div class="buffer-container" id="buffer-container"></div>
        </div>
    </div>
    
    <script>
        let tracingOn = false;
        let lastBufferJson = '';
        let lastSysLogLen = 0;
        let currentView = 'graph'; 
        
        // Animation State
        let currentResolver = null;

        function switchTab(view) {
            currentView = view;
            document.getElementById('tab-list').className = view === 'list' ? 'tab active' : 'tab';
            document.getElementById('tab-graph').className = view === 'graph' ? 'tab active' : 'tab';
            document.getElementById('list-header').style.display = view === 'list' ? 'grid' : 'none';
            document.getElementById('graph-header').style.display = view === 'graph' ? 'grid' : 'none';
            fetchBuffer(true);
        }

        async function toggleTrace() {
            tracingOn = document.getElementById('trace-toggle').checked;
            
            if (tracingOn) {
                await playPatchingAnimation(true);
            } else {
                 await playPatchingAnimation(false);
            }

            // Only send request AFTER animation completes
            await fetch('/control', { method: 'POST', body: JSON.stringify({ enable: tracingOn }) });
        }
        
        function nextStep() {
            if (currentResolver) {
                currentResolver();
                currentResolver = null;
            }
        }
        
        function waitForNext() {
            return new Promise(resolve => {
                currentResolver = resolve;
                // Enable button
                const btn = document.getElementById('overlay-btn');
                btn.style.opacity = '1';
                btn.classList.add('pulse-btn');
            });
        }

        async function playPatchingAnimation(enable) {
            const overlay = document.getElementById('patching-overlay');
            const btn = document.getElementById('overlay-btn');
            const step1 = document.getElementById('step-1');
            const step2 = document.getElementById('step-2');
            const step3 = document.getElementById('step-3');
            
            // Text Update
            if (!enable) {
                 step2.querySelector('div:nth-child(3)').innerHTML = 'Modifying Text Segment:<br><span style="color:#ff7b72; font-weight:bold;">CALL</span> &rarr; <span style="color:#3fb950; font-weight:bold;">NOP</span>';
            } else {
                 step2.querySelector('div:nth-child(3)').innerHTML = 'Modifying Text Segment:<br><span style="text-decoration: line-through; color: #ff7b72;">NOP</span> &rarr; <span style="color: #3fb950; font-weight:bold;">CALL ftrace_caller</span>';
            }

            overlay.style.display = 'flex';
            btn.innerText = "Start Patching &rarr;";
            btn.style.opacity = '1';
            
            // Initial Wait
            await waitForNext(); 
            btn.style.opacity = '0'; // Hide briefly during transition
            
            // Step 1: STOP MACHINE
            step1.classList.add('active');
            btn.innerText = "Next: Hot Patch &rarr;";
            await new Promise(r => setTimeout(r, 500)); // Small animation delay
            await waitForNext();
            btn.style.opacity = '0';
            
            // Step 2: PATCH & VISUAL UPDATE
            step1.classList.remove('active');
            step2.classList.add('active');
            
            if (enable) {
                 document.getElementById('hook-line').classList.add('hook-active');
                 document.getElementById('instr-hook').innerText = 'call';
                 document.getElementById('args-hook').innerText = 'ftrace_caller';
            } else {
                 document.getElementById('hook-line').classList.remove('hook-active');
                 document.getElementById('instr-hook').innerText = 'nop';
                 document.getElementById('args-hook').innerText = '';
            }
            
            btn.innerText = "Next: Resume &rarr;";
            await new Promise(r => setTimeout(r, 500));
            await waitForNext();
            btn.style.opacity = '0';
            
            // Step 3: RESUME
            step2.classList.remove('active');
            step3.classList.add('active');
            btn.innerText = "Finish";
            await new Promise(r => setTimeout(r, 500));
            await waitForNext();
            
            // Finish
            step3.classList.remove('active');
            overlay.style.display = 'none';
        }
        
        async function runFunc(name) {
            await fetch('/run', { method: 'POST', body: JSON.stringify({ func: name }) });
            setTimeout(fetchBuffer, 100);
        }
        
        async function clearBuffer() {
            await fetch('/clear', { method: 'POST' });
            fetchBuffer(true);
        }
        
        async function fetchBuffer(force = false) {
            const res = await fetch('/status');
            const data = await res.json();
            
            // Update Ring Buffer
            const bufferStr = JSON.stringify(data.buffer);
            if (force || bufferStr !== lastBufferJson) {
                lastBufferJson = bufferStr;
                render(data.buffer);
            }
            
            // Update System Logs
            if (data.logs.length !== lastSysLogLen) {
                renderSysLogs(data.logs);
                lastSysLogLen = data.logs.length;
            }
        }
        
        function renderSysLogs(logs) {
            const container = document.getElementById('sys-logs');
            container.innerHTML = '';
            // Show recent logs, iterate reverse or simple append
            logs.forEach(l => {
                const el = document.createElement('div');
                el.className = 'sys-log-entry';
                let style = 'sys-msg-info';
                if (l.type === 'warn') style = 'sys-msg-warn';
                if (l.type === 'crit') style = 'sys-msg-crit';
                
                el.innerHTML = `<span class="sys-ts">${l.ts}</span><span class="${style}">${l.msg}</span>`;
                container.appendChild(el);
            });
            container.scrollTop = container.scrollHeight;
        }
        
        function render(entries) {
            const container = document.getElementById('buffer-container');
            container.innerHTML = '';
            if (currentView === 'list') renderList(container, entries);
            else renderGraph(container, entries);
            container.scrollTop = container.scrollHeight;
        }
        
        function renderList(container, entries) {
            entries.forEach(e => {
                const ts = (e.timestamp % 1000).toFixed(6);
                const el = document.createElement('div');
                el.className = 'trace-entry';
                let funcText = e.function;
                if (e.type === 'RETURN') funcText = `<span style="color:#555">}</span> /* ${e.function} */`;
                else funcText = `${e.function} <span style="color:#555">${e.args}</span>`;
                el.innerHTML = `<span class="col-task">${e.task}-${e.pid}</span><span class="col-cpu">[${e.cpu.toString().padStart(3, '0')}]</span><span class="col-flags">${e.flags || '....'}</span><span class="col-ts">${ts}:</span><span class="col-func">${funcText}</span>`;
                container.appendChild(el);
            });
        }
        
        function renderGraph(container, entries) {
            entries.forEach(e => {
                const el = document.createElement('div');
                el.className = 'graph-entry';
                const indent = e.depth * 20;
                const taskInfo = `<span style="width:140px; color:var(--accent-warn); font-family:monospace; margin-right:10px;">${e.task}-${e.pid}</span>`;
                const cpuInfo = `<span style="width:40px; color:var(--text-dim); font-family:monospace; margin-right:10px;">[${e.cpu.toString().padStart(3, '0')}]</span>`;
                let graphContent = ''; let durationStr = '';
                
                if (e.type === 'ENTRY') {
                    graphContent = `<div style="margin-left:${indent}px;"><span class="func-name">${e.function}</span><span class="func-args">${e.args} {</span></div>`;
                } else if (e.type === 'RETURN') {
                    graphContent = `<div style="margin-left:${indent}px;"><span class="return-brace">}</span> <span style="color:#555; font-size:0.8em;">/* ${e.function} */</span></div>`;
                    if (e.duration) durationStr = `<span class="duration">${e.duration.toFixed(3)} us</span>`;
                }
                el.innerHTML = `${taskInfo}${cpuInfo}<div style="flex:1;">${graphContent}</div>${durationStr}`;
                container.appendChild(el);
            });
        }
        
        setInterval(fetchBuffer, 1000);
    </script>
</body>
</html>
"""

class FtraceHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "buffer": list(TRACE_BUFFER),
                "logs": list(SYSTEM_LOGS)
            }).encode('utf-8'))
        else:
            self.send_error(404)
            
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except:
            data = {}
            
        if self.path == '/control':
            global TRACING_ENABLED
            should_enable = data.get('enable', False)
            
            if should_enable != TRACING_ENABLED:
                if should_enable:
                    # Simulation of Kernel Code Patching
                    log_system_event("Control: Enable Tracing requested.", "info")
                    log_system_event("Kernel: stop_machine() called. Pausing CPUs.", "warn")
                    log_system_event("Kernel: set_memory_rw(text_segment)", "warn")
                    log_system_event("Kernel: text_poke_bp(nop -> call ftrace_caller)", "crit")
                    log_system_event("Kernel: set_memory_ro(text_segment)", "warn")
                    log_system_event("Kernel: stop_machine() finished. Resuming.", "info")
                    TRACING_ENABLED = True
                else:
                    log_system_event("Control: Disable Tracing requested.", "info")
                    log_system_event("Kernel: stop_machine() called.", "warn")
                    log_system_event("Kernel: text_poke_bp(call -> nop)", "crit")
                    log_system_event("Kernel: stop_machine() finished.", "info")
                    TRACING_ENABLED = False
            
        elif self.path == '/run':
            func = data.get('func')
            if func == 'kmalloc': kmalloc(1024)
            elif func == 'vfs_read': run_vfs_read_simulation()
            elif func == 'mixed': 
                kmalloc(64)
                schedule()
                
        elif self.path == '/clear':
            TRACE_BUFFER.clear()
            SYSTEM_LOGS.clear()
            
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"success": True}).encode('utf-8'))

    def log_message(self, format, *args):
        return

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    print(f"Starting Visual Ftrace Demo on port {PORT}...")
    webbrowser.open(f'http://localhost:{PORT}')
    
    with ReusableTCPServer(("", PORT), FtraceHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
