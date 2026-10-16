# Kernel benchmark harness (ADR-0001)

Runs identical raw OCCT operations in the browser (opencascade.js WASM) and on the server (OCP).

Server:

    cd services/api && python ../../tools/kernel-bench/server_raw_bench.py

Browser (downloads ~63 MB opencascade.js into a scratch directory; keep it out of the repository):

    mkdir -p /tmp/ocjs && cd /tmp/ocjs && npm install --ignore-scripts opencascade.js@1.1.1
    mkdir -p bench && cp <repo>/tools/kernel-bench/browser-bench.html bench/
    python3 -m http.server 8765 --bind 127.0.0.1 &
    cd <repo>/apps/web && node ../../tools/kernel-bench/run-browser-bench.mjs

Full parametric pipeline benchmark (feature engine, naming, cache, tessellation):

    cd services/api && python -m autoforge.bench
