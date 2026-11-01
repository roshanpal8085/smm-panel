@echo off
echo Starting Yoomo Server...
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe run.py --reload
) else (
    python run.py --reload
)
pause
