
@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

echo ==============================================
echo   YouTube Agent - Push ALL Repository Changes
echo ==============================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or not on PATH.
    goto :failed
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not inside a Git repository.
    goto :failed
)

for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if /i not "%BRANCH%"=="main" (
    echo ERROR: Current branch is "%BRANCH%", expected "main".
    goto :failed
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo ERROR: Remote "origin" is missing.
    goto :failed
)

echo [1/4] Changes in this repository:
git status --short
echo.
echo WARNING: ALL tracked changes, deletions and untracked files will be included.
echo Files ignored by .gitignore are excluded.
echo Check for passwords, API keys, .env files, and other secrets first.
echo.
choice /c YN /n /m "Stage ALL changes and continue? [Y/N]: "
if errorlevel 2 goto :cancelled

echo.
echo [2/4] Staging all repository changes...
git add -A
if errorlevel 1 goto :failed

git diff --cached --quiet
if not errorlevel 1 (
    echo No changes to commit or push.
    goto :success
)

echo.
echo Files staged for upload:
git diff --cached --name-status
echo.
choice /c YN /n /m "Commit and push the staged files to origin/main? [Y/N]: "
if errorlevel 2 goto :cancelled_staged

echo.
echo [3/4] Creating commit...
git commit -m "Sync all repository changes"
if errorlevel 1 goto :failed

echo.
echo [4/4] Pushing to origin/main...
git push origin main
if errorlevel 1 (
    echo ERROR: Push failed. The local commit is saved; no force push was attempted.
    echo Check the error above before retrying.
    goto :failed
)

echo.
echo SUCCESS: All staged repository changes were pushed to origin/main.
:success
echo.
pause
exit /b 0

:cancelled_staged
echo Cancelled before commit. Files remain staged; nothing was committed or pushed.
goto :failed

:cancelled
echo Cancelled. Nothing was staged, committed or pushed by this script.
goto :failed

:failed
echo.
pause
exit /b 1
