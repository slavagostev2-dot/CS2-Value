@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo CS2 Value is not installed. Run INSTALL.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m cs2_value.map_win_model --db "data\cs2_value.db"
set CODE=%ERRORLEVEL%
echo.
if not "%CODE%"=="0" echo Map Win Model V1 finished with error code %CODE%.
pause
exit /b %CODE%
