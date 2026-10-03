import secrets

print("SECRET_KEY=" + secrets.token_urlsafe(48))
print("CRON_TOKEN=" + secrets.token_urlsafe(48))
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(24).replace("-", "").replace("_", ""))
