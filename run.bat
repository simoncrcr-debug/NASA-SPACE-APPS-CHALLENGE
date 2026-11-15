@echo off
setlocal
cd /d "%~dp0"
rem Windows downloads trusted root certificates on demand, but only for its own TLS stack.
rem Python reads that same store, so touch IRSA and SIMBAD once through Windows first;
rem otherwise the first searches can fail with CERTIFICATE_VERIFY_FAILED on a fresh PC.
powershell -NoProfile -Command "foreach ($u in 'https://irsa.ipac.caltech.edu/','https://simbad.cds.unistra.fr/') { try { Invoke-WebRequest $u -UseBasicParsing -Method Head -TimeoutSec 15 | Out-Null } catch {} }" >nul 2>nul
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo No se encontro Python 3. Instalalo desde https://www.python.org/downloads/ y vuelve a ejecutar run.bat.
  ) else (
    python server.py
  )
)
echo.
echo Cielo Claro se cerro. Puedes cerrar esta ventana.
pause
