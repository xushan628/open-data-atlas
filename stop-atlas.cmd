@echo off
setlocal enabledelayedexpansion
REM 开源数据集图鉴 · 停止本地服务（按端口找进程，兜底用 pid 文件）
REM 注意：本文件必须保存为 GBK/ANSI 编码。
set "PORT=8790"
set "PIDFILE=%~dp0logs\atlas.pid"
set FOUND=0

for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique"`) do (
  set FOUND=1
  echo 结束进程 %%P
  taskkill /PID %%P /F >nul 2>&1
)
if exist "%PIDFILE%" (
  for /f "usebackq tokens=*" %%P in ("%PIDFILE%") do taskkill /PID %%P /F >nul 2>&1
  del "%PIDFILE%" >nul 2>&1
)
if "%FOUND%"=="0" echo [INFO] %PORT% 端口没有监听进程，图鉴未在运行
echo 已停止。
endlocal
