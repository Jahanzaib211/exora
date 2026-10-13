// pm2 process file: keep Exora running on a laptop or a small server (Windows, Linux, macOS).
//
//   npm install -g pm2
//   pm2 start ecosystem.config.cjs      # start (after scripts/setup)
//   pm2 save                            # remember it; `pm2 startup` on Linux to start on boot
//   pm2 logs exora                      # tail both streams
//
// Notes:
//  - `cwd` is this folder: the app resolves `.env`, `data/` and `web/dist` relative to it.
//  - The venv's own `prospector` executable is started directly, so no `interpreter` is needed.
//  - Windows: call `pm2.cmd`, not `pm2.ps1`, if PowerShell's execution policy blocks scripts.

const path = require("path");

const exe =
  process.platform === "win32"
    ? path.join(__dirname, ".venv", "Scripts", "prospector.exe")
    : path.join(__dirname, ".venv", "bin", "prospector");

module.exports = {
  apps: [
    {
      name: "exora",
      cwd: __dirname,
      script: exe,
      args: "serve --host 127.0.0.1 --port 8090",
      interpreter: "none",
      instances: 1,
      autorestart: true,
      // Exponential backoff instead of a tight crash loop: if the app exits on purpose
      // (e.g. refuses to bind), pm2 must not hammer it hundreds of times a minute.
      exp_backoff_restart_delay: 100,
      min_uptime: "10s",
      max_restarts: 20,
      // The engine holds in-memory worker/scheduler state; recycle it before it grows unbounded.
      max_memory_restart: "3G",
      out_file: "data/logs/pm2-exora-out.log",
      error_file: "data/logs/pm2-exora-error.log",
      merge_logs: true,
      time: true,
      env: {
        // Everything runs in this one process (see .env for overrides).
        PROSPECTOR_WORKER_ENABLED: "true",
        PROSPECTOR_SCHEDULER_ENABLED: "true",
      },
    },
  ],
};
