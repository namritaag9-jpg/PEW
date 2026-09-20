@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && set PY=py || set PY=python
%PY% -m pip install -r requirements.txt
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "(Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up'} | Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress"`) do set "IP=%%A"
echo.
echo ================================================
echo PATEL ELECTRICALS WORKSHOP POS
 echo Computer URL: http://127.0.0.1:5000
if defined IP echo Mobile URL:   http://%IP%:5000
 echo Keep this window open while using the POS.
echo ================================================
echo.
%PY% app.py
pause
