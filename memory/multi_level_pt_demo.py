import http.server
import socketserver
import json
import webbrowser
import os
import sys

# -------------------------------------------------------------------------
# Multi-Level Page Table Visualization (x86_64 4-Level Paging)
# Levels: PGD (L4) -> PUD (L3) -> PMD (L2) -> PTE (L1)
# Address Breakdown (48-bit canonical):
#   Bits 47-39: PGD Index (9 bits)
#   Bits 38-30: PUD Index (9 bits)
#   Bits 29-21: PMD Index (9 bits)
#   Bits 20-12: PTE Index (9 bits)
#   Bits 11-00: Offset (12 bits)
# -------------------------------------------------------------------------

PORT = 8003  # Running on distinct port

# Constants
PAGE_SHIFT = 12
PAGE_SIZE  = 1 << PAGE_SHIFT
ENTRIES_PER_TABLE = 512 # 9 bits
PFN_MASK   = 0x000FFFFFFFFFF000

# Level Names
LEVEL_NAMES = {
    4: "PGD (Page Global Directory)",
    3: "PUD (Page Upper Directory)",
    2: "PMD (Page Middle Directory)",
    1: "PTE (Page Table Entry)"
}

class PTE:
    def __init__(self, value=0):
        self.value = value

    def is_present(self):
        return (self.value & 1) != 0

    def set_present(self, enable=True):
        if enable: self.value |= 1
        else: self.value &= ~1
        
    def set_rw(self, enable=True): # Just for visual completeness
        if enable: self.value |= 2
        else: self.value &= ~2

    def get_pfn(self):
        return (self.value & PFN_MASK) >> PAGE_SHIFT

    def set_pfn(self, pfn):
        self.value &= ~PFN_MASK
        self.value |= (pfn << PAGE_SHIFT) & PFN_MASK

    def to_dict(self):
        return {
            "value_hex": f"0x{self.value:016x}",
            "present": self.is_present(),
            "pfn": self.get_pfn()
        }

class PageTable:
    def __init__(self, level):
        self.level = level
        self.entries = [PTE(0) for _ in range(ENTRIES_PER_TABLE)]

class MMU:
    def __init__(self):
        self.phys_mem = {} # PFN -> PageTable object (or Data Dummy)
        self.next_free_pfn = 100
        
        # Root (PGD) is always at CR3
        self.pgd_pfn = 10
        self.cr3 = self.pgd_pfn << PAGE_SHIFT
        self.phys_mem[self.pgd_pfn] = PageTable(4) # Level 4

    def alloc_frame(self):
        pfn = self.next_free_pfn
        self.next_free_pfn += 1
        return pfn

    def get_indices(self, va):
        # returns [pgd_idx, pud_idx, pmd_idx, pte_idx, offset]
        pgd_idx = (va >> 39) & 0x1FF
        pud_idx = (va >> 30) & 0x1FF
        pmd_idx = (va >> 21) & 0x1FF
        pte_idx = (va >> 12) & 0x1FF
        offset  = va & 0xFFF
        return [pgd_idx, pud_idx, pmd_idx, pte_idx, offset]

    def map_page(self, va):
        indices = self.get_indices(va)
        
        # Traverse L4 -> L1, creating as needed
        current_pfn = self.pgd_pfn
        
        for level in range(4, 0, -1):
            idx = indices[4 - level] # 0 for L4, 1 for L3...
            
            table = self.phys_mem.get(current_pfn)
            if not table: return False # Should not happen if logic is correct
            
            entry = table.entries[idx]
            
            if not entry.is_present():
                # Allocate next level
                new_pfn = self.alloc_frame()
                if level > 1:
                    # Create empty table for next level
                    self.phys_mem[new_pfn] = PageTable(level - 1)
                else:
                    # Level 1 PTE points to Data Page
                    self.phys_mem[new_pfn] = "DATA_PAGE" 
                
                entry.set_pfn(new_pfn)
                entry.set_present(True)
                entry.set_rw(True)
            
            current_pfn = entry.get_pfn()

        return True

    def walk(self, va):
        indices = self.get_indices(va)
        logs = []
        path = []
        
        current_pfn = self.pgd_pfn
        
        logs.append(f"Starting Walk for VA 0x{va:012x}")
        logs.append(f"CR3 -> PGD at PFN {current_pfn}")
        
        success = True
        
        for level in range(4, 0, -1):
            idx = indices[4 - level]
            table = self.phys_mem.get(current_pfn)
            
            if not table or isinstance(table, str):
                logs.append(f"Level {level}: Critical Error - Missing Table at PFN {current_pfn}")
                return {"success": False, "logs": logs, "path": path}

            entry = table.entries[idx]
            logs.append(f"Level {level} ({LEVEL_NAMES[level]}) Index {idx}: Entry 0x{entry.value:x} (P={int(entry.is_present())})")
            
            step_info = {
                "level": level,
                "name": LEVEL_NAMES[level],
                "index": idx,
                "entry_val": f"0x{entry.value:x}",
                "present": entry.is_present(),
                "next_pfn": entry.get_pfn()
            }
            path.append(step_info)

            if not entry.is_present():
                logs.append(f"FAULT: Entry not present at Level {level}")
                success = False
                break
            
            current_pfn = entry.get_pfn()
            
        if success:
            pa = (current_pfn << PAGE_SHIFT) | indices[4]
            logs.append(f"Translation Successful: PA 0x{pa:x}")
            
        return {"success": success, "logs": logs, "path": path, "indices": indices}

    def reset(self):
        self.phys_mem = {}
        self.next_free_pfn = 100
        self.pgd_pfn = 10
        self.cr3 = self.pgd_pfn << PAGE_SHIFT
        self.phys_mem[self.pgd_pfn] = PageTable(4)

mmu = MMU()
mmu.map_page(0x7ff_123456000) # Map one example page

# --- Web Server ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Linux 4-Level Page Table Visualization</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0d1117;
            --panel-bg: rgba(22, 27, 34, 0.8);
            --border-color: #30363d;
            --accent-primary: #58a6ff;   /* Blue */
            --accent-secondary: #ff7b72; /* Red/Pink */
            --accent-tertiary: #3fb950;  /* Green */
            --accent-warn: #d29922;      /* Yellow */
            --text-main: #c9d1d9;
            --text-dim: #8b949e;
            --neon-glow: 0 0 10px rgba(88, 166, 255, 0.3);
        }

        body { 
            font-family: 'Inter', sans-serif; 
            background: var(--bg-color); 
            color: var(--text-main); 
            margin: 0; padding: 20px; 
            min-height: 100vh;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(88, 166, 255, 0.1) 0%, transparent 20%),
                radial-gradient(circle at 90% 80%, rgba(255, 123, 114, 0.1) 0%, transparent 20%);
        }

        h1 { 
            font-family: 'Space Mono', monospace; 
            text-align: center; 
            color: var(--text-main);
            text-shadow: 0 0 15px rgba(255, 255, 255, 0.1);
            margin-bottom: 30px;
        }

        .container { max-width: 1400px; margin: 0 auto; }

        /* Controls Panel */
        .controls { 
            background: var(--panel-bg); 
            border: 1px solid var(--border-color); 
            padding: 25px; 
            border-radius: 12px; 
            backdrop-filter: blur(10px);
            box-shadow: 0 4px 24px rgba(0,0,0,0.2);
            margin-bottom: 30px;
            display: flex;
            flex-direction: column;
            gap: 20px;
            align-items: center;
        }

        /* Address Breakdown Visual */
        .addr-breakdown { 
            display: flex; 
            gap: 4px; 
            font-family: 'Space Mono', monospace; 
            font-size: 14px;
            background: #000;
            padding: 10px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }
        
        .bit-chunk { 
            padding: 8px 12px; 
            border-radius: 4px; 
            color: #0d1117;
            font-weight: bold;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-width: 70px;
            transition: transform 0.2s;
        }
        .bit-chunk small { font-size: 10px; opacity: 0.8; margin-top: 4px;}

        .chunk-l4 { background: #ff7b72; box-shadow: 0 0 10px rgba(255, 123, 114, 0.4); }
        .chunk-l3 { background: #d29922; box-shadow: 0 0 10px rgba(210, 153, 34, 0.4); }
        .chunk-l2 { background: #e3b341; box-shadow: 0 0 10px rgba(227, 179, 65, 0.4); }
        .chunk-l1 { background: #3fb950; box-shadow: 0 0 10px rgba(63, 185, 80, 0.4); }
        .chunk-off { background: #58a6ff; box-shadow: 0 0 10px rgba(88, 166, 255, 0.4); }

        /* Input Controls */
        .input-group {
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: center;
        }

        input[type="text"] { 
            background: #0d1117; 
            border: 1px solid var(--border-color); 
            color: var(--accent-primary); 
            padding: 10px 15px; 
            font-family: 'Space Mono', monospace; 
            width: 220px; 
            border-radius: 6px;
            font-size: 16px;
            outline: none;
            transition: all 0.3s;
        }
        input[type="text"]:focus { border-color: var(--accent-primary); box-shadow: var(--neon-glow); }

        button { 
            padding: 10px 20px; 
            border: none; 
            border-radius: 6px; 
            cursor: pointer; 
            font-weight: 600; 
            font-family: 'Inter', sans-serif;
            transition: all 0.2s;
            text-transform: uppercase;
            font-size: 12px;
            letter-spacing: 1px;
        }
        button:hover { transform: translateY(-2px); filter: brightness(1.2); }
        button:active { transform: translateY(0); }

        .btn-map { background: var(--accent-tertiary); color: #000; }
        .btn-walk { background: var(--accent-primary); color: #000; }
        .btn-reset { background: rgba(255,255,255,0.1); color: var(--text-main); border: 1px solid var(--border-color); }
        .btn-tutorial { background: linear-gradient(45deg, #ff7b72, #d29922); color: #000; box-shadow: 0 0 15px rgba(255, 123, 114, 0.3); }

        /* Visualization Area */
        .level-container { 
            display: flex; 
            justify-content: space-between; 
            align-items: flex-start; 
            gap: 20px; 
            overflow-x: auto;
            padding-bottom: 20px;
        }

        .level-col {
            flex: 1;
            min-width: 200px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .level-header {
            text-align: center;
            font-family: 'Space Mono', monospace;
            font-weight: bold;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 1px;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 10px;
        }

        .level-box { 
            background: rgba(22, 27, 34, 0.6); 
            border: 1px solid var(--border-color); 
            padding: 15px; 
            border-radius: 8px; 
            min-height: 120px;
            position: relative;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .level-box:hover {
            border-color: var(--text-dim);
            background: rgba(22, 27, 34, 0.9);
        }

        .arrow-container {
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            color: var(--text-dim);
            opacity: 0.5;
            padding-top: 60px; /* Align with boxes roughly */
        }

        /* Entry Styles */
        .entry-box {
            background: #0d1117; 
            padding: 12px; 
            border-radius: 6px; 
            font-family: 'Space Mono', monospace;
            font-size: 13px;
        }
        
        .entry-item { margin-bottom: 4px; display: flex; justify-content: space-between; }
        .entry-label { color: var(--text-dim); }
        .entry-val { color: var(--text-main); }
        
        .status-present { color: var(--accent-tertiary); font-weight: bold; text-shadow: 0 0 5px rgba(63, 185, 80, 0.5); }
        .status-fault { color: var(--accent-secondary); font-weight: bold; text-shadow: 0 0 5px rgba(255, 123, 114, 0.5); }

        .entry-active { 
            border: 1px solid var(--accent-tertiary); 
            box-shadow: 0 0 15px rgba(63, 185, 80, 0.2) inset;
        }
        .entry-fault { 
            border: 1px solid var(--accent-secondary); 
            box-shadow: 0 0 15px rgba(255, 123, 114, 0.2) inset;
        }

        /* Physical Page */
        #box-phys {
            background: rgba(13, 17, 23, 0.8);
            border: 1px dashed var(--accent-tertiary);
        }

        /* Logs */
        .log-box { 
            text-align: left; 
            background: #0d1117; 
            padding: 20px; 
            border-radius: 8px; 
            height: 180px; 
            overflow-y: auto; 
            font-family: 'Space Mono', monospace; 
            font-size: 13px; 
            border: 1px solid var(--border-color);
            color: var(--text-dim);
            margin-top: 30px;
        }
        .log-line { margin-bottom: 5px; border-bottom: 1px solid rgba(48, 54, 61, 0.5); padding-bottom: 2px; }
        .log-line:last-child { border-bottom: none; }
        .log-highlight { color: var(--accent-primary); }
        .log-error { color: var(--accent-secondary); }

        /* Tutorial Overlay */
        #tutorial-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.4); z-index: 1000; 
            /* backdrop-filter: blur(5px); Removed to allow visibility */
            display: none;
            pointer-events: none; /* Allow clicks to pass through if needed, though we block mostly */
        }
        
        .tutorial-highlight {
            position: relative; z-index: 1001; 
            box-shadow: 0 0 0 4px var(--accent-primary), 0 0 50px 0 rgba(0,0,0,0.8) !important;
            transform: scale(1.02);
            /* background: #161b22; REMOVED to preserve button colors */
            transition: all 0.3s;
        }

        #instruction-box {
            position: fixed; 
            top: 20%; right: 5%;
            background: #161b22; 
            color: var(--text-main); 
            padding: 0; /* Changed padding to handle header better */
            border-radius: 12px;
            width: 350px; 
            z-index: 1002; 
            text-align: left;
            border: 1px solid var(--accent-primary);
            box-shadow: 0 10px 30px rgba(0,0,0,0.8); 
            display: none;
            font-family: 'Inter', sans-serif;
            overflow: hidden;
        }
        
        #instruction-header {
            background: rgba(88, 166, 255, 0.2);
            padding: 15px;
            border-bottom: 1px solid var(--border-color);
            cursor: move;
            color: var(--accent-primary);
            margin: 0;
            font-size: 18px;
        }
        
        #instruction-content {
            padding: 20px;
        }
        
        #instruction-text { font-size: 15px; margin-bottom: 20px; line-height: 1.6; }
        
        #instruction-next { 
            background: var(--accent-primary); 
            color: #000; 
            width: 100%;
        }

        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #0d1117; }
        ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #58a6ff; }

    </style>
</head>
<body>
    <div class="container">
        <h1>Linux 4-Level Page Table Visualization (x86_64)</h1>
        
        <div class="controls" id="panel-controls">
            <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <div style="font-size: 14px; color: var(--text-dim);">INTERACTIVE DEMO</div>
                <button class="btn-tutorial" onclick="startTutorial()">Start Interactive Tutorial</button>
            </div>

            <div class="addr-breakdown" id="addr-bd">
                <div class="bit-chunk chunk-l4" id="chunk-l4">PGD <small>9 bits</small></div>
                <div class="bit-chunk chunk-l3" id="chunk-l3">PUD <small>9 bits</small></div>
                <div class="bit-chunk chunk-l2" id="chunk-l2">PMD <small>9 bits</small></div>
                <div class="bit-chunk chunk-l1" id="chunk-l1">PTE <small>9 bits</small></div>
                <div class="bit-chunk chunk-off" id="chunk-off">Offset <small>12 bits</small></div>
            </div>
            
            <div class="input-group">
                <label>Virtual Address:</label>
                <input type="text" id="va-input" value="0x7ff123456000" placeholder="0x...">
                <button class="btn-map" id="btn-map" onclick="req('map')">Map Address</button>
                <button class="btn-walk" id="btn-walk" onclick="req('walk')">Walk Address</button>
                <button class="btn-reset" onclick="req('reset')">Reset MMU</button>
            </div>
        </div>
        
        <div class="level-container" id="vis-container">
            <!-- PGD L4 -->
            <div class="level-col">
                <div class="level-header" style="color:#ff7b72">Level 4 (PGD)</div>
                <div class="level-box" id="box-l4">
                    <div id="content-l4" style="color:var(--text-dim); text-align:center; padding-top:20px;">Waiting...</div>
                </div>
            </div>
            
            <div class="arrow-container">→</div>
            
            <!-- PUD L3 -->
            <div class="level-col">
                <div class="level-header" style="color:#d29922">Level 3 (PUD)</div>
                <div class="level-box" id="box-l3">
                    <div id="content-l3" style="color:var(--text-dim); text-align:center; padding-top:20px;">Waiting...</div>
                </div>
            </div>
            
            <div class="arrow-container">→</div>
            
            <!-- PMD L2 -->
            <div class="level-col">
                <div class="level-header" style="color:#e3b341">Level 2 (PMD)</div>
                <div class="level-box" id="box-l2">
                    <div id="content-l2" style="color:var(--text-dim); text-align:center; padding-top:20px;">Waiting...</div>
                </div>
            </div>
            
            <div class="arrow-container">→</div>
            
            <!-- PTE L1 -->
            <div class="level-col">
                <div class="level-header" style="color:#3fb950">Level 1 (PTE)</div>
                <div class="level-box" id="box-l1">
                    <div id="content-l1" style="color:var(--text-dim); text-align:center; padding-top:20px;">Waiting...</div>
                </div>
            </div>
            
            <div class="arrow-container">→</div>
            
            <!-- Physical -->
            <div class="level-col">
                <div class="level-header" style="color:#58a6ff">Physical Page</div>
                <div class="level-box" id="box-phys">
                    <div id="content-phys" style="color:var(--text-dim); text-align:center; padding-top:20px;">--</div>
                </div>
            </div>
        </div>
        
        <div class="log-box" id="log-output">
            <div class="log-line">System Ready.</div>
        </div>
    </div>
    
    <!-- Tutorial Overlay -->
    <div id="tutorial-overlay"></div>
    <div id="instruction-box">
        <h3 id="instruction-header">Tutorial Guide (Drag me)</h3>
        <div id="instruction-content">
            <div id="instruction-text"></div>
            <button id="instruction-next" onclick="nextStep()">Next</button>
        </div>
    </div>

    <script>
        let isTutorial = false;
        let tStep = 0;
        
        // --- Drag Functionality ---
        const dragBox = document.getElementById("instruction-box");
        const dragHeader = document.getElementById("instruction-header");
        let isDragging = false;
        let currentX;
        let currentY;
        let initialX;
        let initialY;
        let xOffset = 0;
        let yOffset = 0;

        dragHeader.addEventListener("mousedown", dragStart);
        document.addEventListener("mouseup", dragEnd);
        document.addEventListener("mousemove", drag);

        function dragStart(e) {
            initialX = e.clientX - xOffset;
            initialY = e.clientY - yOffset;
            if (e.target === dragHeader) {
                isDragging = true;
            }
        }

        function dragEnd(e) {
            initialX = currentX;
            initialY = currentY;
            isDragging = false;
        }

        function drag(e) {
            if (isDragging) {
                e.preventDefault();
                currentX = e.clientX - initialX;
                currentY = e.clientY - initialY;
                xOffset = currentX;
                yOffset = currentY;
                setTranslate(currentX, currentY, dragBox);
            }
        }

        function setTranslate(xPos, yPos, el) {
            el.style.transform = `translate3d(${xPos}px, ${yPos}px, 0)`;
        }


        async function req(action) {
            const va = document.getElementById('va-input').value;
            const res = await fetch(`/${action}`, {
                method: 'POST',
                body: JSON.stringify({ va: va })
            });
            const data = await res.json();
            
            if (data.logs) {
                renderLogs(data.logs);
            }
            if (data.path) {
                renderPath(data.path, data.indices);
            }
            if (action === 'reset') {
                renderLogs(["MMU Reset Complete."]);
                clearVis();
            }
            if (action === 'map' && data.success) {
                renderLogs(["Mapping Successful.", "Now click 'Walk Address' to see the path."]);
            }
            
            if (isTutorial) checkTutorial(action, data);
        }
        
        function renderLogs(logs) {
            const el = document.getElementById('log-output');
            el.innerHTML = logs.map(l => {
                let cls = 'log-line';
                if(l.includes('FAULT')) cls += ' log-error';
                else if(l.includes('Successful')) cls += ' log-highlight';
                return `<div class="${cls}">${l}</div>`;
            }).join('');
            el.scrollTop = el.scrollHeight;
        }
        
        function clearVis() {
            [4,3,2,1].forEach(l => document.getElementById(`content-l${l}`).innerHTML = '<div style="color:var(--text-dim); text-align:center; padding-top:20px;">Waiting...</div>');
            document.getElementById('content-phys').innerHTML = '<div style="color:var(--text-dim); text-align:center; padding-top:20px;">--</div>';
        }
        
        function renderPath(path, indices) {
            // Render Breakdown
            const indicesBlocks = document.querySelectorAll('.bit-chunk');
            if(indicesBlocks.length > 0) {
                indicesBlocks[0].innerHTML = `PGD 9<br><span style="color:#fff">${indices[0]}</span>`;
                indicesBlocks[1].innerHTML = `PUD 9<br><span style="color:#fff">${indices[1]}</span>`;
                indicesBlocks[2].innerHTML = `PMD 9<br><span style="color:#fff">${indices[2]}</span>`;
                indicesBlocks[3].innerHTML = `PTE 9<br><span style="color:#fff">${indices[3]}</span>`;
                indicesBlocks[4].innerHTML = `Off 12<br><span style="color:#fff">0x${indices[4].toString(16)}</span>`;
            }

            clearVis();
            
            path.forEach(step => {
                const boxId = `content-l${step.level}`;
                let statusHtml = step.present 
                    ? '<span class="status-present">PRESENT (1)</span>' 
                    : '<span class="status-fault">NOT PRESENT (0)</span>';
                    
                let html = `
                    <div class="entry-box ${step.present ? 'entry-active' : 'entry-fault'}">
                        <div class="entry-item">
                            <span class="entry-label">Index</span>
                            <span class="entry-val">${step.index}</span>
                        </div>
                        <div class="entry-item">
                            <span class="entry-label">PTE Val</span>
                            <span class="entry-val" style="font-size:10px">${step.entry_val}</span>
                        </div>
                        <div class="entry-item" style="margin-top:8px; border-top:1px solid #30363d; padding-top:4px;">
                            ${statusHtml}
                        </div>
                        <div class="entry-item">
                            <span class="entry-label">Next PFN</span>
                            <span class="entry-val">${step.next_pfn}</span>
                        </div>
                    </div>
                `;
                document.getElementById(boxId).innerHTML = html;
                
                // If last step (PTE) and present, update physical
                if (step.level === 1 && step.present) {
                    const off = indices[4];
                    const pa = (BigInt(step.next_pfn) << 12n) | BigInt(off);
                    document.getElementById('content-phys').innerHTML = `
                         <div class="entry-box" style="border:1px solid var(--accent-tertiary);">
                            <div class="entry-item">
                                <span class="entry-label">Frame PFN</span>
                                <span class="entry-val">${step.next_pfn}</span>
                            </div>
                            <div class="entry-item">
                                <span class="entry-label">Offset</span>
                                <span class="entry-val">0x${off.toString(16)}</span>
                            </div>
                            <div class="entry-item" style="color:var(--accent-tertiary); margin-top:8px; font-weight:bold;">
                                <span class="entry-label">Phys Addr</span>
                                <span>0x${pa.toString(16)}</span>
                            </div>
                         </div>
                    `;
                }
            });
        }
        
        // --- Tutorial Logic ---
        
        function startTutorial() {
            req('reset'); // Start fresh
            isTutorial = true;
            tStep = 0;
            document.getElementById('tutorial-overlay').style.display = 'block';
            document.getElementById('instruction-box').style.display = 'block';
            nextStep();
        }
        
        function endTutorial() {
             isTutorial = false;
             document.getElementById('tutorial-overlay').style.display = 'none';
             document.getElementById('instruction-box').style.display = 'none';
             clearHighlights();
             renderLogs(["Tutorial Completed! You can now experiment freely."]);
        }
        
        function highlight(id) {
            clearHighlights();
            const el = document.getElementById(id);
            if(el) el.classList.add('tutorial-highlight');
        }
        
        function clearHighlights() {
            document.querySelectorAll('.tutorial-highlight').forEach(el => el.classList.remove('tutorial-highlight'));
        }
        
        function setInstruction(text, waitAction=false) {
            document.getElementById('instruction-text').innerHTML = text;
            const btn = document.getElementById('instruction-next');
            if (waitAction) {
                btn.style.display = 'none';
            } else {
                btn.style.display = 'inline-block';
                btn.innerText = 'Next Step';
            }
        }
        
        function nextStep() {
            tStep++;
            runStep();
        }
        
        function runStep() {
            switch(tStep) {
                case 1:
                    setInstruction("<b>Welcome to 4-Level Paging!</b><br><br>Linux (x86_64) uses 4 levels of tables to translate virtual addresses. <br><b>PGD -> PUD -> PMD -> PTE</b>.<br><br>The visualizer below shows each level as a column.");
                    break;
                case 2:
                    setInstruction("<b>Address Breakdown</b><br><br>The 48-bit Virtual Address is split into 5 parts.<br>Each <b>9-bit</b> chunk indexes into one of the page tables (512 entries).<br>The final <b>12 bits</b> are the offset within the 4KB page.", false);
                    highlight('addr-bd');
                    break;
                case 3:
                    setInstruction("<b>Step 1: Allocation</b><br><br>Let's map a valid address: <b>0x7ff123456000</b>.<br>Currently, the MMU tables are empty.<br><br>Click the <b>MAP ADDRESS</b> button.", true);
                    highlight('btn-map');
                    break;
                case 4:
                     setInstruction("<b>Mapping Complete</b><br><br>The OS has now created the table structure in memory.<br>But the CPU hasn't accessed it yet.<br><br>Click <b>WALK ADDRESS</b> to simulate a hardware table walk.", true);
                     highlight('btn-walk');
                     break;
                case 5:
                     setInstruction("<b>Hardware Walk (Translation)</b><br><br>Follow the path from left to right.<br>1. <b>PGD</b> (Level 4) -> Points to PUD PFN<br>2. <b>PUD</b> (Level 3) -> Points to PMD PFN<br>3. <b>PMD</b> (Level 2) -> Points to PTE PFN<br>4. <b>PTE</b> (Level 1) -> Points to Physical Page", false);
                     highlight('vis-container');
                     break;
                case 6:
                     setInstruction("<b>Physical Address</b><br><br>The Translation is successful.<br>The final Physical Address is calculated by combining the Frame PFN from the PTE with the Offset.<br><br>See the final result in the rightmost box.", false);
                     highlight('box-phys');
                     break;
                case 7:
                     setInstruction("<b>Page Fault Test</b><br><br>Now let's see what happens when we access unmapped memory.<br>I'll change the address to <b>0xdeadbeef</b>.<br><br>Click <b>WALK ADDRESS</b> to try it.", true);
                     document.getElementById('va-input').value = "0xdeadbeef";
                     highlight('btn-walk');
                     break;
                case 8:
                     setInstruction("<b>PAGE FAULT!</b><br><br>The walk failed at Level 4 (PGD).<br>The entry for index 0 was <b>NOT PRESENT</b>.<br>The CPU raises a #PF exception.<br><br>This concludes the tutorial.", false);
                     break;
                default:
                     endTutorial();
            }
        }
        
        function checkTutorial(action, data) {
            if (tStep === 3 && action === 'map' && data.success) {
                nextStep();
            }
            if (tStep === 4 && action === 'walk' && data.success) {
                nextStep();
            }
            if (tStep === 7 && action === 'walk' && !data.success) {
                nextStep();
            }
        }

    </script>
</body>
</html>
"""

class SimpleHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(HTML_CONTENT.encode('utf-8'))

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = json.loads(self.rfile.read(length))
        
        va_str = data.get('va', '0')
        try:
            va = int(va_str, 16)
        except:
            va = 0
            
        res = {}
        
        if self.path == '/map':
            success = mmu.map_page(va)
            res = {"success": success}
        elif self.path == '/walk':
            res = mmu.walk(va)
        elif self.path == '/reset':
            mmu.reset()
            res = {"success": True}
            
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(res).encode('utf-8'))
    
    def log_message(self, format, *args):
        return

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    print(f"Starting 4-Level Page Table Demo on port {PORT}...")
    webbrowser.open(f'http://localhost:{PORT}')
    try:
        with ReusableTCPServer(("", PORT), SimpleHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
