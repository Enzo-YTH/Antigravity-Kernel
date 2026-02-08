import http.server
import socketserver
import json
import webbrowser
import os
import sys

PORT = 8001 # Use a different port to avoid conflict
OBJECT_SIZE = 8 # bytes
RED_ZONE_SIZE = 4
TOTAL_SIZE = RED_ZONE_SIZE + OBJECT_SIZE + RED_ZONE_SIZE

# Slub Debug Constants
SLUB_RED_ACTIVE = 0xcc
SLUB_RED_INACTIVE = 0xbb # Not used in this simple demo, we use ACTIVE for both sides usually
POISON_INUSE = 0x5a
POISON_FREE = 0x6b
POISON_END = 0xa5 # End of poison (not used here for simplicity)

class DebugObject:
    def __init__(self, addr):
        self.addr = addr
        self.is_allocated = False
        # Initialize memory with "Uninitialized" pattern or just zeros
        self.memory = [0] * TOTAL_SIZE 

    def allocate(self):
        self.is_allocated = True
        # 1. Fill Red Zones
        for i in range(RED_ZONE_SIZE):
            self.memory[i] = SLUB_RED_ACTIVE
            self.memory[TOTAL_SIZE - 1 - i] = SLUB_RED_ACTIVE
        
        # 2. Fill Data with Poison (In-Use)
        # Note: In real Linux, poison usually happens on FREE, 
        # but debug mode can also poison uninitialized allocs to catch Uninit Read.
        # Let's fill with POISON_INUSE to show the pattern.
        for i in range(OBJECT_SIZE):
            self.memory[RED_ZONE_SIZE + i] = POISON_INUSE

    def free(self):
        self.is_allocated = False
        # 1. Check Red Zones (Simulated Hardware/Kernel check)
        # In real life, this happens BEFORE modifying state.
        
        # 2. Fill Data with Poison (Free)
        for i in range(OBJECT_SIZE):
            self.memory[RED_ZONE_SIZE + i] = POISON_FREE

    def corrupt(self, index, value):
        if 0 <= index < TOTAL_SIZE:
            self.memory[index] = value
            return True
        return False

    def validate(self):
        errors = []
        
        # Check Left Red Zone
        for i in range(RED_ZONE_SIZE):
            if self.memory[i] != SLUB_RED_ACTIVE:
                errors.append(f"Redzone LEFT corrupted at byte {i}: Expected 0xcc, Got 0x{self.memory[i]:02x}")

        # Check Right Red Zone
        for i in range(RED_ZONE_SIZE):
            idx = TOTAL_SIZE - 1 - i
            if self.memory[idx] != SLUB_RED_ACTIVE:
                errors.append(f"Redzone RIGHT corrupted at byte {idx}: Expected 0xcc, Got 0x{self.memory[idx]:02x}")

        # Check Poison (If Freed)
        if not self.is_allocated:
            for i in range(OBJECT_SIZE):
                idx = RED_ZONE_SIZE + i
                if self.memory[idx] != POISON_FREE:
                    errors.append(f"Poison corrupted at byte {idx} (Use-After-Free?): Expected 0x6b, Got 0x{self.memory[idx]:02x}")

        return errors

    def to_dict(self):
        return {
            "addr": self.addr,
            "is_allocated": self.is_allocated,
            "memory": self.memory,
            "red_zone_size": RED_ZONE_SIZE,
            "object_size": OBJECT_SIZE
        }

# Global State
debug_object = DebugObject(0x1000)
debug_object.allocate() # Start allocated

# --- Web Server ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Slub Debug Visualization</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #222; color: #eee; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        h1 { color: #fff; margin-bottom: 5px; }
        .subtitle { color: #aaa; margin-bottom: 30px; font-size: 0.9em; }
        
        .memory-grid { display: flex; gap: 5px; margin-bottom: 20px; background: #333; padding: 20px; border-radius: 8px; border: 2px solid #555; }
        
        .byte-box { 
            width: 50px; height: 60px; 
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            border: 1px solid #000; font-family: 'Consolas', monospace; cursor: pointer;
            transition: transform 0.1s;
        }
        .byte-box:hover { transform: scale(1.1); z-index: 10; border-color: #fff; }
        
        .red-zone { background: #d63031; color: white; }
        .data-inuse { background: #00b894; color: black; }
        .data-free { background: #0984e3; color: white; } /* Poisoned */
        
        .label { font-size: 10px; margin-top: 5px; color: #ccc; }
        .value { font-size: 16px; font-weight: bold; }
        
        .controls { display: flex; gap: 15px; margin-bottom: 20px; }
        button { padding: 10px 20px; border: none; border-radius: 5px; font-weight: bold; cursor: pointer; transition: background 0.2s; }
        .btn-alloc { background: #00b894; color: black; }
        .btn-free { background: #0984e3; color: white; }
        .btn-validate { background: #e17055; color: white; }
        .btn-reset { background: #636e72; color: white; }
        
        #log-panel { width: 600px; height: 200px; background: #000; border: 1px solid #444; color: #0f0; font-family: monospace; padding: 10px; overflow-y: auto; font-size: 12px; }
        .log-error { color: #ff0000; font-weight: bold; }
        .log-info { color: #888; }
        
        .legend { display: flex; gap: 20px; margin-bottom: 10px; font-size: 12px; }
        .legend-item { display: flex; align-items: center; gap: 5px; }
        .box { width: 15px; height: 15px; border: 1px solid #fff; }
    </style>
</head>
<body>

    <h1>Slub Debug Visualization</h1>
    <div class="subtitle">Interactive Red Zoning & Poisoning Demo</div>

    <div class="legend">
        <div class="legend-item"><div class="box" style="background:#d63031"></div> Red Zone (0xcc)</div>
        <div class="legend-item"><div class="box" style="background:#00b894"></div> Data In-Use (0x5a)</div>
        <div class="legend-item"><div class="box" style="background:#0984e3"></div> Data Free/Poison (0x6b)</div>
    </div>

    <div id="memory-grid" class="memory-grid"></div>

    <div class="controls">
        <button class="btn-alloc" onclick="action('alloc')">Allocate Object</button>
        <button class="btn-free" onclick="action('free')">Free Object</button>
        <button class="btn-validate" onclick="action('validate')">Run Validation Check</button>
        <button class="btn-reset" onclick="action('reset')">Reset</button>
    </div>

    <div id="log-panel"></div>

    <script>
        function appendLog(message, isError=false) {
            const panel = document.getElementById('log-panel');
            const entry = document.createElement('div');
            entry.className = isError ? 'log-error' : 'log-info';
            entry.innerText = `> ${message}`;
            panel.appendChild(entry);
            panel.scrollTop = panel.scrollHeight;
        }

        async function fetchState() {
            const res = await fetch('/state');
            const data = await res.json();
            renderGrid(data);
        }

        function renderGrid(state) {
            const container = document.getElementById('memory-grid');
            container.innerHTML = '';
            
            state.memory.forEach((byteVal, index) => {
                const box = document.createElement('div');
                let type = '';
                let label = '';
                
                // Determine Section
                if (index < state.red_zone_size) {
                    type = 'red-zone';
                    label = 'RZ Left';
                } else if (index >= state.red_zone_size + state.object_size) {
                    type = 'red-zone';
                    label = 'RZ Right';
                } else {
                    type = state.is_allocated ? 'data-inuse' : 'data-free';
                    label = `Data ${index - state.red_zone_size}`;
                }
                
                box.className = `byte-box ${type}`;
                box.innerHTML = `
                    <div class="value">0x${byteVal.toString(16).padStart(2,'0')}</div>
                    <div class="label">${label}</div>
                `;
                
                box.onclick = () => corruptByte(index, byteVal);
                
                container.appendChild(box);
            });
        }

        async function corruptByte(index, currentVal) {
            const newValStr = prompt(`Corrupt Byte ${index}? Enter Hex (e.g., ff):`, currentVal.toString(16));
            if (newValStr === null) return;
            
            const newVal = parseInt(newValStr, 16);
            if (isNaN(newVal)) return;
            
            await fetch('/corrupt', {
                method: 'POST',
                body: JSON.stringify({ index, value: newVal })
            });
            appendLog(`User corrupted Byte ${index} -> 0x${newVal.toString(16).padStart(2,'0')}`);
            fetchState();
        }

        async function action(type) {
            const res = await fetch(`/${type}`, { method: 'POST' });
            const data = await res.json();
            
            if (data.logs) data.logs.forEach(l => appendLog(l));
            if (data.errors) data.errors.forEach(e => appendLog(e, true));
            if (data.success && data.message) appendLog(data.message);
            
            fetchState();
        }

        fetchState();
        appendLog("System Ready. Object is currently ALLOCATED.");
    </script>
</body>
</html>
"""

class DebugRequestHandler(http.server.SimpleHTTPRequestHandler):
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
            self.wfile.write(json.dumps(debug_object.to_dict()).encode('utf-8'))
        else:
            self.send_error(404)

    def do_POST(self):
        global debug_object
        response = {"success": False}
        
        if self.path == '/alloc':
            debug_object.allocate()
            response["success"] = True
            response["message"] = "Object Allocated. Red Zones set. Data clean."
        
        elif self.path == '/free':
            errors = debug_object.validate() # Validate BEFORE free usually
            debug_object.free()
            response["success"] = True
            response["message"] = "Object Freed. Data poisoned (0x6b)."
            response["errors"] = errors  # Report if we freed a corrupted object directly
            
        elif self.path == '/validate':
            errors = debug_object.validate()
            response["success"] = True
            if not errors:
                response["message"] = "Validation Passed: Memory Integrity OK."
            else:
                response["errors"] = errors
        
        elif self.path == '/reset':
            debug_object = DebugObject(0x1000)
            debug_object.allocate()
            response["success"] = True
            response["message"] = "System Reset."

        elif self.path == '/corrupt':
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            debug_object.corrupt(data.get('index'), data.get('value'))
            response["success"] = True

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    print(f"Starting Slub Debug Visualization on port {PORT}...")
    webbrowser.open(f'http://localhost:{PORT}')
    with socketserver.TCPServer(("", PORT), DebugRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
