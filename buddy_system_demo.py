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

# --- Legacy Slab Allocator Logic (Old Linux) ---

class LegacySlabPage:
    def __init__(self, pfn, size, object_size):
        self.pfn = pfn
        self.size = size
        self.object_size = object_size
        
        # Simulate Metadata Overhead: Reserve first 20% of page for metadata array
        # This is a simplification. Real Slab has a struct slab_head at the end or start.
        self.metadata_size = max(1, size // 5) 
        self.available_space = size - self.metadata_size
        self.capacity = self.available_space // object_size
        
        # Simple boolean array
        self.objects = [True] * self.capacity # True = Free
        self.used_count = 0
    
    def allocate(self):
        if self.used_count >= self.capacity:
            return None
        
        for i, is_free in enumerate(self.objects):
            if is_free:
                self.objects[i] = False
                self.used_count += 1
                # Return logical address (offset by metadata)
                return self.pfn + self.metadata_size + (i * self.object_size)
        return None

    def deallocate(self, address):
        # Check if address falls in the object area of this page
        start_addr = self.pfn + self.metadata_size
        end_addr = start_addr + (self.capacity * self.object_size)
        
        if not (start_addr <= address < end_addr):
            return False
            
        offset = address - start_addr
        if offset % self.object_size != 0:
            return False # Invalid alignment?
            
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
            "metadata_size": self.metadata_size,
            "type": "legacy",
            "slots": self.objects 
        }

class LegacySlabCache:
    def __init__(self, name, object_size, buddy_allocator):
        self.name = name
        self.object_size = object_size
        self.buddy = buddy_allocator
        self.pages = []
        self.page_order = 5 

    def allocate(self):
        for page in self.pages:
            addr = page.allocate()
            if addr is not None:
                self.buddy.log(f"[LegacySlab-{self.name}] Alloc Obj at {addr}. (Metadata Check: OK)")
                return addr
        
        self.buddy.log(f"[LegacySlab-{self.name}] Full. Req Page (Order {self.page_order}).")
        buddy_block = self.buddy.allocate(1 << self.page_order, slab_owner=f"Legacy {self.name}")
        
        if buddy_block:
            new_page = LegacySlabPage(buddy_block.pfn, buddy_block.size, self.object_size)
            self.pages.append(new_page)
            addr = new_page.allocate()
            self.buddy.log(f"[LegacySlab-{self.name}] New Page {buddy_block.pfn}. Header overhead: {new_page.metadata_size}. Alloc {addr}")
            return addr
        return None

    def deallocate(self, address):
        for page in self.pages:
             # Check if address is within this page bounds
             if page.pfn <= address < page.pfn + page.size:
                if page.deallocate(address):
                    self.buddy.log(f"[LegacySlab-{self.name}] Freed {address}.")
                    if page.is_empty():
                        self.buddy.log(f"[LegacySlab-{self.name}] Page {page.pfn} empty -> Buddy.")
                        self.pages.remove(page)
                        self.buddy.deallocate(page.pfn)
                    return True
        return False
        
    def to_dict(self):
        return {
            "name": self.name,
            "object_size": self.object_size,
            "pages": [p.to_dict() for p in self.pages]
        }


# --- Slub Allocator Logic (Modern Linux) ---

class SlubPage:
    def __init__(self, pfn, size, object_size):
        self.pfn = pfn
        self.size = size 
        self.object_size = object_size
        self.capacity = size // object_size
        
        self.free_head = 0
        self.objects = []
        for i in range(self.capacity):
            next_idx = i + 1 if i < self.capacity - 1 else -1
            self.objects.append(next_idx) 
            
        self.used_count = 0
    
    def allocate(self):
        if self.free_head == -1: return None
        obj_idx = self.free_head
        self.free_head = self.objects[obj_idx]
        self.objects[obj_idx] = -2 # Used
        self.used_count += 1
        return self.pfn + (obj_idx * self.object_size)

    def deallocate(self, address):
        offset = address - self.pfn
        if offset < 0 or offset >= self.size: return False
        index = offset // self.object_size
        if self.objects[index] != -2: return False 
        
        old_head = self.free_head
        self.objects[index] = old_head
        self.free_head = index
        self.used_count -= 1
        return True
    
    def is_empty(self):
        return self.used_count == 0

    def to_dict(self):
        return {
            "pfn": self.pfn,
            "total_slots": self.capacity,
            "used_slots": self.used_count,
            "free_head": self.free_head,
            "type": "slub",
            "slots": self.objects 
        }

class SlubCache:
    def __init__(self, name, object_size, buddy_allocator):
        self.name = name
        self.object_size = object_size
        self.buddy = buddy_allocator
        self.pages = [] 
        self.page_order = 5 

    def allocate(self):
        for page in self.pages:
            addr = page.allocate()
            if addr is not None:
                self.buddy.log(f"[Slub-{self.name}] Alloc Obj at {addr} (Ptr Update: Head->{page.free_head})")
                return addr
        
        self.buddy.log(f"[Slub-{self.name}] Full. Req Page (Order {self.page_order}).")
        buddy_block = self.buddy.allocate(1 << self.page_order, slab_owner=f"Slub {self.name}")
        
        if buddy_block:
            new_page = SlubPage(buddy_block.pfn, buddy_block.size, self.object_size)
            self.pages.append(new_page)
            addr = new_page.allocate()
            self.buddy.log(f"[Slub-{self.name}] New Page {buddy_block.pfn}. Alloc {addr}.")
            return addr
        return None

    def deallocate(self, address):
        for page in self.pages:
            if page.pfn <= address < page.pfn + page.size:
                if page.deallocate(address):
                    self.buddy.log(f"[Slub-{self.name}] Freed {address}.")
                    if page.is_empty():
                        self.buddy.log(f"[Slub-{self.name}] Page {page.pfn} empty -> Buddy.")
                        self.pages.remove(page)
                        self.buddy.deallocate(page.pfn)
                    return True
        return False

    def to_dict(self):
        return {
            "name": self.name,
            "object_size": self.object_size,
            "pages": [p.to_dict() for p in self.pages]
        }

# Initialize 
allocator = BuddyAllocator(TOTAL_MEMORY)

legacy_caches = {
    "task_struct": LegacySlabCache("task_struct", 4, allocator),
}

slub_caches = {
    "task_struct": SlubCache("task_struct", 4, allocator),
}

# --- Web Server ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Slab vs Slub Comparison</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        h1, h2, h3 { color: #333; margin: 5px 0; }
        .subtitle { color: #666; font-size: 0.9em; margin-bottom: 20px; }
        
        .main-layout { display: flex; gap: 20px; align-items: flex-start; width: 100%; max-width: 1400px; justify-content: center; }
        .left-panel { display: flex; flex-direction: column; gap: 20px; flex: 1; }
        .right-panel { width: 300px; flex-shrink: 0;}
        
        .section-box { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .comparison-layout { display: flex; gap: 20px; }
        .comp-col { flex: 1; }
        
        .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
        input { padding: 8px; border: 1px solid #ccc; border-radius: 4px; width: 60px; }
        button { padding: 6px 12px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; color: white; transition: background 0.3s; font-size: 11px; }
        .btn-alloc { background-color: #007bff; }
        .btn-alloc:hover { background-color: #0056b3; }
        .btn-reset { background-color: #6c757d; }
        .btn-legacy { background-color: #d35400; }
        .btn-legacy:hover { background-color: #a04000; }
        .btn-slub { background-color: #28a745; }
        .btn-slub:hover { background-color: #218838; }

        #memory-container { position: relative; width: 100%; height: 60px; background: #ddd; border: 2px solid #333; border-radius: 4px; overflow: hidden; display: flex; }
        .block { height: 100%; box-sizing: border-box; border-right: 1px solid rgba(0,0,0,0.1); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 10px; color: #333; transition: all 0.3s ease; cursor: pointer; flex-shrink: 0; position: relative; overflow: hidden; }
        .block:hover { opacity: 0.9; }
        .free { background: #90EE90; }
        .allocated { background: #FF7F7F; }
        .slab-owned { background: #FFD700; border-bottom: 3px solid #d35400; } /* Gold */
        
        .slab-container { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
        .slab-cache { border: 1px solid #ccc; padding: 10px; border-radius: 4px; background: #fafafa; }
        .slab-page { border: 1px solid #999; padding: 2px; background: white; margin-bottom: 5px; }
        .slab-page-info { font-size: 10px; text-align: center; color: #666; margin-bottom: 2px; }
        .slab-slots { display: flex; flex-wrap: wrap; gap: 1px; }
        
        .slot { height: 16px; min-width: 25px; background: #90EE90; border: 1px solid #ccc; font-size: 9px; display: flex; align-items: center; justify-content: center; color: #333; overflow: hidden; padding: 0 2px;}
        .slot.used { background: #dc3545; color: white; border-color: #a71d2a; }
        .slot.head { border: 2px solid #007bff; box-shadow: 0 0 3px #007bff; }
        .metadata-header { background: #e74c3c; color: white; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold;}
        
        #log-panel { background: #1e1e1e; color: #00ff00; padding: 15px; border-radius: 8px; font-family: 'Consolas', 'Courier New', monospace; height: 600px; overflow-y: auto; font-size: 11px; border: 1px solid #333; }
        .log-entry { margin-bottom: 4px; border-bottom: 1px solid #333; padding-bottom: 2px; line-height: 1.4; word-wrap: break-word; }
        .log-legacy { color: #e67e22; }
        .log-slub { color: #2ecc71; }
    </style>
</head>
<body>

    <h1>Legacy Slab vs Modern Slub</h1>
    <div class="subtitle">Direct Comparison of Memory Allocators</div>

    <div class="main-layout">
        <div class="left-panel">
            <!-- Buddy System Section -->
            <div class="section-box">
                <h2>1. Buddy System (Page Allocator)</h2>
                <div class="controls">
                    <input type="number" id="buddy-size" placeholder="Size" value="32" onkeypress="if(event.key==='Enter') allocBuddy()">
                    <button class="btn-alloc" onclick="allocBuddy()">Alloc Page</button>
                    <button class="btn-reset" onclick="resetAll()">Reset All</button>
                </div>
                <div id="memory-container"></div>
            </div>

            <!-- Comparison Section -->
            <div class="comparison-layout">
                <!-- Legacy Slab -->
                <div class="comp-col section-box">
                    <h3 style="color: #d35400;">2. Legacy Slab</h3>
                    <div style="font-size: 0.8em; color: #666;">
                        Features: <b>Metadata Overhead</b> (Red Header).<br>
                        Objects allocated after header. Separate queue management.
                    </div>
                    <div class="controls" style="margin-top: 10px;">
                        <span><b>task_struct</b> (Size 4):</span>
                        <button class="btn-legacy" onclick="allocLegacy('task_struct')">+ Alloc Legacy</button>
                    </div>
                    <div id="legacy-container" class="slab-container"></div>
                </div>

                <!-- Modern Slub -->
                <div class="comp-col section-box">
                    <h3 style="color: #218838;">3. Modern Slub</h3>
                    <div style="font-size: 0.8em; color: #666;">
                        Features: <b>Zero Overhead</b>.<br>
                        Embedded Free List (Green blocks point to next free).
                    </div>
                    <div class="controls" style="margin-top: 10px;">
                        <span><b>task_struct</b> (Size 4):</span>
                        <button class="btn-slub" onclick="allocSlub('task_struct')">+ Alloc Slub</button>
                    </div>
                    <div id="slub-container" class="slab-container"></div>
                </div>
            </div>
        </div>

        <div class="right-panel">
            <h3>Kernel Log</h3>
            <div id="log-panel"></div>
        </div>
    </div>

    <script>
        const TOTAL_MEMORY = 512;
        
        function appendLog(message) {
            const panel = document.getElementById('log-panel');
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            
            if(message.includes('[Legacy')) entry.classList.add('log-legacy');
            else if(message.includes('[Slub')) entry.classList.add('log-slub');
            
            entry.innerText = message; 
            panel.appendChild(entry);
            panel.scrollTop = panel.scrollHeight;
        }

        async function fetchState() {
            const respBuddy = await fetch('/state');
            renderBuddy(await respBuddy.json());
            
            const respLegacy = await fetch('/legacy_state');
            renderLegacy(await respLegacy.json());
            
            const respSlub = await fetch('/slub_state');
            renderSlub(await respSlub.json());
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
                if (!block.is_free && !block.slab_type) div.onclick = () => deallocBuddy(block.address);
                container.appendChild(div);
            });
        }

        function renderLegacy(caches) {
            const container = document.getElementById('legacy-container');
            container.innerHTML = '';
            for (const [name, cache] of Object.entries(caches)) {
                let pagesHtml = '';
                cache.pages.forEach(page => {
                    let slotsHtml = '';
                    // Metadata Block
                    slotsHtml += `<div class="metadata-header" style="width: ${page.metadata_size * 6}px;" title="Metadata Header (Overhead)">HDR</div>`;
                    
                    page.slots.forEach((isFree, index) => {
                         const addr = page.pfn + page.metadata_size + index * cache.object_size;
                         const click = isFree ? '' : `onclick="deallocBuddy(${addr})" style="cursor:pointer" title="Free Legacy Object"`;
                         slotsHtml += `<div class="slot ${isFree ? '' : 'used'}" ${click}>${isFree?'F':'U'}</div>`;
                    });
                    pagesHtml += `<div class="slab-page"><div class="slab-page-info">Page PFN ${page.pfn}</div><div class="slab-slots">${slotsHtml}</div></div>`;
                });
                container.innerHTML += `<div class="slab-cache"><div class="slab-header">${name}</div>${pagesHtml}</div>`;
            }
        }

        function renderSlub(caches) {
            const container = document.getElementById('slub-container');
            container.innerHTML = '';
            for (const [name, cache] of Object.entries(caches)) {
                let pagesHtml = '';
                cache.pages.forEach(page => {
                    let slotsHtml = '';
                    page.slots.forEach((val, index) => {
                        const addr = page.pfn + index * cache.object_size;
                        const isUsed = val === -2;
                        const isHead = index === page.free_head;
                        
                        let content = '';
                        if (isUsed) content = 'USED';
                        else if (val === -1) content = 'END';
                        else content = `→ ${val}`;
                        
                        const click = isUsed ? `onclick="deallocBuddy(${addr})" style="cursor:pointer" title="Free Slub Object"` : '';
                        slotsHtml += `<div class="slot ${isUsed ? 'used' : ''} ${isHead ? 'head' : ''}" ${click}>${content}</div>`;
                    });
                    pagesHtml += `<div class="slab-page"><div class="slab-page-info">Page PFN ${page.pfn} (Head: ${page.free_head})</div><div class="slab-slots">${slotsHtml}</div></div>`;
                });
                container.innerHTML += `<div class="slab-cache"><div class="slab-header">${name}</div>${pagesHtml}</div>`;
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
        
        async function allocLegacy(name) {
            const res = await fetch('/legacy_allocate', { method: 'POST', body: JSON.stringify({ name }) });
            handleResponse(await res.json());
        }

        async function allocSlub(name) {
            const res = await fetch('/slub_allocate', { method: 'POST', body: JSON.stringify({ name }) });
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

        fetchState();
        appendLog("[System] Kernel initialized. Comparison Mode.");
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
        elif self.path == '/legacy_state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            state = {name: c.to_dict() for name, c in legacy_caches.items()}
            self.wfile.write(json.dumps(state).encode('utf-8'))
        elif self.path == '/slub_state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            state = {name: c.to_dict() for name, c in slub_caches.items()}
            self.wfile.write(json.dumps(state).encode('utf-8'))
        else:
            self.send_error(404)

    def do_POST(self):
        global allocator, legacy_caches, slub_caches
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 0:
                data = json.loads(self.rfile.read(length).decode('utf-8'))
            else:
                data = {}
        except:
             data = {}

        response = {"success": False, "logs": []}
        allocator.logs = [] 
        
        if self.path == '/allocate':
            size = data.get('size')
            block = allocator.allocate(size)
            response["success"] = (block is not None)
            response["logs"] = allocator.logs
        
        elif self.path == '/deallocate':
            addr = data.get('address')
            if allocator.deallocate(addr):
                response["success"] = True
            else:
                found = False
                # Try Legacy
                for cache in legacy_caches.values():
                    if cache.deallocate(addr):
                        found = True
                        break
                # Try Slub
                if not found:
                    for cache in slub_caches.values():
                        if cache.deallocate(addr):
                            found = True
                            break
                            
                response["success"] = found
                if not found:
                     response["message"] = "Address not found"
            
            response["logs"] = allocator.logs

        elif self.path == '/legacy_allocate':
            name = data.get('name')
            if name in legacy_caches:
                addr = legacy_caches[name].allocate()
                response["success"] = (addr is not None)
                response["logs"] = allocator.logs
            else:
                 response["message"] = "Unknown legacy cache"

        elif self.path == '/slub_allocate':
            name = data.get('name')
            if name in slub_caches:
                addr = slub_caches[name].allocate()
                response["success"] = (addr is not None)
                response["logs"] = allocator.logs
            else:
                 response["message"] = "Unknown slub cache"

        elif self.path == '/reset':
            allocator = BuddyAllocator(TOTAL_MEMORY)
            legacy_caches = {"task_struct": LegacySlabCache("task_struct", 4, allocator)}
            slub_caches = {"task_struct": SlubCache("task_struct", 4, allocator)}
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
