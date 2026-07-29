@echo off
REM One-click: apply the Adaptive Loop Vercel embed patch to DGator86/0DTE.
REM cursor[bot] cannot push to 0DTE (GitHub 403). Run this as the repo owner.
REM
REM Prereq: SPY-DER checkout with integrations/zerodte/ready/0001-embed-*.patch
REM and a local clone of DGator86/0DTE with push access.

setlocal
set SPYDER=%~dp0..\..
set PATCH=%~dp0ready\0001-embed-spy-der-adaptive-loop-on-vercel.patch
if not exist "%PATCH%" (
  echo Missing %PATCH%
  exit /b 1
)

if "%0DTE%"=="" (
  echo Set 0DTE=C:\path\to\0DTE then re-run.
  exit /b 1
)

pushd "%0DTE%"
git checkout main
git pull
git checkout -b cursor/adaptive-loop-embed-f51d
git am "%PATCH%"
git push -u origin HEAD
echo.
echo Open a PR on DGator86/0DTE and merge. Vercel redeploys from main;
echo the VPS zerodte-update.timer picks up the proxy within ~2 minutes.
echo.
echo Also set SPY_DER_OPERATOR_TOKEN in /etc/spy-der/spy-der.env and restart
echo spy-der-dashboard-api so Promote/Reject unlock on the Adaptive Loop tab.
popd
