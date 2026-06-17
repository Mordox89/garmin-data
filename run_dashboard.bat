@echo off
echo ================================
echo  Marathon Dashboard Sync
echo ================================
cd /d "%~dp0"

echo.
echo [1/3] Garmin data ophalen...
python garmin_fetch.py
if errorlevel 1 (
    echo FOUT: garmin_fetch.py mislukt
    pause
    exit /b 1
)

echo.
echo [2/3] AI coach rapport genereren...
python build_coach_report.py
if errorlevel 1 (
    echo WAARSCHUWING: coach rapport mislukt (dashboard werkt nog wel)
)

echo.
echo [3/3] Pushen naar GitHub...
git add -A
git commit -m "Manual sync"
git pull --rebase -X theirs
git push

echo.
echo ================================
echo  Klaar! Dashboard bijgewerkt.
echo ================================
pause
