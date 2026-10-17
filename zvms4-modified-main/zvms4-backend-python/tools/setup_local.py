"""Create missing local development configuration without touching existing keys.

Run from the backend directory with its virtualenv Python. No DB connection is
attempted. Generated secrets and settings are ignored by Git.
"""

from pathlib import Path
import secrets

from Crypto.PublicKey import RSA


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    private_path = root / "rsa_private_key.pem"
    public_path = root / "rsa_public_key.pem"
    if private_path.exists():
        key = RSA.import_key(private_path.read_bytes())
        if not public_path.exists():
            public_path.write_bytes(key.publickey().export_key())
    elif public_path.exists():
        raise SystemExit("Public key exists without private key; restore the matching private key.")
    else:
        key = RSA.generate(2048)
        with private_path.open("xb") as output:
            output.write(key.export_key())
        with public_path.open("xb") as output:
            output.write(key.publickey().export_key())

    secret_path = root / "aes_key.txt"
    if not secret_path.exists():
        with secret_path.open("x", encoding="utf-8") as output:
            output.write(secrets.token_hex(32))

    settings_path = root / "settings.py"
    if not settings_path.exists():
        source = (root / "settings.example.py").read_text(encoding="utf-8")
        source = source.replace('ORACLE_ACTIVE = "prod"', 'ORACLE_ACTIVE = "test"')
        with settings_path.open("x", encoding="utf-8") as output:
            output.write(source)
    print("Local keys/settings ready. Oracle credentials remain unconfigured; offline tests can run.")


if __name__ == "__main__":
    main()
