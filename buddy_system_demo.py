import http.server
import socketserver
import json
import webbrowser
import math
import os
import sys

PORT = 8000
MAX_ORDER = 9
TOTAL_MEMORY = 512 # 2^9
PAGE_SIZE = 1 # Start with smallest unit as page for simplicity in visualization, but let's say Order 0 is a Page.

# --- Enhanced Buddy System Logic ---

class Block:
    def __init__(self, pfn, order, is_free=True):
        self.pfn = pfn
        self.order = order
        self.size = 1 << order
        self.is_free = is_free
        self.slab_type = None # Track if used by a specific slab cache

    def to_dict(self):
        return {
            "address": self.pfn,
            "size": self.size,
            "order": self.order,
            "is_free": self.is_free,
            "slab_type": self.slab_type
        }

class BuddyAllocator:
    def __init__(self, total_size):
        self.total_size = total_size
        self.max_order = int(math.log2(total_size))
        self.free_area = {i: [] for i in range(self.max_order + 1)}
        self.blocks = {} 
        
        initial_block = Block(0, self.max_order, True)
        self.free_area[self.max_order].append(0)
        self.blocks[0] = initial_block
        
        self.logs = []

    def log(self, message):
        self.logs.append(message)

    def get_blocks_sorted(self):
        sorted_blocks = []
        current_pfn = 0
        while current_pfn < self.total_size:
            if current_pfn in self.blocks:
                block = self.blocks[current_pfn]
                sorted_blocks.append(block)
                current_pfn += block.size
            else:
                current_pfn += 1 
        return sorted_blocks

    def allocate(self, size, slab_owner=None):
        # self.logs = [] # Don't clear logs here if called by Slab, as we want to preserve Slab logs too.
        # But for direct calls we might want to. Let's append to logs.
        
        if size <= 0: return None
        
        req_order = 0
        if size > 1:
            req_order = (size - 1).bit_length()
        
        self.log(f"[Buddy] Request: {size} units -> Need Order {req_order}")
        
        current_order = req_order
        while current_order <= self.max_order:
            if self.free_area[current_order]:
                pfn = self.free_area[current_order].pop(0)
                self.log(f"[Buddy] Found free block at PFN {pfn} (Order {current_order})")
                
                while current_order > req_order:
                    current_order -= 1
                    buddy_pfn = pfn + (1 << current_order)
                    self.log(f"[Buddy] Splitting PFN {pfn} (Order {current_order+1}) -> Buddy {buddy_pfn}")
                    
                    buddy_block = Block(buddy_pfn, current_order, True)
                    self.blocks[buddy_pfn] = buddy_block
                    self.free_area[current_order].append(buddy_pfn)
                    
                    self.blocks[pfn].order = current_order
                    self.blocks[pfn].size = 1 << current_order
                
                self.blocks[pfn].is_free = False
                if slab_owner:
                    self.blocks[pfn].slab_type = slab_owner
                    self.log(f"[Buddy] Assigned PFN {pfn} to Slab Cache '{slab_owner}'")
                else:
                    self.log(f"[Buddy] Allocated PFN {pfn}")
                    
                return self.blocks[pfn]
            
            current_order += 1
            
        self.log("[Buddy] Allocation Failed: No suitable block found.")
        return None

    def deallocate(self, pfn):
        if pfn not in self.blocks or self.blocks[pfn].is_free:
            self.log(f"[Buddy] Error: Invalid deallocation for PFN {pfn}")
            return False
            
        block = self.blocks[pfn]
        block.is_free = True
        block.slab_type = None
        current_order = block.order
        
        self.log(f"[Buddy] Freed PFN {pfn} (Order {current_order}). Merging...")
        
        while current_order < self.max_order:
            buddy_pfn = pfn ^ (1 << current_order)
            # self.log(f"[Buddy] Checking Buddy: {pfn} XOR (1<<{current_order}) = {buddy_pfn}")
            
            if (buddy_pfn in self.blocks and 
                self.blocks[buddy_pfn].is_free and 
                self.blocks[buddy_pfn].order == current_order):
                
                self.log(f"[Buddy] Merging PFN {pfn} + Buddy {buddy_pfn} -> Order {current_order+1}")
                self.free_area[current_order].remove(buddy_pfn)
                del self.blocks[buddy_pfn]
                del self.blocks[pfn]
                
                new_pfn = pfn & buddy_pfn
                current_order += 1
                pfn = new_pfn
                
                new_block = Block(pfn, current_order, True)
                self.blocks[pfn] = new_block
            else:
                break
        
        self.free_area[current_order].append(pfn)
        self.blocks[pfn].order = current_order
        self.blocks[pfn].size = 1 << current_order
        self.blocks[pfn].is_free = True
        
        return True

# --- Slab Allocator Logic ---

class Slab:
    def __init__(self, pfn, size, object_size):
        self.pfn = pfn
        self.size = size # Page size (from Buddy)
        self.object_size = object_size
        self.capacity = size // object_size
        self.objects = [True] * self.capacity # True = Free, False = Used
        self.used_count = 0
    
    def allocate(self):
        if self.used_count >= self.capacity:
            return None
        
        for i, is_free in enumerate(self.objects):
            if is_free:
                self.objects[i] = False
                self.used_count += 1
                # Return logical address: PFN + offset
                return self.pfn + (i * self.object_size)
        return None

    def deallocate(self, address):
        offset = address - self.pfn
        if offset < 0 or offset >= self.size:
            return False
        
        index = offset // self.object_size
        if not self.objects[index]: # If used
            self.objects[index] = True
            self.used_count -= 1
            return True
        return False
    
    def is_empty(self):
        return self.used_count == 0

    def to_dict(self):
        return {
            "pfn": self.pfn,
            "total_slots": self.capacity,
            "used_slots": self.used_count,
            "slots": self.objects # Array of booleans
        }

class SlabCache:
    def __init__(self, name, object_size, buddy_allocator):
        self.name = name
        self.object_size = object_size
        self.buddy = buddy_allocator
        self.slabs = [] # List of Slab objects
        # We assume 1 Page (Order 0, size 1 here is too small? Let's say Order 4 = 16 units is a page)
        # For this demo, let's say a "Page" requested from Buddy is dynamically determined.
        # Let's say we request a block big enough to hold at least 4 objects.
        self.page_order = max(0, (object_size * 4 - 1).bit_length()) 
        # Or simpler: Fixed "Page" size? 
        # Let's stick to requesting a block of size e.g. 32 (Order 5) for smaller objects.
        self.page_order = 5 # Size 32
        if object_size > 16: self.page_order = (object_size * 2 - 1).bit_length() # At least 2 objects

    def allocate(self):
        # 1. Try to allocate from existing slabs
        for slab in self.slabs:
            addr = slab.allocate()
            if addr is not None:
                self.buddy.log(f"[Slab-{self.name}] Allocated object at {addr} (in Slab PFN {slab.pfn})")
                return addr
        
        # 2. No space? Request new page from Buddy
        self.buddy.log(f"[Slab-{self.name}] Cache full. Requesting new Page (Order {self.page_order}) from Buddy...")
        buddy_block = self.buddy.allocate(1 << self.page_order, slab_owner=self.name)
        
        if buddy_block:
            new_slab = Slab(buddy_block.pfn, buddy_block.size, self.object_size)
            self.slabs.append(new_slab)
            addr = new_slab.allocate()
            self.buddy.log(f"[Slab-{self.name}] Created new Slab at {buddy_block.pfn}. Allocated object at {addr}.")
            return addr
        else:
            self.buddy.log(f"[Slab-{self.name}] Failed to expand cache. Buddy Allocator OOM.")
            return None

    def deallocate(self, address):
        for slab in self.slabs:
            if slab.pfn <= address < slab.pfn + slab.size:
                if slab.deallocate(address):
                    self.buddy.log(f"[Slab-{self.name}] Freed object at {address}.")
                    
                    # Check if slab is empty and return to buddy?
                    if slab.is_empty():
                        self.buddy.log(f"[Slab-{self.name}] Slab at {slab.pfn} is empty. Returning to Buddy.")
                        self.slabs.remove(slab)
                        self.buddy.deallocate(slab.pfn)
                    return True
        return False

    def to_dict(self):
        return {
            "name": self.name,
            "object_size": self.object_size,
            "slabs": [s.to_dict() for s in self.slabs]
        }

# Initialize global allocator and default caches
allocator = BuddyAllocator(TOTAL_MEMORY)
slab_caches = {
    "task_struct": SlabCache("task_struct", 4, allocator), # Small objs
    "inode": SlabCache("inode", 8, allocator)      # Medium objs
}

# --- Web Server ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Linux Memory Subsystem Visualization</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        h1, h2 { color: #333; margin: 5px 0; }
        .subtitle { color: #666; font-size: 0.9em; margin-bottom: 20px; }
        
        .main-layout { display: flex; gap: 20px; align-items: flex-start; width: 100%; max-width: 1200px; justify-content: center; }
        .left-panel { display: flex; flex-direction: column; gap: 20px; flex: 1; }
        .right-panel { width: 350px; flex-shrink: 0;}
        
        .section-box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        
        .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 15px; flex-wrap: wrap; }
        input { padding: 8px; border: 1px solid #ccc; border-radius: 4px; width: 60px; }
        button { padding: 8px 12px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; color: white; transition: background 0.3s; font-size: 12px; }
        .btn-alloc { background-color: #007bff; }
        .btn-alloc:hover { background-color: #0056b3; }
        .btn-free { background-color: #dc3545; }
        .btn-reset { background-color: #6c757d; }
        .btn-slab { background-color: #28a745; }
        .btn-slab:hover { background-color: #218838; }

        #memory-container { position: relative; width: 100%; height: 80px; background: #ddd; border: 2px solid #333; border-radius: 4px; overflow: hidden; display: flex; }
        .block { height: 100%; box-sizing: border-box; border-right: 1px solid rgba(0,0,0,0.1); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 10px; color: #333; transition: all 0.3s ease; cursor: pointer; flex-shrink: 0; position: relative; overflow: hidden; }
        .block:hover { opacity: 0.9; }
        .free { background: #90EE90; }
        .allocated { background: #FF7F7F; }
        .slab-owned { background: #FFD700; border-bottom: 3px solid #d35400; } /* Gold */
        
        .slab-container { display: flex; flex-direction: column; gap: 10px; }
        .slab-cache { border: 1px solid #ccc; padding: 10px; border-radius: 4px; background: #fafafa; }
        .slab-header { font-weight: bold; font-size: 14px; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center; }
        .slab-pages { display: flex; gap: 10px; flex-wrap: wrap; }
        .slab-page { border: 1px solid #999; padding: 2px; background: white; width: 140px; }
        .slab-page-info { font-size: 10px; text-align: center; color: #666; margin-bottom: 2px; }
        .slab-slots { display: flex; flex-wrap: wrap; gap: 1px; }
        .slot { width: 12px; height: 12px; background: #90EE90; border: 0.5px solid #ccc; }
        .slot.used { background: #28a745; }
        
        #log-panel { background: #1e1e1e; color: #00ff00; padding: 15px; border-radius: 8px; font-family: 'Consolas', 'Courier New', monospace; height: 600px; overflow-y: auto; font-size: 12px; border: 1px solid #333; }
        .log-entry { margin-bottom: 4px; border-bottom: 1px solid #333; padding-bottom: 2px; line-height: 1.4; word-wrap: break-word; }
        .log-buddy { color: #aaa; }
        .log-slab { color: #FFD700; }
        .highlight { color: #ffff00; font-weight: bold; }
    </style>
</head>
<body>

    <h1>Linux Memory Subsystem</h1>
    <div class="subtitle">Interactions between Buddy System (Pages) and Slab Allocator (Objects)</div>

    <div class="main-layout">
        <div class="left-panel">
            <!-- Buddy System Section -->
            <div class="section-box">
                <h2>1. Buddy System (Page Allocator)</h2>
                <div class="controls">
                    <input type="number" id="buddy-size" placeholder="Size" onkeypress="if(event.key==='Enter') allocBuddy()">
                    <button class="btn-alloc" onclick="allocBuddy()">Alloc Page</button>
                    <button class="btn-reset" onclick="resetAll()">Reset System</button>
                </div>
                <div id="memory-container"></div>
                <div style="font-size: 11px; margin-top:5px; color:#666">Click Red blocks to free pages. Gold blocks are owned by Slab (Free via Slab controls).</div>
            </div>

            <!-- Slab Allocator Section -->
            <div class="section-box">
                <h2>2. Slab Allocator (Object Cache)</h2>
                <div class="controls">
                    <span><b>task_struct</b> (Size 4):</span>
                    <button class="btn-slab" onclick="allocSlab('task_struct')">+ Alloc Object</button>
                    <span><b>inode</b> (Size 8):</span>
                    <button class="btn-slab" onclick="allocSlab('inode')">+ Alloc Object</button>
                </div>
                <div id="slab-container" class="slab-container"></div>
            </div>
        </div>

        <div class="right-panel">
            <h3>Kernel Event Log</h3>
            <div id="log-panel"></div>
        </div>
    </div>

    <script>
        const TOTAL_MEMORY = 512;
        
        function appendLog(message) {
            const panel = document.getElementById('log-panel');
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            
            if(message.includes('[Slab')) entry.classList.add('log-slab');
            else if(message.includes('[Buddy')) entry.classList.add('log-buddy');
            
            entry.innerText = message; // Simple text
            panel.appendChild(entry);
            panel.scrollTop = panel.scrollHeight;
        }

        async function fetchState() {
            // Get Buddy State
            const respBuddy = await fetch('/state');
            const buddyBlocks = await respBuddy.json();
            renderBuddy(buddyBlocks);
            
            // Get Slab State
            const respSlab = await fetch('/slab_state');
            const slabCaches = await respSlab.json();
            renderSlabs(slabCaches);
        }

        function renderBuddy(blocks) {
            const container = document.getElementById('memory-container');
            container.innerHTML = '';
            
            blocks.forEach(block => {
                const div = document.createElement('div');
                let className = 'block ';
                if (block.is_free) className += 'free';
                else if (block.slab_type) className += 'slab-owned';
                else className += 'allocated';
                
                div.className = className;
                div.style.width = `${(block.size / TOTAL_MEMORY) * 100}%`;
                
                div.innerHTML = `<span>${block.size}</span>`;
                div.title = `PFN: ${block.address}, Order: ${block.order}\\n${block.slab_type ? 'Owned by: '+block.slab_type : ''}`;
                
                // Only allow direct free if not slab owned (conceptually)
                if (!block.is_free && !block.slab_type) {
                    div.onclick = () => deallocBuddy(block.address);
                }
                
                container.appendChild(div);
            });
        }

        function renderSlabs(caches) {
            const container = document.getElementById('slab-container');
            container.innerHTML = '';
            
            for (const [name, cache] of Object.entries(caches)) {
                const div = document.createElement('div');
                div.className = 'slab-cache';
                
                let pagesHtml = '';
                cache.slabs.forEach(slab => {
                    let slotsHtml = '';
                    slab.slots.forEach((isFree, index) => {
                        const addr = slab.pfn + index * cache.object_size;
                        const click = isFree ? '' : `onclick="deallocBuddy(${addr})" style="cursor:pointer" title="Free Object ${addr}"`;
                        slotsHtml += `<div class="slot ${isFree ? '' : 'used'}" ${click}></div>`;
                    });
                    
                    pagesHtml += `
                        <div class="slab-page">
                            <div class="slab-page-info">Page PFN ${slab.pfn}</div>
                            <div class="slab-slots">${slotsHtml}</div>
                        </div>
                    `;
                });
                
                if (cache.slabs.length === 0) pagesHtml = '<div style="font-size:11px; color:#999; margin:5px;">No Active Pages</div>';
                
                div.innerHTML = `
                    <div class="slab-header">
                        <span>Cache: ${name} (Obj Size: ${cache.object_size})</span>
                    </div>
                    <div class="slab-pages">${pagesHtml}</div>
                `;
                container.appendChild(div);
            }
        }

        async function allocBuddy() {
            const size = parseInt(document.getElementById('buddy-size').value);
            if (!size) return;
            const res = await fetch('/allocate', { method: 'POST', body: JSON.stringify({ size }) });
            handleResponse(await res.json());
        }

        async function deallocBuddy(addr) {
            const res = await fetch('/deallocate', { method: 'POST', body: JSON.stringify({ address: addr }) });
            handleResponse(await res.json());
        }
        
        async function allocSlab(name) {
            const res = await fetch('/slab_allocate', { method: 'POST', body: JSON.stringify({ name }) });
            handleResponse(await res.json());
        }

        async function resetAll() {
             const res = await fetch('/reset', { method: 'POST', body: JSON.stringify({}) });
             document.getElementById('log-panel').innerHTML = '';
             handleResponse(await res.json());
        }

        function handleResponse(data) {
            if (data.logs) data.logs.forEach(l => appendLog(l));
            if (data.success) fetchState();
            else if (data.message) alert(data.message);
        }

        // Initial load
        fetchState();
        appendLog("[System] Kernel initialized.");
    </script>
</body>
</html>
"""

class BuddyRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif self.path == '/state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            state = [b.to_dict() for b in allocator.get_blocks_sorted()]
            self.wfile.write(json.dumps(state).encode('utf-8'))
        elif self.path == '/slab_state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            state = {name: c.to_dict() for name, c in slab_caches.items()}
            self.wfile.write(json.dumps(state).encode('utf-8'))
        else:
            self.send_error(404)

    def do_POST(self):
        global allocator, slab_caches
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 0:
                data = json.loads(self.rfile.read(length).decode('utf-8'))
            else:
                data = {}
        except:
             data = {}

        response = {"success": False, "logs": []}
        
        # Clear logs before op (except for shared logs issue, but single threaded here so ok)
        allocator.logs = [] 
        
        if self.path == '/allocate':
            size = data.get('size')
            block = allocator.allocate(size)
            response["success"] = (block is not None)
            response["logs"] = allocator.logs
        
        elif self.path == '/deallocate':
            addr = data.get('address')
            # Try Buddy First
            if allocator.deallocate(addr):
                response["success"] = True
            else:
                # Try Slabs
                found = False
                for cache in slab_caches.values():
                    if cache.deallocate(addr):
                        found = True
                        break
                response["success"] = found
                if not found:
                     response["message"] = "Address not found in Buddy or Slab caches"
            
            response["logs"] = allocator.logs

        elif self.path == '/slab_allocate':
            name = data.get('name')
            if name in slab_caches:
                addr = slab_caches[name].allocate()
                response["success"] = (addr is not None)
                response["logs"] = allocator.logs # Contains both slab and buddy logs
            else:
                 response["message"] = "Unknown cache"

        elif self.path == '/reset':
            allocator = BuddyAllocator(TOTAL_MEMORY)
            slab_caches = {
                "task_struct": SlabCache("task_struct", 4, allocator),
                "inode": SlabCache("inode", 8, allocator)
            }
            response["success"] = True
            response["logs"] = ["System Reset."]

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    print(f"Starting Linux Memory Subsystem Visualization on port {PORT}...")
    webbrowser.open(f'http://localhost:{PORT}')
    with socketserver.TCPServer(("", PORT), BuddyRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
