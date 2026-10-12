@echo off
cd /d "d:\1.1.1python\Conference letter app"
git init
git add -A
git commit -m "first commit"
git branch -M main
git remote remove origin 2>nul
git remote add origin https://github.com/sumaiyamohammadwajeeh2019/Conference-letter-app.git
git push -u origin main
pause
