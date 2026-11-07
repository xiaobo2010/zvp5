#!/usr/bin/env bash
set -euo pipefail

umask 077
openssl genrsa -out rsa_private_key.pem 2048 && openssl rsa -in rsa_private_key.pem -pubout -out rsa_public_key.pem
openssl rand -hex 32 > aes_key.txt
chmod 600 rsa_private_key.pem aes_key.txt
chmod 644 rsa_public_key.pem
