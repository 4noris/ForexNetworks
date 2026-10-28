# MT5 Brokerage Suite (RMS)

Production-oriented MT5 brokerage reporting, live risk, client analysis, and evidence-based network monitoring.

Configure `MT5_MANAGER_SERVER`, `MT5_MANAGER_LOGIN`, and `MT5_MANAGER_PASSWORD`
in `.env`. Persistent Manager connections are reused by the reporting API.
For the native C++/Go feed, WebSocket delivery, freshness rules and startup,
see [Native MT5 live data](docs/NATIVE_LIVE.md).

The Economic Calendar fetches Forex Factory's current-week JSON feed on the backend
and caches it for 15 minutes. It does not require an MT5 Expert Advisor or a
terminal login. Calendar times display in `RMS_MT5_SERVER_TIMEZONE` (default
`Europe/Athens`, UTC+3 on 14 October 2026). Set this
environment variable to the broker's IANA time zone if its server clock differs.
News Trading compares the feed's UTC event times with trade opening times.

See the [complete MT5 RMS guide](docs/MT5_RMS_COMPLETE_GUIDE.md) for every screen, calculations, evidence rules, workflows, configuration, API routes, request models, database schema, deployment, security, integrations, and troubleshooting. Runtime logs are written to `logs/` and excluded from source control.

Dealing & Risk includes Live Monitor, Exposure, Markout, P&L and Anti-Fraud views.
The historical archive provides authenticated reporting APIs and background jobs;
the retired Analytics page redirects to Risk Center.

## Run from source

Use Windows x64 for the native Manager feed. Install Python 3.11 x64, Node.js,
Go and the MSVC C++ tools. Obtain a licensed MT5 Manager SDK and a compatible
`MT5Manager` Python binding separately; vendor binaries are not redistributed.
See [source distribution and dependencies](docs/SOURCE_DISTRIBUTION.md).

Create `.venv`, install `requirements.txt` and the approved Manager binding, then
copy `.env.example` to a private `.env` and configure the real Manager and session
secrets. Install frontend packages with `npm.cmd ci` inside `frontend` and run
`npm.cmd run build`. From the project root:

```powershell
.\native\build-native.ps1 -SdkPath 'D:\MetaTrader5SDK'
.\.venv\Scripts\python.exe native\start_native.py
```

Keep the SDK runtime directory available through `MT5_SDK_LIB_DIR`. The gateway
serves the application on `http://127.0.0.1:8000`. Broker source synchronization
must complete before live totals are available. Start only one native instance.
The Linux Dockerfile is a reporting deployment template; it does not include
the Windows collector or proprietary Manager dependencies.

## Development verification

```powershell
python -m pytest tests -q
Set-Location frontend
npm.cmd run lint
npm.cmd run build
npm.cmd audit --audit-level=high
```

Never commit real broker, Telegram, encryption, or session secrets. Use a
deployment-only secret store, configure explicit trusted `ALLOWED_ORIGINS`,
and rotate credentials immediately if they are exposed.
