import http.server
import socketserver
import json
import webbrowser
import os
import sys

PORT = 8002 

# Bit Masks for 64-bit PTE (Linux x86_64 style simplified)
_PAGE_PRESENT  = 1 << 0
_PAGE_RW       = 1 << 1
_PAGE_USER     = 1 << 2
_PAGE_ACCESSED = 1 << 5
_PAGE_DIRTY    = 1 << 6
_PAGE_NX       = 1 << 63

PAGE_SHIFT = 12
PAGE_SIZE = 1 << PAGE_SHIFT
PFN_MASK = 0x000FFFFFFFFFF000 # Bits 12-51

class PTE:
    def __init__(self, value=0):
        self.value = value

    def set_flag(self, flag, enable=True):
        if enable:
            self.value |= flag
        else:
            self.value &= ~flag

    def has_flag(self, flag):
        return (self.value & flag) != 0

    def get_pfn(self):
        return (self.value & PFN_MASK) >> PAGE_SHIFT

    def set_pfn(self, pfn):
        # Clear old PFN
        self.value &= ~PFN_MASK
        # Set new PFN
        self.value |= (pfn << PAGE_SHIFT) & PFN_MASK

    def to_dict(self):
        return {
            "value_hex": f"0x{self.value:016x}",
            "pfn": self.get_pfn(),
            "flags": {
                "P": self.has_flag(_PAGE_PRESENT),
                "RW": self.has_flag(_PAGE_RW),
                "US": self.has_flag(_PAGE_USER),
                "A": self.has_flag(_PAGE_ACCESSED),
                "D": self.has_flag(_PAGE_DIRTY),
                "NX": self.has_flag(_PAGE_NX)
            }
        }

class PageTable:
    def __init__(self, size=1024):
        self.entries = [PTE(0) for _ in range(size)]

class MMU:
    def __init__(self):
        # Simulate Physical Memory as a dict: PFN -> PageTable Object or Data Page
        # In a real system, this is just RAM. Here objects represent physical frames.
        self.phys_mem = {} # PFN -> PageTable
        self.next_free_pfn = 1
        
        # Allocate PGD (Page Global Directory) at PFN 100
        self.pgd_pfn = 100
        self.cr3 = self.pgd_pfn << PAGE_SHIFT # CR3 stores Physical Address of PGD
        
        self.phys_mem[self.pgd_pfn] = PageTable(1024)

    def get_pfn_from_phys(self, phys_addr):
        return phys_addr >> PAGE_SHIFT

    def alloc_frame(self):
        pfn = self.next_free_pfn
        self.next_free_pfn += 1
        return pfn
        
    def map_page(self, va, flags=_PAGE_PRESENT | _PAGE_RW | _PAGE_USER):
        # Break down VA (32-bit simplified: 10 | 10 | 12)
        dir_idx = (va >> 22) & 0x3FF
        tbl_idx = (va >> 12) & 0x3FF
        
        # 1. Get PGD
        pgd = self.phys_mem[self.pgd_pfn]
        pde = pgd.entries[dir_idx]
        
        # 2. Check if Page Table exists
        if not pde.has_flag(_PAGE_PRESENT):
            # Create new Page Table
            pt_pfn = self.alloc_frame()
            self.phys_mem[pt_pfn] = PageTable(1024)
            
            # Update PDE to point to new PT
            pde.set_pfn(pt_pfn)
            pde.set_flag(_PAGE_PRESENT)
            pde.set_flag(_PAGE_RW) # Table itself is RW
            pde.set_flag(_PAGE_USER)
            
        # 3. Get Page Table
        pt_pfn = pde.get_pfn()
        pt = self.phys_mem[pt_pfn]
        pte = pt.entries[tbl_idx]
        
        # 4. Map the final page
        if not pte.has_flag(_PAGE_PRESENT):
            data_pfn = self.alloc_frame()
            pte.set_pfn(data_pfn)
            pte.value |= flags # Set user requested flags
            # Ensure Present is set
            pte.set_flag(_PAGE_PRESENT)

    def walk(self, va, write=False):
        log = []
        
        dir_idx = (va >> 22) & 0x3FF
        tbl_idx = (va >> 12) & 0x3FF
        offset  = va & 0xFFF
        
        log.append(f"Walking VA 0x{va:08x}")
        log.append(f"Breakdown: PGD Index {dir_idx} | PTE Index {tbl_idx} | Offset 0x{offset:03x}")
        
        # Step 1: CR3 -> PGD
        log.append(f"CR3 Read: Base PFN {self.pgd_pfn}")
        pgd = self.phys_mem[self.pgd_pfn]
        pde = pgd.entries[dir_idx]
        
        log.append(f"PGD[{dir_idx}]: Val 0x{pde.value:x} (P={int(pde.has_flag(_PAGE_PRESENT))})")
        
        if not pde.has_flag(_PAGE_PRESENT):
            return {"error": "Page Fault (PGD Entry Not Present)", "logs": log}

        # Step 2: PGD -> PT
        pt_pfn = pde.get_pfn()
        log.append(f"Following PDE -> Page Table at PFN {pt_pfn}")
        pt = self.phys_mem.get(pt_pfn)
        if not pt:
             return {"error": "Critical: Page Table Physical Frame Missing!", "logs": log}
             
        pte = pt.entries[tbl_idx]
        log.append(f"PTE[{tbl_idx}]: Val 0x{pte.value:x} (P={int(pte.has_flag(_PAGE_PRESENT))}, RW={int(pte.has_flag(_PAGE_RW))})")
        
        if not pte.has_flag(_PAGE_PRESENT):
             return {"error": "Page Fault (PTE Not Present)", "logs": log, "pte_snapshot": pte.to_dict()}
        
        # Step 3: Check Permissions & Update Bits (Hardware Logic)
        pte.set_flag(_PAGE_ACCESSED)
        log.append("Hardware: Set Accessed (A) bit")
        
        if write:
            if not pte.has_flag(_PAGE_RW):
                return {"error": "Page Fault (Permission Denied: Write to Read-Only Page)", "logs": log, "pte_snapshot": pte.to_dict()}
            pte.set_flag(_PAGE_DIRTY)
            log.append("Hardware: Write detected. Set Dirty (D) bit")
            
        data_pfn = pte.get_pfn()
        pa = (data_pfn << PAGE_SHIFT) | offset
        log.append(f"Translation Successful: PA 0x{pa:x} (PFN {data_pfn} + Offset 0x{offset:03x})")
        
        return {
            "success": True, 
            "pa": pa, 
            "logs": log,
            "pte_snapshot": pte.to_dict() # Return state AFTER hardware updates
        }
        
    def modify_pte(self, va, flag, enable):
        # Admin tool to mess with bits manually
        dir_idx = (va >> 22) & 0x3FF
        tbl_idx = (va >> 12) & 0x3FF
        
        pgd = self.phys_mem[self.pgd_pfn]
        pde = pgd.entries[dir_idx]
        if not pde.has_flag(_PAGE_PRESENT): return False
        
        pt = self.phys_mem[pde.get_pfn()]
        pte = pt.entries[tbl_idx]
        if not pte.has_flag(_PAGE_PRESENT): return False
        
        # New: Handle PFN Increment for CoW Sim
        if flag == 'PFN_INC':
            old_pfn = pte.get_pfn()
            new_pfn = self.alloc_frame() # actually alloc new frame
            pte.set_pfn(new_pfn)
            pte.set_flag(_PAGE_RW, True) # CoW implies the new private page is Writable
            return True

        bit_map = {
            "RW": _PAGE_RW,
            "US": _PAGE_USER,
            "NX": _PAGE_NX,
            "P": _PAGE_PRESENT
        }
        if flag in bit_map:
            pte.set_flag(bit_map[flag], enable)
            return True
        return False
        
    def reset(self):
        self.phys_mem = {}
        self.next_free_pfn = 1
        self.pgd_pfn = 100
        self.cr3 = self.pgd_pfn << PAGE_SHIFT
        self.phys_mem[self.pgd_pfn] = PageTable(1024)


# Global MMU
mmu = MMU()
mmu.map_page(0x12345678) 

# --- Web Server ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page Table Visualization</title>
    <style>
        body { font-family: 'Segoe UI', monospace; background: #1e1e2e; color: #cdd6f4; padding: 20px; display: flex; flex-direction: column; align-items: center; }
        h1 { color: #89b4fa; }
        
        .main-container { display: flex; gap: 20px; width: 100%; max-width: 1200px; position: relative; }
        .control-panel { flex: 1; background: #313244; padding: 20px; border-radius: 8px; z-index: 10; }
        .vis-panel { flex: 2; background: #313244; padding: 20px; border-radius: 8px; display: flex; flex-direction: column; gap: 20px; z-index: 10;}
        
        input { background: #45475a; border: 1px solid #585b70; color: white; padding: 8px; font-family: monospace; width: 120px; }
        button { padding: 8px 16px; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; margin-right: 5px; }
        .btn-green { background: #a6e3a1; color: #1e1e2e; }
        .btn-blue { background: #89b4fa; color: #1e1e2e; }
        .btn-red { background: #f38ba8; color: #1e1e2e; }
        .btn-tutorial { background: #f9e2af; color: #1e1e2e; margin-bottom: 15px; width: 100%; }
        
        .address-breakdown { display: flex; gap: 5px; font-size: 14px; margin-bottom: 20px; }
        .addr-part { padding: 10px; border-radius: 4px; text-align: center; }
        .part-pgd { background: #fab387; color: #1e1e2e; width: 30%; }
        .part-pte { background: #f9e2af; color: #1e1e2e; width: 30%; }
        .part-off { background: #94e2d5; color: #1e1e2e; width: 40%; }
        
        .hardware-walk { background: #181825; padding: 15px; border-radius: 4px; font-size: 12px; height: 150px; overflow-y: auto; color: #a6adc8; border: 1px solid #45475a;}
        
        .pte-inspector { background: #11111b; padding: 15px; border-radius: 8px; border: 1px solid #cba6f7; }
        .pte-bits { display: flex; gap: 2px; margin-top: 10px; flex-wrap: wrap; }
        .bit-box { 
            width: 30px; height: 40px; border: 1px solid #45475a; 
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            font-size: 10px; cursor: pointer;
        }
        .bit-header { color: #6c7086; margin-bottom: 2px; }
        .bit-val { font-weight: bold; font-size: 14px; }
        
        .flag-active { background: #a6e3a1; color: #1e1e2e; border-color: #a6e3a1; }
        .flag-inactive { background: #313244; color: #6c7086; }
        .flag-fault { background: #f38ba8 !important; }
        
        .log-line { margin: 2px 0; }
        .log-error { color: #f38ba8; font-weight: bold; }
        .log-success { color: #a6e3a1; }
        
        .toggle-btn { font-size: 9px; padding: 2px 5px; margin-top: 5px; background: #45475a; color: white; border: none; cursor: pointer; }
        .toggle-btn:hover { background: #585b70; }

        /* Tutorial Styles */
        #tutorial-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7); z-index: 100; pointer-events: none;
            display: none;
        }
        .tutorial-highlight {
            position: relative; z-index: 101; box-shadow: 0 0 15px 5px #f9e2af; border-radius: 4px; animation: pulse 1.5s infinite;
        }
        @keyframes pulse { 0% { box-shadow: 0 0 10px 0px #f9e2af; } 50% { box-shadow: 0 0 20px 5px #f9e2af; } 100% { box-shadow: 0 0 10px 0px #f9e2af; } }
        
        #instruction-box {
            position: fixed; top: 80px; right: 20px;
            background: #f9e2af; color: #1e1e2e; padding: 20px; border-radius: 8px;
            width: 300px; z-index: 102; text-align: center;
            box-shadow: 0 4px 10px rgba(0,0,0,0.5); display: none;
        }
        #instruction-text { font-size: 14px; margin-bottom: 10px; font-weight: bold; }
        #instruction-next { background: #1e1e2e; color: #f9e2af; padding: 5px 15px; border: none; cursor: pointer; border-radius: 4px; }

    </style>
</head>
<body>

    <h1>Page Table & MMU Visualization</h1>
    
    <div style="margin-bottom: 20px;">
        <h3>Tutorials</h3>
        <button class="btn-tutorial" onclick="startTutorial('basic')">1. Basic Walkthrough</button>
        <button class="btn-tutorial" style="background:#89dceb" onclick="startTutorial('cow')">2. Copy-on-Write (CoW)</button>
    </div>

    <div class="main-container">
        <!-- Controls -->
        <div class="control-panel" id="panel-controls">
            <h3>MMU Controls</h3>
            <div style="margin-bottom: 15px;" id="input-group">
                <label>Virtual Address (Hex):</label><br>
                <input type="text" id="va-input" value="0x12345678">
            </div>
            
            <div style="margin-bottom: 20px;" id="action-group">
                <span>Action:</span><br><br>
                <div style="display:inline-block" id="btn-group-read"><button class="btn-green"  onclick="walk('READ')">READ (Load)</button></div>
                <div style="display:inline-block" id="btn-group-write"><button class="btn-red"   onclick="walk('WRITE')">WRITE (Store)</button></div>
                <div style="display:inline-block" id="btn-group-map"><button class="btn-blue"  onclick="alloc()">MAP (Alloc)</button></div>
            </div>
            
            <hr style="border-color: #45475a;">
            <div style="font-size: 0.9em; color: #bac2de;">
                <p><b>Instructions:</b></p>
                1. <b>MAP</b>: Create a page table mapping for the address.<br>
                2. <b>READ</b>: Watch the MMU walk. Notice the 'A' (Accessed) bit set.<br>
                3. <b>WRITE</b>: Watch the 'D' (Dirty) bit set.<br>
                4. <b>Experiment</b>: Toggle 'RW' off below, then try to WRITE!
            </div>
        </div>
        
        <!-- Visualization -->
        <div class="vis-panel">
            <!-- (Visualization content largely same, omitting for brevity in diff tool if possible, but replace needs full context usually) -->
             <!-- Address Breakdown -->
            <div>
                <h4>1. Virtual Address Breakdown (32-bit Sim)</h4>
                <div class="address-breakdown">
                    <div class="addr-part part-pgd">
                        <div>Directory Index</div>
                        <div id="disp-pgd" style="font-weight:bold; font-size:1.2em;">--</div>
                        <div class="bit-range">Bits 31-22</div>
                    </div>
                    <div class="addr-part part-pte">
                        <div>Table Index</div>
                        <div id="disp-pte" style="font-weight:bold; font-size:1.2em;">--</div>
                        <div class="bit-range">Bits 21-12</div>
                    </div>
                    <div class="addr-part part-off">
                        <div>Offset</div>
                        <div id="disp-off" style="font-weight:bold; font-size:1.2em;">--</div>
                        <div class="bit-range">Bits 11-0</div>
                    </div>
                </div>
                
                <!-- New: Calculation Explanation -->
                <div class="calc-box">
                    <strong>Binary Breakdown:</strong>
                    <div id="binary-disp" style="font-family:monospace; margin-top:5px; letter-spacing:1px;">
                        <span style="color:#fab387">0000000000</span> <span style="color:#f9e2af">0000000000</span> <span style="color:#94e2d5">000000000000</span>
                    </div>
                    <div style="margin-top:5px; font-size:11px; color:#a6adc8;">
                        PGD Index = (VA >> 22) & 0x3FF<br>
                        PTE Index = (VA >> 12) & 0x3FF
                    </div>
                </div>
            </div>

            <!-- Hardware Walk Log -->
            <div id="walk-container">
                <h4>2. Hardware MMU Walk (CR3 -> PA)</h4>
                <div id="walk-log" class="hardware-walk">Waiting for request...</div>
            </div>

            <!-- PTE Inspector -->
            <div id="pte-container" style="display:none;">
                <h4>3. PTE Inspector (Page Table Entry)</h4>
                <div class="pte-inspector">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span>Raw Value: <span id="pte-raw" style="font-family:monospace; color:#fab387;"></span></span>
                        <span id="pte-status" style="font-weight:bold;"></span>
                    </div>
                    
                    <div class="pte-bits" id="pte-bits">
                        <!-- Bits rendered here -->
                    </div>
                     
                    <!-- New: PFN Explanation -->
                    <div class="calc-box" style="margin-top:10px; background:#1e1e2e;">
                        <strong>How to get PFN?</strong><br>
                        PFN = (PTE_Value & 0x000FFFFFFFFFF000) >> 12<br>
                        <span style="font-size:10px; color:#6c7086;">(Masks out flags, keeps bits 12-51)</span>
                    </div>
                    
                    <div style="margin-top: 10px; font-size: 11px; color: #a6adc8;">
                        * Click Toggle buttons below to simulate Admin/OS changes.
                    </div>
                    <div style="display:flex; gap:10px; margin-top:5px;" id="toggle-group">
                        <button class="toggle-btn" id="btn-toggle-rw" onclick="toggleBit('RW', false)">Clear RW (Make Read-Only)</button>
                        <button class="toggle-btn" onclick="toggleBit('RW', true)">Set RW (Make Writable)</button>
                        <button class="toggle-btn" onclick="toggleBit('P', false)">Clear Present (Sim Swap)</button>
                    </div>
                </div>
            </div>
            
        </div>
    </div>

    <!-- Tutorial Overlay -->
    <div id="tutorial-overlay"></div>
    <div id="instruction-box">
        <div id="instruction-text"></div>
        <button id="instruction-next" onclick="nextStep()">Next</button>
    </div>

    <script>
        const PAGE_SHIFT = 12;
        let tutorialStep = 0;
        let tutorialMode = 'basic'; 
        let isTutorialActive = false;

        async function walk(type) {
            const vaStr = document.getElementById('va-input').value;
            const res = await fetch('/walk', {
                method: 'POST',
                body: JSON.stringify({ va: vaStr, type: type })
            });
            const data = await res.json();
            
            renderBreakdown(data.breakdown);
            renderBinary(data.breakdown.va_int);
            renderLog(data.logs, data.error);
            
            if (data.pte) {
                renderPTE(data.pte, data.error);
            } else {
                document.getElementById('pte-container').style.display = 'none';
            }
            
            if(isTutorialActive) checkTutorialProgress(type, data.error);
        }

        async function alloc(flags='default') {
            const vaStr = document.getElementById('va-input').value;
            await fetch('/alloc', {
                method: 'POST',
                body: JSON.stringify({ va: vaStr, flags })
            });
            walk('READ'); 
            if(isTutorialActive && tutorialStep === 1) nextStep();
        }
        
        async function toggleBit(flag, enable) {
            const vaStr = document.getElementById('va-input').value;
            await fetch('/modify', {
                method: 'POST',
                body: JSON.stringify({ va: vaStr, flag, enable })
            });
            walk('READ');
            if(isTutorialActive && tutorialMode === 'basic' && tutorialStep === 7 && flag === 'RW' && !enable) nextStep();
        }
        
        async function cowHandlerSimulation() {
            // Simulate OS Handler:
            // 1. Allocate NEW frame (simulated by toggle PFN or just map new)
            // 2. Copy data (not visualized here)
            // 3. Update PTE -> New PFN, RW=1
            const vaStr = document.getElementById('va-input').value;
            
            // For sim, we just re-alloc with RW enabled. 
            // In real OS, we'd verify refcounts etc.
            await fetch('/alloc', {
                method: 'POST',
                body: JSON.stringify({ va: vaStr, flags: 'RW' }) 
            });
            
            // Force PFN change to visualize "New Page"
            await fetch('/modify', {
                 method: 'POST',
                 body: JSON.stringify({ va: vaStr, flag: 'PFN_INC', enable: true }) // Special hack for demo
            });
            
            walk('READ');
            nextStep();
        }

        async function resetServer() {
             await fetch('/reset', { method: 'POST' });
             document.getElementById('walk-log').innerHTML = 'System Reset.';
             document.getElementById('pte-container').style.display = 'none';
        }

        // --- Tutorial Logic ---
        function startTutorial(mode) {
            isTutorialActive = true;
            tutorialMode = mode;
            tutorialStep = 0;
            resetServer(); 
            document.getElementById('tutorial-overlay').style.display = 'block';
            document.getElementById('instruction-box').style.display = 'block';
            nextStep();
        }

        function endTutorial() {
            isTutorialActive = false;
            document.getElementById('tutorial-overlay').style.display = 'none';
            document.getElementById('instruction-box').style.display = 'none';
            clearHighlights();
            alert("Tutorial Completed!");
        }

        function highlight(id) {
            clearHighlights();
            const el = document.getElementById(id);
            if(el) el.classList.add('tutorial-highlight');
        }

        function clearHighlights() {
            document.querySelectorAll('.tutorial-highlight').forEach(el => el.classList.remove('tutorial-highlight'));
        }

        function setInstruction(text, autoNext=false, customBtn=null) {
            document.getElementById('instruction-text').innerHTML = text;
            const nextBtn = document.getElementById('instruction-next');
            if (customBtn) {
                nextBtn.style.display = 'inline-block';
                nextBtn.innerText = customBtn.text;
                nextBtn.onclick = customBtn.action;
            } else {
                nextBtn.innerText = "Next";
                nextBtn.onclick = nextStep;
                nextBtn.style.display = autoNext ? 'none' : 'inline-block';
            }
        }

        function nextStep() {
            tutorialStep++;
            if (tutorialMode === 'basic') runBasicStep();
            else if (tutorialMode === 'cow') runCowStep();
        }

        function runBasicStep() {
            switch(tutorialStep) {
                case 1:
                    setInstruction("Step 1: Allocation.<br>First, we need to map a page.<br>Click <b>MAP</b>.", true);
                    highlight('btn-group-map');
                    break;
                case 2:
                    setInstruction("Step 2: Read.<br>Simulate CPU READ.<br>Click <b>READ</b>.", true);
                    highlight('btn-group-read');
                    break;
                case 3:
                     setInstruction("Step 3: Accessed Bit.<br>Hardware sets <b>'A'</b> bit.<br>Click Next.");
                     highlight('pte-bits');
                     break;
                case 4:
                    setInstruction("Step 4: Write.<br>Simulate STORE/WRITE.<br>Click <b>WRITE</b>.", true);
                    highlight('btn-group-write');
                    break;
                case 5:
                    setInstruction("Step 5: Dirty Bit.<br>Hardware sets <b>'D'</b> bit.<br>Click Next.");
                    highlight('pte-bits');
                    break;
                case 6:
                    setInstruction("Step 6: Breakdown.<br>See Binary Breakdown above.<br>Click Next.");
                    highlight('binary-disp');
                    break;
                case 7:
                    setInstruction("Step 7: Permissions.<br>Click <b>Clear RW</b> to make it Read-Only.", true);
                    highlight('btn-toggle-rw');
                    break;
                case 8:
                    setInstruction("Step 8: Page Fault.<br>Try to <b>WRITE</b> to this Read-Only page.", true);
                    highlight('btn-group-write');
                    break;
                default:
                    endTutorial();
            }
        }

        function runCowStep() {
            switch(tutorialStep) {
                case 1:
                    setInstruction("<b>CoW Scenario</b><br>Process A forks Process B.<br>They allow specific Read-Only access to the same physical page.<br>Click <b>MAP</b> (Simulating shared RO mapping).", true);
                    // Hack: We need MAP to create specific RO page. 
                    // We'll intercept 'alloc' call in checkTutorialProgress or modify alloc behavior
                    highlight('btn-group-map');
                    break;
                case 2:
                    // Force RW=0 for this tutorial step
                    setInstruction("Step 2: Read-Only Shared Page.<br>This page is currently <b>Read-Only</b> (RW=0).<br>Both processes can READ it safely.<br>Click <b>READ</b>.", true);
                    toggleBit('RW', false); // Silent toggle
                    highlight('btn-group-read');
                    break;
                 case 3:
                    setInstruction("Step 3: Attempt to Write.<br>Now Process B tries to modify the data.<br>Click <b>WRITE</b>.", true);
                    highlight('btn-group-write');
                    break;
                 case 4:
                    // OS Handler Step
                    setInstruction("<b>PAGE FAULT!</b><br>CPU denied access.<br>The OS Trap Handler catches this.<br>It sees the 'CoW' marker (implementation specific).<br>It must:<br>1. Alloc new page<br>2. Copy data<br>3. Update PTE to new PFN & RW=1.<br><br>Click <b>Simulate OS Handler</b>.", false, {text: "Simulate OS Handler", action: cowHandlerSimulation});
                    highlight('pte-bits');
                    break;
                 case 5:
                    setInstruction("Step 5: Retry Write.<br>The Page Table is updated!<br>New PFN, and RW=1.<br>Now, try <b>WRITE</b> again.", true);
                    highlight('btn-group-write');
                    break;
                 default:
                    endTutorial();
            }
        }

        function checkTutorialProgress(action, error) {
            if (tutorialMode === 'basic') {
                if(tutorialStep === 2 && action === 'READ') nextStep();
                if(tutorialStep === 4 && action === 'WRITE') nextStep();
                if(tutorialStep === 8 && action === 'WRITE' && error) {
                     setTimeout(() => { alert("PAGE FAULT! (Expected)"); nextStep(); }, 500);
                }
            } else if (tutorialMode === 'cow') {
                if(tutorialStep === 1 && action === 'READ') nextStep(); // alloc calls read
                if(tutorialStep === 2 && action === 'READ') nextStep();
                if(tutorialStep === 3 && action === 'WRITE' && error) nextStep();
                if(tutorialStep === 5 && action === 'WRITE' && !error) {
                     setTimeout(() => { alert("Success! CoW complete."); nextStep(); }, 500);
                }
            }
        }

        // --- Render Helpers ---

        function renderBreakdown(bd) {
            if(!bd) return;
            document.getElementById('disp-pgd').innerText = bd.dir_idx;
            document.getElementById('disp-pte').innerText = bd.tbl_idx;
            document.getElementById('disp-off').innerText = '0x' + bd.offset.toString(16);
        }

        function renderBinary(va) {
            if (va === undefined) return;
            // 32-bit string
            let binStr = (va >>> 0).toString(2).padStart(32, '0');
            
            // Slice it 10 | 10 | 12
            // Since it's 32 bits, bits 31-22 are first 10, 21-12 next 10, 11-0 last 12
            let pgdBin = binStr.substring(0, 10);
            let pteBin = binStr.substring(10, 20);
            let offBin = binStr.substring(20, 32);
            
            document.getElementById('binary-disp').innerHTML = 
                `<span style="color:#fab387" title="PGD Index">${pgdBin}</span> ` +
                `<span style="color:#f9e2af" title="PTE Index">${pteBin}</span> ` +
                `<span style="color:#94e2d5" title="Offset">${offBin}</span>`;
        }

        function renderLog(logs, error) {
            const el = document.getElementById('walk-log');
            el.innerHTML = logs.map(l => `<div class="log-line">${l}</div>`).join('');
            if(error) {
                el.innerHTML += `<div class="log-line log-error">!!! ${error} !!!</div>`;
            } else {
                el.innerHTML += `<div class="log-line log-success">Done.</div>`;
            }
            el.scrollTop = el.scrollHeight;
        }

        function renderPTE(pte, error) {
            document.getElementById('pte-container').style.display = 'block';
            document.getElementById('pte-raw').innerText = pte.value_hex;
            
            const bitsContainer = document.getElementById('pte-bits');
            bitsContainer.innerHTML = '';
            
            const flags = [
                {k: 'NX', l: 'NoExec'},
                {k: 'PFN', l: 'PFN (Frame)', w: 100, val: pte.pfn}, 
                {k: 'D', l: 'Dirty'},
                {k: 'A', l: 'Accessed'},
                {k: 'US', l: 'User'},
                {k: 'RW', l: 'Read/Write'},
                {k: 'P', l: 'Present'}
            ];
            
            flags.forEach(f => {
                const box = document.createElement('div');
                box.className = 'bit-box';
                if (f.w) box.style.width = f.w + 'px';
                
                let isActive = false;
                let displayVal = '';
                
                if (f.k === 'PFN') {
                    displayVal = f.val;
                    isActive = false; // Neutral color for PFN
                } else {
                    isActive = pte.flags[f.k];
                    displayVal = isActive ? 1 : 0;
                    if(isActive) box.classList.add('flag-active');
                    else box.classList.add('flag-inactive');
                }
                
                // Highlight fault cause?
                if (error) {
                     if (error.includes('Not Present') && f.k === 'P' && !isActive) box.classList.add('flag-fault');
                     if (error.includes('Permission') && f.k === 'RW' && !isActive) box.classList.add('flag-fault');
                }

                box.innerHTML = `<div class="bit-header">${f.k}</div><div class="bit-val">${displayVal}</div>`;
                box.title = f.l;
                bitsContainer.appendChild(box);
            });
        }
    </script>
</body>
</html>
"""

class PTRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
             data = json.loads(self.rfile.read(length).decode('utf-8'))
        except:
             data = {}
        
        va_str = data.get('va', '0')
        try:
            va = int(va_str, 16)
        except:
            va = 0

        response = {}
        
        # Helper to calc breakdown
        response["breakdown"] = {
            "va_int": va,
            "dir_idx": (va >> 22) & 0x3FF,
            "tbl_idx": (va >> 12) & 0x3FF,
            "offset": va & 0xFFF
        }

        if self.path == '/walk':
            # Perform Walk
            write_mode = (data.get('type') == 'WRITE')
            res = mmu.walk(va, write=write_mode)
            response.update(res)
            if "pte_snapshot" in res:
                response["pte"] = res["pte_snapshot"]
        
        elif self.path == '/alloc':
            req_flags = data.get('flags')
            # Parse flags string to int mask
            flags_mask = _PAGE_PRESENT | _PAGE_RW | _PAGE_USER # default
            if req_flags == 'RW':
                 flags_mask = _PAGE_PRESENT | _PAGE_RW | _PAGE_USER
            elif req_flags == 'RO': # Read Only
                 flags_mask = _PAGE_PRESENT | _PAGE_USER # No RW
            
            mmu.map_page(va, flags=flags_mask)
            response["success"] = True
            
        elif self.path == '/modify':
            mmu.modify_pte(va, data.get('flag'), data.get('enable'))
            response["success"] = True

        elif self.path == '/reset':
            mmu.reset()
            # Restore default mapping
            mmu.map_page(0x12345678)
            response["success"] = True

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    print(f"Starting Page Table Visualization on port {PORT}...")
    webbrowser.open(f'http://localhost:{PORT}')
    with ReusableTCPServer(("", PORT), PTRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
