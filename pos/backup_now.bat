@echo off
cd /d "%~dp0"
if not exist backups mkdir backups
copy patel_pos.db backups\patel_pos_manual_backup.db
echo Backup created.
pause
