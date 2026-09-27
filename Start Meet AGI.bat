@echo off
rem WHY: double-click this file to start Meet AGI (backend, public tunnel, dashboard) and open
rem http://localhost:3000. The dashboard only exists while this window is open.
rem To stop: press Ctrl+C in this window - that ends any open meeting (the bot leaves, the summary
rem is written) before shutting down. Closing the window with X skips that step.
cd /d "%~dp0"
title Meet AGI - keep this window open, press Ctrl+C to stop
python scripts\go.py
echo.
echo Meet AGI has stopped. Press any key to close this window.
pause >nul
