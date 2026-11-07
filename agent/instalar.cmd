@echo off
rem Instalador do Cabine RFID Agent. Dê dois cliques (pede permissão de Administrador).
rem Coloque este arquivo na mesma pasta do cabine-agent.exe.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Solicitando permissao de Administrador...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
set SERVER=https://cabine.solidsd.com.br
set /p SERVER=URL do Cabine [%SERVER%]: 
echo Se o agente ja esta instalado, deixe o token em branco (ENTER) para manter o atual.
set /p TOKEN=Token do agente (painel Cabine ^> Antenas ^> Agentes): 
if "%TOKEN%"=="" (
  cabine-agent.exe install --server "%SERVER%"
) else (
  cabine-agent.exe install --server "%SERVER%" --token "%TOKEN%"
)
echo.
echo Para ver o estado depois:  cabine-agent.exe status   (ou use o icone na bandeja)
pause
