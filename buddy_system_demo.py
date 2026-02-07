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

    def to_dict(self):
        return {
            "address": self.pfn,
            "size": self.size,
            "order": self.order,
            "is_free": self.is_free
        }

class BuddyAllocator:
    def __init__(self, total_size):
        self.total_size = total_size
        self.max_order = int(math.log2(total_size))
        
        # Free areas: List of free PFNs for each order
        self.free_area = {i: [] for i in range(self.max_order + 1)}
        
        # PFN Map: Tracks the state of every possible starting PFN
        # We only really need to track valid block starts, but a dict is easiest.
        self.blocks = {} 
        
        # Initial state: One huge block of max_order
        initial_block = Block(0, self.max_order, True)
        self.free_area[self.max_order].append(0)
        self.blocks[0] = initial_block
        
        self.logs = []

    def log(self, message):
        self.logs.append(message)

    def get_blocks_sorted(self):
        # Return a sorted list of current blocks for visualization
        # We need to traverse the memory to output a linear view
        sorted_blocks = []
        current_pfn = 0
        while current_pfn < self.total_size:
            if current_pfn in self.blocks:
                block = self.blocks[current_pfn]
                sorted_blocks.append(block)
                current_pfn += block.size
            else:
                # Should not happen in a correct system, but for safety:
                current_pfn += 1 
        return sorted_blocks

    def allocate(self, size):
        self.logs = [] # Clear logs for this operation
        
        if size <= 0: return None
        
        # Calculate required order
        # If size=1 -> order 0. size=2 -> order 1. size=3 -> order 2 (4).
        req_order = 0
        if size > 1:
            req_order = (size - 1).bit_length()
        
        self.log(f"Request: {size} units -> Need Order {req_order} (Size {1<<req_order})")
        
        # Find smallest available order >= req_order
        current_order = req_order
        while current_order <= self.max_order:
            if self.free_area[current_order]:
                # Found a free block!
                pfn = self.free_area[current_order].pop(0)
                self.log(f"Found free block at PFN {pfn} (Order {current_order})")
                
                # Split down to req_order
                while current_order > req_order:
                    current_order -= 1
                    buddy_pfn = pfn + (1 << current_order)
                    self.log(f"Splitting PFN {pfn} (Order {current_order+1}) into PFN {pfn} and Buddy {buddy_pfn} (Order {current_order})")
                    
                    # Create the buddy block and add to free area
                    buddy_block = Block(buddy_pfn, current_order, True)
                    self.blocks[buddy_pfn] = buddy_block
                    self.free_area[current_order].append(buddy_pfn)
                    
                    # Update current block order
                    self.blocks[pfn].order = current_order
                    self.blocks[pfn].size = 1 << current_order
                
                # Mark as allocated
                self.blocks[pfn].is_free = False
                self.log(f"Allocated PFN {pfn} (Order {req_order})")
                return self.blocks[pfn]
            
            current_order += 1
            
        self.log("Allocation Failed: No suitable block found.")
        return None

    def deallocate(self, pfn):
        self.logs = [] # Clear logs
        
        if pfn not in self.blocks or self.blocks[pfn].is_free:
            self.log(f"Error: Invalid deallocation request for PFN {pfn}")
            return False
            
        block = self.blocks[pfn]
        block.is_free = True
        current_order = block.order
        
        self.log(f"Freed PFN {pfn} (Order {current_order}). Starting merge check...")
        
        while current_order < self.max_order:
            # XOR Magic: Calculate buddy PFN
            buddy_pfn = pfn ^ (1 << current_order)
            self.log(f"Checking Buddy: {pfn} XOR (1<<{current_order}) = <span class='highlight'>{buddy_pfn}</span>")
            
            # Check if buddy is valid and free and has same order
            if (buddy_pfn in self.blocks and 
                self.blocks[buddy_pfn].is_free and 
                self.blocks[buddy_pfn].order == current_order):
                
                self.log(f"Buddy {buddy_pfn} is FREE and Order {current_order}. <span class='success'>Merging!</span>")
                
                # Remove buddy from free area
                self.free_area[current_order].remove(buddy_pfn)
                
                # Remove buddy block object (conceptually merged into the lower-address one)
                del self.blocks[buddy_pfn]
                del self.blocks[pfn] # Temporarily remove to re-insert merged one
                
                # New PFN is the smaller of the two (bitwise AND allows this too for aligned blocks)
                new_pfn = pfn & buddy_pfn
                
                # Order increases
                current_order += 1
                
                # Update PFN for next iteration
                pfn = new_pfn
                
                # Create merged block
                new_block = Block(pfn, current_order, True)
                self.blocks[pfn] = new_block
                
                self.log(f"Merged into NEW Block PFN {pfn} (Order {current_order})")
                
            else:
                self.log(f"Buddy {buddy_pfn} is NOT free or wrong order. <span class='warning'>Cannot merge.</span>")
                break
        
        # Add final free block to free area
        self.free_area[current_order].append(pfn)
        # Ensure block state is consistent
        self.blocks[pfn].order = current_order
        self.blocks[pfn].size = 1 << current_order
        self.blocks[pfn].is_free = True # Should already be true
        
        return True

# Initialize global allocator
allocator = BuddyAllocator(TOTAL_MEMORY)

# --- Web Server ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Buddy System Visualization (XOR Logic)</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        h1 { color: #333; margin-bottom: 5px; }
        .subtitle { color: #666; font-size: 0.9em; margin-bottom: 20px; }
        
        .main-layout { display: flex; gap: 20px; align-items: flex-start; }
        
        .left-panel { display: flex; flex-direction: column; gap: 20px; }
        .right-panel { width: 300px; }
        
        .controls { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); display: flex; gap: 10px; align-items: center; }
        input { padding: 8px; border: 1px solid #ccc; border-radius: 4px; width: 80px; }
        button { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; color: white; transition: background 0.3s; }
        .btn-alloc { background-color: #007bff; }
        .btn-alloc:hover { background-color: #0056b3; }
        .btn-reset { background-color: #dc3545; }
        .btn-reset:hover { background-color: #a71d2a; }
        
        #memory-container { position: relative; width: 800px; height: 120px; background: #ddd; border: 2px solid #333; border-radius: 4px; overflow: hidden; display: flex; }
        .block { height: 100%; box-sizing: border-box; border-right: 1px solid rgba(0,0,0,0.2); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 11px; color: #333; transition: all 0.3s ease; cursor: pointer; flex-shrink: 0; }
        .block:hover { opacity: 0.9; }
        .free { background: #90EE90; }
        .allocated { background: #FF7F7F; }
        
        #log-panel { background: #1e1e1e; color: #00ff00; padding: 15px; border-radius: 8px; font-family: 'Consolas', 'Courier New', monospace; height: 400px; overflow-y: auto; font-size: 13px; border: 1px solid #333; }
        .log-entry { margin-bottom: 5px; border-bottom: 1px solid #333; padding-bottom: 2px; }
        .highlight { color: #ffff00; font-weight: bold; }
        .success { color: #00ff00; font-weight: bold; }
        .warning { color: #ff9900; }
        
        .legend { margin-top: 10px; font-size: 0.9em; color: #666; text-align: center; }
    </style>
</head>
<body>

    <h1>Buddy System Visualization</h1>
    <div class="subtitle">Strict Linux Kernel Implementation (Orders & XOR Buddy Finding)</div>

    <div class="main-layout">
        <div class="left-panel">
            <div class="controls">
                <input type="number" id="size-input" placeholder="Size" onkeypress="handleKeyPress(event)">
                <button class="btn-alloc" onclick="allocate()">Allocate</button>
                <button class="btn-reset" onclick="resetMemory()">Reset</button>
            </div>

            <div id="memory-container"></div>
            <div class="legend">
                Blocks show: <b>[Size] (Order)</b><br>
                Click Red blocks to Deallocate & Watch Merges!
            </div>
        </div>

        <div class="right-panel">
            <h3>Event Log (XOR Logic)</h3>
            <div id="log-panel"></div>
        </div>
    </div>

    <script>
        const TOTAL_MEMORY = 512;
        
        function handleKeyPress(event) {
            if (event.key === 'Enter') allocate();
        }

        function appendLog(message) {
            const panel = document.getElementById('log-panel');
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `> ${message}`;
            panel.appendChild(entry);
            panel.scrollTop = panel.scrollHeight;
        }

        async function fetchState() {
            const response = await fetch('/state');
            const blocks = await response.json();
            render(blocks);
        }

        function render(blocks) {
            const container = document.getElementById('memory-container');
            container.innerHTML = '';
            
            blocks.forEach(block => {
                const div = document.createElement('div');
                div.className = `block ${block.is_free ? 'free' : 'allocated'}`;
                const widthPercent = (block.size / TOTAL_MEMORY) * 100;
                div.style.width = `${widthPercent}%`;
                
                div.innerHTML = `<span>Size:${block.size}</span><span>(Ord:${block.order})</span>`;
                div.title = `PFN: ${block.address}, Order: ${block.order}`;
                
                if (!block.is_free) {
                    div.onclick = () => deallocate(block.address);
                }
                
                container.appendChild(div);
            });
        }

        async function allocate() {
            const sizeInput = document.getElementById('size-input');
            const size = parseInt(sizeInput.value);
            if (!size || size <= 0) {
                alert("Please enter a valid positive number.");
                return;
            }

            const response = await fetch('/allocate', {
                method: 'POST',
                body: JSON.stringify({ size: size })
            });
            const result = await response.json();
            
            if (result.logs) {
                result.logs.forEach(log => appendLog(log));
            }
            
            if (result.success) {
                fetchState();
            } else {
                appendLog("<span class='warning'>Allocation Failed</span>");
            }
            sizeInput.value = '';
            sizeInput.focus();
        }

        async function deallocate(address) {
            const response = await fetch('/deallocate', {
                method: 'POST',
                body: JSON.stringify({ address: address })
            });
            const result = await response.json();
            
            if (result.logs) {
                result.logs.forEach(log => appendLog(log));
            }

            if (result.success) {
                fetchState();
            } else {
                alert("Deallocation failed.");
            }
        }

        async function resetMemory() {
            await fetch('/reset', {
                method: 'POST',
                body: JSON.stringify({})
            });
            document.getElementById('log-panel').innerHTML = ''; // Clear logs
            appendLog("System Reset. Memory Cleared.");
            fetchState();
        }

        // Initial load
        fetchState();
        appendLog("System Ready. Total Memory: " + TOTAL_MEMORY);
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
        else:
            self.send_error(404)

    def do_POST(self):
        global allocator
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            content_length = 0
            
        if content_length > 0:
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
        else:
            data = {}
        
        response = {"success": False, "logs": []}
        
        if self.path == '/allocate':
            size = data.get('size')
            block = allocator.allocate(size)
            response["logs"] = allocator.logs
            if block:
                response["success"] = True
            else:
                response["message"] = "No suitable block found"
        
        elif self.path == '/deallocate':
            address = data.get('address')
            if allocator.deallocate(address):
                response["success"] = True
            response["logs"] = allocator.logs
        
        elif self.path == '/reset':
            allocator = BuddyAllocator(TOTAL_MEMORY)
            response["success"] = True
            response["logs"] = ["Memory Reset."]

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def log_message(self, format, *args):
        return # Suppress logging

if __name__ == "__main__":
    print(f"Starting Enhanced Buddy System Web Server on port {PORT}...")
    print(f"Open http://localhost:{PORT} in your browser.")
    
    webbrowser.open(f'http://localhost:{PORT}')
    
    with socketserver.TCPServer(("", PORT), BuddyRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")
            httpd.server_close()
