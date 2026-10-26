@echo off
echo 正在自动检测所有更新...
git add -A
git commit -m "Auto update: %date% %time%"
git push
echo ========================
echo 所有更新已成功推送到 GitHub！
echo ========================
pause