# Source distribution and external dependencies

This repository contains authored RMS Python, React/TypeScript, Go, C++ and MQL5
source, source assets, build configuration and isolated test fixtures. It omits
live databases, broker records, credentials, runtime caches, captured videos,
service-account keys, licenses and generated executables.

Create a deployment-only `.env` from `.env.example`. Keep real Manager credentials,
session/encryption secrets and Google/Telegram credentials outside source control.
The historical archive binds to its configured Manager; never mix another
source's archive into a new deployment.

The native collector requires a separately obtained, licensed MetaTrader 5 Manager
SDK, matching headers and x64 runtime DLLs. They are not redistributed. Set
`MT5_SDK_LIB_DIR` and pass `-SdkPath` to `native/build-native.ps1`. Build on Windows
with MSVC x64 C++ tools and Go as described in `NATIVE_REALTIME.md`. Compile the
optional `.mq5` source using MetaEditor rather than checking in `.ex5` binaries.

Python reporting also imports the separately installed `MT5Manager` binding. The
local compatibility reference was package `mt5manager` version `5.0.6070` on Python
3.11 x64; install a broker/vendor-approved compatible package/runtime in the
deployment environment. `requirements.txt` lists the remaining Python libraries.
Install frontend dependencies from `frontend/package-lock.json` and build them
locally; `node_modules` and generated `dist` assets are excluded.

Private deployment IDs and broker-derived negative-equity regression records
were replaced with clearly synthetic identities and financial values in this
source distribution. Those fixtures are test-only and never live-data fallbacks.
No production database or runtime `.env` was modified during export preparation.


Database reset is disabled by default. Set a strong, deployment-only
`RMS_RESET_PASSWORD` explicitly to enable the administrator-only reset endpoint.
Unset, empty or whitespace-only configuration returns 503 before any mutation.
No built-in reset password is shipped.
