#!/usr/bin/env bash
set -euo pipefail

sudo apt update && sudo apt upgrade -y
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.13 python3.13-venv supervisor nginx -y
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo cp config/supervisor.conf /etc/supervisor/conf.d/zvms.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start zvms
sudo supervisorctl status
sudo cp config/nginx/nginx /etc/nginx/sites-available/zvms
sudo ln -sfn /etc/nginx/sites-available/zvms /etc/nginx/sites-enabled/zvms
sudo nginx -t
sudo systemctl restart nginx
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d api.zvms.site
sudo certbot renew --dry-run
sudo tail -f /var/log/zvms.out.log
sudo tail -f /var/log/nginx/zvms.log
