@echo off
setlocal
REM One-click: apply SPY-DER parallel panel to 0DTE and open a PR.
REM Requires: git + gh (GitHub CLI) logged in as a user with push to DGator86/0DTE.

where git >nul 2>&1 || (echo Install git first & exit /b 1)
where gh >nul 2>&1 || (echo Install GitHub CLI: https://cli.github.com/ & exit /b 1)

set WORK=%TEMP%\0dte-spy-der-panel
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
cd /d "%WORK%"

echo Cloning 0DTE...
gh repo clone DGator86/0DTE repo -- --depth 50
cd repo
git checkout -b cursor/spy-der-parallel-panel

echo Downloading patch from SPY-DER main...
curl.exe -fsSL -o spyder.patch https://raw.githubusercontent.com/DGator86/SPY-DER/main/integrations/zerodte/0dte-spy-der-parallel-panel.patch
if errorlevel 1 (
  echo curl failed - trying gh api...
  gh api repos/DGator86/SPY-DER/contents/integrations/zerodte/0dte-spy-der-parallel-panel.patch --jq .content > spyder.b64
  powershell -NoProfile -Command "[IO.File]::WriteAllBytes('spyder.patch',[Convert]::FromBase64String((Get-Content -Raw spyder.b64) -replace '\s',''))"
)

echo Applying patch...
git am spyder.patch
if errorlevel 1 (
  echo git am failed. Aborting.
  git am --abort
  exit /b 1
)

echo Pushing branch...
git push -u origin HEAD
if errorlevel 1 (
  echo Push failed. Make sure you are logged in: gh auth login
  exit /b 1
)

echo Opening PR...
gh pr create -R DGator86/0DTE --base main --head cursor/spy-der-parallel-panel --title "Add SPY-DER as fourth parallel paper/dashboard track" --body "Wires SPY-DER into PAPER_TRACKS, live_state forecast.parallel_tracks, VPS auto-deploy of /opt/spy-der, and the Parallel decisions panel (Legacy / V2 / V3 / SPY-DER). XAI_API_KEY is already on the VPS; after merge Vercel + VPS self-update should show the fourth track."
echo Done. Merge the PR, then wait ~2 minutes for VPS/Vercel.
