@echo off
setlocal
REM 开源数据集图鉴 · 本地服务启动器（隐藏窗口常驻，日志写 logs\atlas.log）
REM 注意：本文件必须保存为 GBK/ANSI 编码，cmd.exe 在中文系统下按 GBK 解析批处理。
set "PORT=8790"
set "PY=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "APP=%~dp0"
set "LOGDIR=%~dp0logs"

if not exist "%PY%" goto :nopy
if not exist "%APP%data\atlas.sqlite" goto :nodb
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 goto :running

powershell -NoProfile -Command "$env:PYTHONIOENCODING='utf-8'; $env:ATLAS_PORT='%PORT%'; $p = Start-Process -FilePath '%PY%' -ArgumentList 'scripts\serve.py' -WorkingDirectory '%APP%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\atlas.log' -RedirectStandardError '%LOGDIR%\atlas.err.log' -PassThru; $p.Id | Set-Content -Encoding ascii '%LOGDIR%\atlas.pid'; Write-Host ('已启动 pid=' + $p.Id)"
echo 本机地址：http://127.0.0.1:%PORT%
echo 局域网地址：http://192.168.1.16:%PORT%  （首次访问若被防火墙拦住，允许 Python 即可）
echo 日志：%LOGDIR%\atlas.log   停止：stop-atlas.cmd
goto :end

:running
echo [SKIP] %PORT% 端口已在监听，直接访问 http://127.0.0.1:%PORT%
goto :end

:nopy
echo [ERR] 找不到 Python 运行时：%PY%
goto :end

:nodb
echo [ERR] 缺少 data\atlas.sqlite，请先运行 scripts\fetch_data.py 和 scripts\build_db.py
goto :end

:end
endlocal
