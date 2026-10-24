@echo off
chcp 65001 >nul
rem WeAidStock - 클라우드(Cloudflare·Oracle) 관련 파일 삭제. 한 번 실행하면 이 파일도 스스로 지워집니다.
cd /d "%~dp0"
if exist "deploy" rmdir /s /q "deploy"
if exist "docs\cloud-migration.md" del /q "docs\cloud-migration.md"
if exist "backend\app\api\internal.py" del /q "backend\app\api\internal.py"
if exist "backend\app\services\state_snapshot.py" del /q "backend\app\services\state_snapshot.py"
dir /b "docs" 2>nul | findstr . >nul || rmdir "docs" 2>nul
echo 클라우드 관련 파일을 지웠습니다.
timeout /t 3 >nul
(goto) 2>nul & del "%~f0"
