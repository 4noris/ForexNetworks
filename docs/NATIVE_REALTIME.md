# Native real-time data path

Run `start-native.cmd` from the RMS root. It loads the existing dotenv settings without placing credentials in arguments, starts the Go gateway on `127.0.0.1:8000`, starts the existing authenticated analytics backend on `127.0.0.1:8001`, and supervises the C++ collector. A Windows job owns the descendants so stopping the gateway also stops its collector and analytics children. Do not run the legacy port-8000 backend at the same time.

The C++ collector uses the locally installed, licensed MT5 Manager SDK. It subscribes to ticks, positions, users, deals, symbol/group changes and account margin/stop-out transitions. There are no order, dealer, balance-adjustment or position-write calls. Native account subscriptions do not supply every equity update, so authoritative account/position reads supplement the callbacks approximately once per second. The collector uses a separate connection and process from historical analytics. SDK callbacks copy their data into a bounded writer queue; overflow causes restart and full resynchronization, never silent loss. Account, price and position observations therefore have different source cadences.

Go holds the native records in memory with an epoch and monotonic sequence. Initial records remain incomplete until the snapshot is verified. Internal snapshot/event/health endpoints accept only loopback requests with a process-private bearer token. Slow consumers disconnect and obtain a new snapshot. The public API and WebSocket gateway forwards to RMS's existing authentication and business routes; Python remains the analytics worker. Live RMS cache population switches to the native feed instead of the Python polling loop. No CSV, sample prices, random records or synthetic equity is used as a fallback.

The existing React UI now includes a typed TypeScript source-status component. It exposes native readiness, source age and collector-to-Go p95 delivery. That latency is a transport-stage measurement, not full broker-to-browser latency. Zero-millisecond samples mean less than the one-millisecond timestamp resolution. Existing JSX screens remain supported; this is not a wholesale frontend or database migration.

The bridge keeps original quote timestamps, preserves broker-reported equity, requires complete account coverage, and rejects invalid metrics, epoch changes and missing sequences. Unavailable native data marks the live source unavailable while it resynchronizes. Native mode checks live-view delivery approximately every 100 ms and coalesces cache projection at approximately 100 ms; actual refresh speed is limited by source cadence, processing time, browser rendering and network conditions. Historical scans retain their own recorded timestamps and completion times.

Build locally with Go and MSVC x64:

```powershell
.\native\build-native.ps1 -SdkPath 'D:\MetaTrader5SDK' -GoPath 'C:\Program Files\Go\bin\go.exe'
```

SDK headers and DLLs are referenced from the installed SDK and are not redistributed. `MT5_SDK_LIB_DIR` can select its library directory. `RMS_ANALYTICS_PORT` can select the internal backend port. Do not expose the internal analytics listener or add the private native bearer token to frontend configuration, URLs, source control or logs. Runtime SDK databases and generated binaries belong outside source control.

Validation includes Go state/authentication/epoch tests, Python projection and source-integrity tests, browser checks, and a read-only shadow connection to the actual configured MT5 server. Unit/browser fixtures are test-only. A successful build or shadow feed does not establish a universal latency guarantee. PostgreSQL migration and any new trade-execution engine are outside this live-ingestion change; current reporting storage and execution rules remain in place.

