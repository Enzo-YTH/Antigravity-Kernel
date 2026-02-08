# Visual Learning Expert: Linux Kernel Internals

An interactive laboratory of Python-based visualizations designed to demystify complex Linux Kernel concepts. Each script provides a hands-on simulation of a specific kernel subsystem, complete with visual feedback and educational insights.

## 🚀 How to Run
All demos are written in Python 3 and use the standard library (no `pip install` required).

```bash
python3 <script_name>.py
```
Most scripts will launch a local web server (e.g., `http://localhost:8000`) and automatically open your default browser.

---

## 📚 Available Simulations

### 1. Ftrace & Code Patching (`ftrace_visual_demo.py`)
**Visualizes:** Kernel function tracing, dynamic code patching, and the ring buffer.
-   **Core Concepts:** `mcount`, `nop` vs `call` replacement, Ring Buffer, Function Graph Tracer.
-   **Key Features:**
    -   **Live Code Patching**: Watch the kernel "Stop Machine" and hot-patch NOP instructions to CALLs in real-time.
    -   **Function Graph**: Visualizes the call stack (entry/return) with indentation and duration.
    -   **Realistic Scenarios**: Simulates `vfs_read`, `kmalloc`, and scheduler events.
-   **Port**: 8004

### 2. Buddy System Allocator (`buddy_system_demo.py`)
**Visualizes:** Physical memory management using the Buddy Algorithm.
-   **Core Concepts:** Orders (0-10), Block Splitting, Coalescing (Merging), Free Lists.
-   **Key Features:**
    -   **Interactive Allocation**: Request pages of any order (e.g., Order 0 = 4KB, Order 10 = 4MB).
    -   **Visual Splitting**: See a large block break down into smaller buddies to satisfy a request.
    -   **Visual Merging**: Free a page and watch it merge with its buddy to form a larger block up the hierarchy.
-   **Port**: 8003

### 3. Multi-Level Page Table (`multi_level_pt_demo.py`)
**Visualizes:** Virtual to Physical address translation in x86_64 (4-level paging).
-   **Core Concepts:** PGD, P4D, PUD, PMD, PTE, Offset, CR3 Register.
-   **Key Features:**
    -   **Address Walking**: Step-by-step traversal of the 4 levels for a given virtual address.
    -   **Page Faults**: Simulates what happens when a translation is missing.
    -   **TLB Simulation**: Shows how the Translation Lookaside Buffer caches recent translations.
-   **Port**: 8002

### 4. Single-Level Page Table (`page_table_demo.py`)
**Visualizes:** Basic virtual memory concepts (simpler model).
-   **Core Concepts:** VPN (Virtual Page Number), PPN (Physical Page Number), Offset.
-   **Key Features:**
    -   Simplified view of mapping virtual pages to physical frames.
    -   Good starting point before diving into the multi-level complexity.
-   **Port**: 8001

### 5. SLUB Allocator Debugging (`slub_debug_demo.py`)
**Visualizes:** Kernel slab allocator and its debugging features.
-   **Core Concepts:** Slab, Object, Red Zone, Poisoning, Object Tracking.
-   **Key Features:**
    -   **Visual Memory Layout**: See the structure of a Slab object including metadata and payload.
    -   **Corruption Detection**: Simulate writing past the object boundary (Buffer Overflow) and see `SLUB_DEBUG` catch it via Red Zone checks.
    -   **Use-after-Free**: Simulate accessing an object after freeing it (Poisoning check).

---

## 🛠 Project Structure
-   `*_demo.py`: Self-contained Python scripts for each simulation.
-   Each script runs a standalone HTTP server implementation to serve the UI.
-   No external dependencies are required.

## 📝 License
MIT
