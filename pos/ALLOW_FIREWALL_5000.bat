@echo off
netsh advfirewall firewall add rule name="Patel POS Flask 5000" dir=in action=allow protocol=TCP localport=5000 profile=private
if %errorlevel%==0 (
  echo Firewall rule added for Private networks on TCP port 5000.
) else (
  echo Could not add rule. Right-click this file and choose Run as administrator.
)
pause
