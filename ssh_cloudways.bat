@echo off
echo ============================================================
echo  Connecting to Ganjier Guild Cloudways Server
echo ============================================================
echo  Host: ssh.app22846.cloudwayssites.com
echo  Port: 2311
echo  User: admin
echo.
echo  Enter your Cloudways Master Credentials password when prompted.
echo  (Get it from: platform.cloudways.com → Servers → Master Credentials)
echo ============================================================
echo.
ssh -p 2311 admin@ssh.app22846.cloudwayssites.com
echo.
echo Connection closed.
pause
