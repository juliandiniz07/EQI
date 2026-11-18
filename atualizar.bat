@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py atualizar.py) || (python atualizar.py)
echo.
pause
